"""Collecte quotidienne : python -m radar [--only NOM] [--no-fetch]

1. interroge chaque entreprise de config/companies.yaml (+ Adzuna + offres manuelles)
2. garde les stages, les enregistre dans data/radar.db (nouvelles / disparues)
3. score les offres actives et écrit docs/ : le tableau de bord, data/offers.json et data/offres_du_jour.csv
"""
from __future__ import annotations

import argparse
import base64
import csv
import logging
import os
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path

import yaml

from .company_size import enrich
from .filters import classify_sector, in_france, prepare, region
from .fit import FitLexicon
from .llm import LLM
from .http import PoliteSession
from .scoring import Scorer
from .sources.aggregators import adzuna, manual
from .sources import boards
from .sources.ats import FETCHERS
from .store import Store, dump_json, offer_from_row
from .text import fold

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger("radar")


def load(name: str) -> dict:
    return yaml.safe_load((ROOT / "config" / name).read_text(encoding="utf-8"))


def fetch_company(cfg: dict):
    """Chaque entreprise a sa propre session : les pauses sont par hôte, et une panne reste isolée."""
    try:
        offers = FETCHERS[cfg["ats"]](cfg, PoliteSession(budget=240))
        for o in offers:
            o.size = cfg.get("size", "")
        return cfg, offers, ""
    except Exception as e:
        return cfg, [], f"{type(e).__name__}: {e}"[:300]


def collect(store: Store, today: date, only: str | None) -> None:
    companies = [c for c in load("companies.yaml")["companies"]
                 if c.get("enabled", True) and c.get("ats") in FETCHERS
                 and (not only or only.lower() in c["name"].lower())]
    log.info("%d entreprises à interroger", len(companies))
    offers = []
    with ThreadPoolExecutor(max_workers=int(os.getenv("RADAR_WORKERS", "6"))) as pool:
        for cfg, found, err in pool.map(fetch_company, companies):
            store.log_run(today, cfg["ats"], cfg["name"], len(found), err)
            log.info("%-28s %-15s %3d stage(s) %s", cfg["name"][:28], cfg["ats"], len(found), err and "ERREUR " + err)
            offers += found

    if not only:
        sources = load("sources.yaml")
        for b in sources.get("boards", []):
            if not b.get("enabled", True):
                continue
            try:
                found = getattr(boards, b["fetch"])(b, PoliteSession(delay=0.8, budget=300))
                for o in found:
                    o.size = o.size or b.get("size", "")
                err = ""
            except Exception as e:
                found, err = [], f"{type(e).__name__}: {e}"[:300]
            store.log_run(today, "jobboard", b["name"], len(found), err)
            log.info("%-28s %-15s %3d stage(s) %s", b["name"][:28], "jobboard", len(found), err and "ERREUR " + err)
            offers += found
        s = PoliteSession(delay=1.0)
        extra = adzuna(sources.get("adzuna_queries", []), s)
        store.log_run(today, "adzuna", "*", len(extra))
        extra += manual(ROOT / "data" / "manual_offers.csv")
        offers += extra

    before = len(offers)
    offers = [o for o in offers if in_france(o)]
    log.info("%d stages hors de France écartés", before - len(offers))
    for o in offers:
        o.sector = o.sector or classify_sector(o.company, o.title, o.description)

    unique = {}
    for o in offers:  # les flux entreprises passent avant les agrégateurs : ils ont la description complète
        unique.setdefault(o.key, o)
    enrich(store.db, list(unique.values()), today)
    new = store.upsert(list(unique.values()), today)
    gone = store.expire(today)
    log.info("%d stages collectés, %d nouveaux, %d retirés", len(unique), new, gone)


def ai_fit(ai: dict, sector: str) -> dict:
    """Caractéristiques de l'offre pour le fit CV, d'après la fiche IA (mêmes identifiants que config/fit.yaml)."""
    from .fit import SECTOR_DOMAINS
    domains = sorted({ai["domain"], *SECTOR_DOMAINS.get(sector, [])} - {"other"})
    return {"skills": ai["required_skills"], "nice": ai["nice_to_have_skills"], "domains": domains,
            "roles": [] if ai["role"] == "other" else [ai["role"]], "langs": ai["languages_required"]}


def export(store: Store, today: date, use_llm: bool = True) -> None:
    scoring_cfg = load("scoring.yaml")
    scorer = Scorer(scoring_cfg, today)
    lexicon = FitLexicon(load("fit.yaml"))
    llm_cfg = load("llm.yaml")
    rows = [(row, offer_from_row(row)) for row in store.active()]
    rows = [(row, o) for row, o in rows if in_france(o)]

    # Analyse IA des offres plausibles (pré-filtre par les règles pour limiter le coût), avec cache en base
    ai = {}
    if use_llm:
        llm = LLM(llm_cfg, load("fit.yaml"), scoring_cfg["school"])
        threshold = llm_cfg["offers"]["prefilter_min_data"]
        candidates = [o for _, o in rows if scorer.data_axis(prepare(o.title), prepare(o.description))[0] >= threshold]
        ai = llm.analyze_offers(store.db, candidates)

    items = []
    for row, o in rows:
        a = ai.get(o.key)
        sc = scorer.score(o, a)
        if sc is None:
            continue
        title, text = sc.pop("_title"), sc.pop("_text")
        fit = ai_fit(a, o.sector) if a else lexicon.offer_features(title, text, o.sector)
        items.append({
            "id": row["key"], "company": o.company, "title": o.title, "url": o.url, "location": o.location,
            "sector": o.sector, "size": o.size, "region": region(o.location), "source": o.source, "posted_at": o.posted_at[:10],
            "first_seen": row["first_seen"], "new": row["first_seen"] == today.isoformat(),
            "excerpt": o.description[:600],
            # texte de recherche du tableau de bord : annonce complète, repliée (minuscules, sans accents)
            "text": fold(o.description)[:6000], "fit": fit, **sc,
            **({"ai": {"summary": a["summary"], "other_skills": a["other_skills"][:5], "degree": a["degree_target"],
                       "entity": a.get("entity", ""), "team": a.get("team", "")}}
               if a else {}),
        })
    order = {"A": 0, "B": 1, "C": 2, "D": 3, "X": 4}
    items.sort(key=lambda x: (order[x["grade"]], -x["score"], -x["data"]))

    runs = store.db.execute("SELECT source, company, found, error FROM runs WHERE day=?", (today.isoformat(),))
    runs = [dict(r) for r in runs]
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "school": load("scoring.yaml")["school"],
        "fit_lexicon": lexicon.export(),
        "cv_api_url": llm_cfg["cv"]["api_url"],
        "network": load("network.yaml"),
        "stats": {
            "offers": len(items), "new": sum(i["new"] for i in items),
            "by_sector": Counter(i["sector"] for i in items), "by_size": Counter(i["size"] or "?" for i in items),
            "by_region": Counter(i["region"] or "?" for i in items),
            "by_grade": Counter(i["grade"] for i in items),
            "by_calendar": Counter(i["calendar"] for i in items),
            "ai_analyzed": sum(1 for i in items if "ai" in i),
            "companies_ok": sum(1 for r in runs if not r["error"]),
            "companies_error": [r["company"] for r in runs if r["error"]],
        },
        "offers": items,
    }
    dump_json(ROOT / "docs" / "data" / "offers.json", payload)
    page = dashboard_page()
    (ROOT / "docs" / "index.html").write_text(
        '<!doctype html>\n<html lang="fr">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
        f"{page}\n</html>\n", encoding="utf-8")

    with (ROOT / "docs" / "data" / "offres_du_jour.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["note", "score", "data", "profil", "concurrence", "calendrier", "nouveau", "entreprise",
                    "intitule", "lieu", "secteur", "taille", "publie_le", "lien", "raisons"])
        for i in items:
            w.writerow([{"X": "hors calendrier"}.get(i["grade"], i["grade"]), i["score"], i["data"], i["profile"],
                        i["competition"], i["calendar"].replace("_", " "), "oui" if i["new"] else "", i["company"],
                        i["title"], i["location"], i["sector"], i["size"], i["posted_at"], i["url"],
                        " | ".join(f"{r[0]:+d} {r[1]}" for r in i["reasons"])])
    log.info("export : %d stages data (%d nouveaux) -> docs/data/offers.json", len(items), payload["stats"]["new"])


def dashboard_page() -> str:
    """Page du tableau de bord (fragment HTML), avec le logo intégré en data URI."""
    logo = base64.b64encode((Path(__file__).parent / "assets" / "logo-institut-agro-rennes-angers.png").read_bytes())
    page = (Path(__file__).parent / "dashboard.html").read_text(encoding="utf-8")
    return page.replace("__LOGO__", "data:image/png;base64," + logo.decode())


def main(argv=None):
    ap = argparse.ArgumentParser(prog="radar")
    ap.add_argument("--only", help="n'interroger que les entreprises dont le nom contient ce texte")
    ap.add_argument("--no-fetch", action="store_true", help="re-scorer sans rien télécharger")
    ap.add_argument("--no-llm", action="store_true", help="ne pas appeler le modèle (fiches en cache ignorées)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(message)s",
                        stream=sys.stdout)
    for name in ("urllib3", "requests"):
        logging.getLogger(name).setLevel(logging.WARNING)

    today = date.today()
    store = Store(ROOT / "data" / "radar.db")
    if not args.no_fetch:
        collect(store, today, args.only)
    export(store, today, use_llm=not args.no_llm)


if __name__ == "__main__":
    main()
