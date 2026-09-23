"""Taille des entreprises (startup / PME / ETI / grand groupe) via l'annuaire officiel des entreprises.

API publique et gratuite de l'État : https://recherche-entreprises.api.gouv.fr (sans clé, 7 requêtes/s).
Elle renvoie la catégorie INSEE (PME, ETI, GE), la tranche d'effectif, la date de création et le code NAF.
Les résultats sont mis en cache dans la base : chaque entreprise n'est cherchée qu'une fois par trimestre.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from .http import PoliteSession
from .text import fold, slug

log = logging.getLogger(__name__)

API = "https://recherche-entreprises.api.gouv.fr/search"
# Activités typiques des jeunes pousses : édition de logiciels, conseil informatique, traitement de données, R&D
STARTUP_NAF = ("58.2", "62.", "63.1", "72.", "71.12B", "74.90B")
SIZES = ("startup", "pme", "eti", "grande", "public")

SCHEMA = """CREATE TABLE IF NOT EXISTS company_size (
    name_key TEXT PRIMARY KEY, size TEXT, legal_name TEXT, category TEXT, created TEXT, naf TEXT, checked_on TEXT)"""


def classify(category: str, created: str, naf: str, today: date, nature: str = "") -> str:
    if nature.startswith("7"):  # personne morale de droit public : ministère, EPST (INRAE, CNRS), collectivité
        return "public"
    if category == "GE":
        return "grande"
    if category == "ETI":
        return "eti"
    try:
        age = today.year - int(created[:4])
    except (TypeError, ValueError):
        age = 99
    if age <= 5 or (age <= 12 and (naf or "").startswith(STARTUP_NAF)):
        return "startup"
    return "pme"


def _same_company(query: str, legal_name: str) -> bool:
    """Garde-fou contre les homonymes : chaque mot significatif du nom cherché doit figurer dans la raison sociale."""
    words = [w for w in slug(query).split("-") if len(w) > 2 and w not in {"groupe", "group", "france", "sas"}]
    legal = fold(legal_name)
    return bool(words) and all(w in legal for w in words)


def lookup(name: str, s: PoliteSession, today: date) -> dict | None:
    query = name.split(" · ")[0]
    r = s.get(API, params={"q": query, "per_page": 3, "etat_administratif": "A"})
    if r.status_code != 200:
        return None
    for res in r.json().get("results", []):
        legal = res.get("nom_complet") or ""
        if _same_company(query, legal):
            cat, created, naf = res.get("categorie_entreprise") or "", res.get("date_creation") or "", \
                res.get("activite_principale") or ""
            nature = str(res.get("nature_juridique") or "")
            return {"size": classify(cat, created, naf, today, nature), "legal_name": legal, "category": cat,
                    "created": created, "naf": naf}
    return None


def enrich(db, offers, today: date, max_lookups: int = 400) -> None:
    """Complète `offer.size` quand la config ne le donne pas (offres Adzuna, manuelles, entreprises non taillées)."""
    db.execute(SCHEMA)
    stale = (today - timedelta(days=90)).isoformat()
    todo = {o.company for o in offers if not o.size and o.company and o.company != "?"}
    cache = {}
    for name in todo:
        row = db.execute("SELECT size, checked_on FROM company_size WHERE name_key=?", (slug(name),)).fetchone()
        if row and row[1] >= stale:
            cache[name] = row[0]
    missing = [n for n in todo if n not in cache][:max_lookups]
    if missing:
        s = PoliteSession(delay=0.2, timeout=10, retries=2)
        for name in missing:
            try:
                info = lookup(name, s, today) or {}
            except Exception as e:
                log.debug("annuaire %s : %s", name, e)
                continue
            cache[name] = info.get("size", "")
            db.execute("INSERT OR REPLACE INTO company_size VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (slug(name), cache[name], info.get("legal_name", ""), info.get("category", ""),
                        info.get("created", ""), info.get("naf", ""), today.isoformat()))
        db.commit()
        log.info("annuaire des entreprises : %d recherches, %d tailles trouvées",
                 len(missing), sum(1 for n in missing if cache.get(n)))
    for o in offers:
        if not o.size:
            o.size = cache.get(o.company, "")
