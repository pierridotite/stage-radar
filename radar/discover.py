"""Détecteur d'ATS : trouve le flux d'offres public d'une entreprise à partir de sa page carrières.

    python -m radar.discover liste.csv > trouvees.yaml

liste.csv (séparateur ,) : name,sector,size,url   (url = page carrières ou site de l'entreprise ; peut être vide)
Pour chaque ligne : lit la page, cherche la signature d'un ATS connu, sinon essaie des identifiants
devinés à partir du nom ; ne garde que les flux qui répondent vraiment. Sortie : entrées YAML prêtes
à coller dans config/companies.yaml (avec en commentaire le nombre d'offres et de stages vus).
"""
from __future__ import annotations

import csv
import re
import sys
from concurrent.futures import ThreadPoolExecutor

from .http import PoliteSession
from .sources.ats import FETCHERS
from .text import slug

SIGNATURES = [
    ("workday", re.compile(r"https?://([\w-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([\w-]+)")),
    ("smartrecruiters", re.compile(r"(?:jobs|careers)\.smartrecruiters\.com/([\w-]+)")),
    ("smartrecruiters", re.compile(r"api\.smartrecruiters\.com/v1/companies/([\w-]+)")),
    ("lever", re.compile(r"jobs\.(eu\.)?lever\.co/([\w.-]+)")),
    ("greenhouse", re.compile(r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board(?:/js)?\?for=)?([\w-]+)")),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([\w.%-]+)")),
    ("workable", re.compile(r"apply\.workable\.com/([\w-]+)")),
    ("recruitee", re.compile(r"([\w-]+)\.recruitee\.com")),
    ("teamtailor", re.compile(r"([\w-]+)\.teamtailor\.com")),
    ("personio", re.compile(r"([\w-]+)\.jobs\.personio\.(?:de|com)")),
    ("breezy", re.compile(r"([\w-]+)\.breezy\.hr")),
    ("wttj", re.compile(r"welcometothejungle\.com/(?:fr|en)/companies/([\w-]+)")),
]
IGNORED_IDS = {"www", "api", "app", "assets", "cdn", "static", "embed", "jobs", "careers", "boards", "static-assets"}


def _count(ats: str, cfg: dict, s) -> tuple[int, int] | None:
    """(nb d'offres totales, nb de stages) si le flux répond, sinon None."""
    try:
        if ats == "smartrecruiters":
            r = s.get(f"https://api.smartrecruiters.com/v1/companies/{cfg['id']}/postings", params={"limit": 1})
            total = r.json().get("totalFound", 0) if r.ok else 0
            return (total, len(FETCHERS[ats](cfg, s))) if total else None
        if ats == "lever":
            host = "api.eu.lever.co" if cfg.get("region") == "eu" else "api.lever.co"
            r = s.get(f"https://{host}/v0/postings/{cfg['id']}", params={"mode": "json"})
            return (len(r.json()), len(FETCHERS[ats](cfg, s))) if r.ok and r.json() else None
        if ats == "greenhouse":
            r = s.get(f"https://boards-api.greenhouse.io/v1/boards/{cfg['id']}/jobs")
            return (len(r.json().get("jobs", [])), len(FETCHERS[ats](cfg, s))) if r.ok else None
        if ats == "workday":
            tenant = cfg["host"].split(".")[0]
            r = s.post(f"https://{cfg['host']}/wday/cxs/{tenant}/{cfg['site']}/jobs",
                       json={"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""})
            return (r.json().get("total", 0), len(FETCHERS[ats](cfg, s))) if r.ok else None
        if ats == "wttj":
            return (-1, -1)  # repéré mais non collecté (CGU) : noté pour information
        interns = FETCHERS[ats](cfg, s)
        return (-1, len(interns))
    except Exception:
        return None


def _guesses(name: str) -> list[str]:
    base = slug(name)
    # pas de devinette sur le seul premier mot : trop d'homonymes (Elicit, Evolution, KWS...)
    return list(dict.fromkeys([base.replace("-", ""), base]))


def detect(row: dict) -> dict | None:
    s = PoliteSession(delay=0.2, timeout=10, retries=0)
    s.headers["Accept"] = "text/html,application/json,*/*"
    name, url = row["name"].strip(), (row.get("url") or "").strip()
    candidates: list[tuple[str, dict]] = []

    if url:
        try:
            page = s.get(url).text
            page += " " + " ".join(re.findall(r'href="([^"]+)"', page))
        except Exception:
            page = ""
        page = f"{url} {page}"  # l'URL donnée peut être elle-même celle de l'ATS (ex. *.myworkdayjobs.com/Site)
        for ats, rx in SIGNATURES:
            for m in rx.finditer(page):
                if ats == "workday":
                    candidates.append((ats, {"host": f"{m[1]}.{m[2]}.myworkdayjobs.com", "site": m[3]}))
                elif ats == "lever":
                    candidates.append((ats, {"id": m[2], **({"region": "eu"} if m[1] else {})}))
                elif m[1].lower() not in IGNORED_IDS:
                    candidates.append((ats, {"id": m[1]}))
        if "teamtailor" in page and not any(a == "teamtailor" for a, _ in candidates):
            candidates.append(("teamtailor", {"url": url.rstrip("/") + "/jobs.rss"}))

    if not candidates:  # identifiants devinés
        for g in _guesses(name):
            for ats in ("smartrecruiters", "lever", "greenhouse", "ashby", "recruitee", "workable", "teamtailor",
                        "personio", "breezy"):
                candidates.append((ats, {"id": g}))

    seen = set()
    for ats, extra in candidates:
        key = (ats, tuple(sorted(extra.items())))
        if key in seen:
            continue
        seen.add(key)
        cfg = {"name": name, "sector": row.get("sector", ""), **extra}
        res = _count(ats, cfg, s)
        if res:
            return {"name": name, "sector": row.get("sector", ""), "size": row.get("size", ""), "ats": ats,
                    **extra, "_total": res[0], "_interns": res[1]}
    return None


def to_yaml(e: dict) -> str:
    fields = {k: v for k, v in e.items() if not k.startswith("_") and v != ""}
    body = ", ".join(f"{k}: {v!r}" if isinstance(v, str) and (":" in v or " " in v or "'" in v) else f"{k}: {v}"
                     for k, v in fields.items())
    note = "  # non collecté (WTTJ)" if e["ats"] == "wttj" else f"  # offres: {e['_total']}, stages: {e['_interns']}"
    return f"  - {{{body}}}{note}"


def main(argv=None):
    path = (argv or sys.argv[1:])[0]
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = [r for r in csv.DictReader(f) if r.get("name")]
    missing = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for row, found in zip(rows, pool.map(detect, rows)):
            if found:
                print(to_yaml(found), flush=True)
            else:
                missing.append(row["name"])
    if missing:
        print("# introuvables : " + ", ".join(missing))


if __name__ == "__main__":
    main()
