"""Localisation des offres sur la carte : ville -> coordonnées, via le service de géocodage de l'IGN (Géoplateforme,
gratuit, sans clé). Une requête par lieu distinct, gardée en cache dans la base (table geocode) : seuls les lieux
nouveaux sont interrogés d'un jour à l'autre.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date
from pathlib import Path

from .filters import REGIONS
from .text import fold

log = logging.getLogger(__name__)

URL = "https://data.geopf.fr/geocodage/search"
SCHEMA = """CREATE TABLE IF NOT EXISTS geocode (
    q TEXT PRIMARY KEY, city TEXT, lon REAL, lat REAL, region TEXT, checked_on TEXT)"""
MAP = json.loads((Path(__file__).resolve().parent / "assets" / "france-regions.json").read_text(encoding="utf-8"))

_REGION_NAMES = {fold(r) for r in REGIONS} | {"ile de france", "idf", "paca"}
_NOISE = re.compile(r"\b(france|fr|fra|remote|hybride?|teletravail|office|cedex|arrondissement|\d+(er|e|eme)|"
                    r"metropole|metropolitan|region|area|greater|headquarters|siege|campus)\b")
# lieux courants qui ne sont pas des communes
_ALIASES = {"la defense": "puteaux", "paris la defense": "puteaux", "sophia antipolis": "valbonne",
            "saclay plateau": "saclay", "plateau de saclay": "saclay", "marne la vallee": "champs-sur-marne"}
# villes connues (listes des régions) : dernier recours quand aucun morceau du lieu n'est une commune
_KNOWN = re.compile(r"(?<![a-z])(" + "|".join(sorted({w.rstrip("*") for _, words in REGIONS.values() for w in words
                                                      if not w.endswith("*") and fold(w) not in {fold(r) for r in REGIONS}
                                                      and len(w) > 3}, key=len, reverse=True)) + r")(?![a-z])")
_STREET = re.compile(r"\b(rue|avenue|av|bd|boulevard|allee|chemin|route|place|quai|impasse|zi|za|zac|parc|bat|batiment)\b")


def candidates(location: str) -> tuple[str, list[str]]:
    """Code postal et noms de ville possibles, du plus au moins probable, dans un lieu brut d'offre
    (« 91300, MASSY, 91 », « FRA-Bas-Rhin-Haguenau », « 11ème Arrondissement, Paris », « PARIS 08 »...)."""
    loc = fold(location)
    postcode = (re.search(r"(?<!\d)(\d{5})(?!\d)", loc) or [None, ""])[1]
    parts = [p for chunk in re.split(r"[,;/|()]|\s+-\s*|\s*-\s+", loc) for p in [chunk.strip()] if p]
    if len(parts) == 1 and parts[0].count("-") >= 2 and parts[0].startswith(("fra-", "fr-")):
        parts = [parts[0].split("-")[-1]]                    # « FRA-Bas-Rhin-Haguenau » -> « haguenau »
    out = []
    for p in parts:
        # rue, code interne (« FR_REN_RSAS »), région, ou département (« 94 Val-de-Marne ») : pas une ville
        if _STREET.search(p) or "_" in p or p in _REGION_NAMES or re.fullmatch(r"\d{2,3}\s+[a-z' -]+", p):
            continue
        p = re.sub(r"\d+", " ", _NOISE.sub(" ", p))
        p = re.sub(r"\s+", " ", p).strip(" -.")
        p = _ALIASES.get(p, p)
        if len(p) >= 2 and p not in _REGION_NAMES and not p.isdigit() and p not in out:
            out.append(p)
    for m in _KNOWN.finditer(loc):
        if m[1] not in out:
            out.append(m[1])
    return postcode, out[:4]


def project(lon: float, lat: float) -> list[int]:
    p = MAP["proj"]
    return [round((lon - p["lon_min"]) * p["cos"] * p["scale"] + p["pad"]),
            round((p["lat_max"] - lat) * p["scale"] + p["pad"])]


def _search(s, city: str, postcode: str) -> dict | None:
    params = {"q": city, "type": "municipality", "limit": 1, "index": "address"}
    if postcode:
        params["postcode"] = postcode
    r = s.get(URL, params=params)
    if r.status_code != 200:
        return None
    feats = r.json().get("features") or []
    if not feats and postcode:                  # code CEDEX (« 31326 ») : pas un code postal de commune
        return _search(s, city, "")
    return feats[0] if feats else None


def locate(db, s, items: list[dict], today: date, max_lookups: int = 500) -> None:
    """Ajoute à chaque offre sa ville et sa position sur la carte ("city", "xy") ; sa région devient celle de la ville."""
    db.execute(SCHEMA)
    cache = {r[0]: r for r in db.execute("SELECT q, city, lon, lat, region FROM geocode")}
    lookups = 0
    for it in items:
        key = fold(it["location"]).strip()
        if not key:
            continue
        if key not in cache and lookups < max_lookups:
            postcode, names = candidates(it["location"])
            found = None
            for name in names:
                lookups += 1
                f = _search(s, name, postcode)
                if not f:
                    continue
                props = f["properties"]
                region = (props.get("context") or "").split(", ")[-1]
                # nom exact : accepté (et sa région fait foi) ; sinon, score suffisant et région cohérente
                exact = fold(props.get("city") or props.get("name")).replace("-", " ") == name.replace("-", " ")
                if exact or (props.get("score", 0) >= 0.5 and (not it["region"] or it["region"] == region)):
                    found = (props.get("city") or props.get("name"), *f["geometry"]["coordinates"], region)
                    break
            row = (key, *(found or ("", None, None, "")))
            db.execute("INSERT OR REPLACE INTO geocode VALUES (?, ?, ?, ?, ?, ?)", (*row, today.isoformat()))
            cache[key] = row
        row = cache.get(key)
        if row and row[1]:
            it["city"], it["xy"] = row[1], project(row[2], row[3])
            if row[4] in REGIONS:          # région du géocodage, plus sûre que les mots du lieu brut
                it["region"] = row[4]
    db.commit()
    if lookups:
        log.info("Géocodage : %d requêtes, %d offres placées sur la carte", lookups, sum("xy" in i for i in items))
