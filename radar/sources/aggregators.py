"""Sources transverses : API Adzuna (agrégateur, clé gratuite) et offres ajoutées à la main.

Les agrégateurs qui interdisent l'indexation de leurs recherches dans leur robots.txt ou leurs CGU
(LinkedIn, Indeed, HelloWork, JobTeaser, 1jeune1solution/stages) ne sont volontairement pas scrapés :
les offres repérées là-bas s'ajoutent dans data/manual_offers.csv et passent par le même scoring.
"""
from __future__ import annotations

import csv
import logging
import os
import re
from collections import Counter
from pathlib import Path

from ..filters import looks_like_internship
from ..models import Offer
from ..text import fold, strip_html

log = logging.getLogger(__name__)


def _adzuna_search(s, what: str, pages: int):
    """Résultats bruts d'une recherche Adzuna France (30 derniers jours), page par page."""
    app_id, app_key = os.getenv("ADZUNA_APP_ID"), os.getenv("ADZUNA_APP_KEY")
    for page in range(1, pages + 1):
        r = s.get(f"https://api.adzuna.com/v1/api/jobs/fr/search/{page}", params={
            "app_id": app_id, "app_key": app_key, "what": what, "results_per_page": 50,
            "max_days_old": 30, "content-type": "application/json",
        })
        if r.status_code != 200:
            log.warning("Adzuna %r page %s : HTTP %s", what, page, r.status_code)
            return
        results = r.json().get("results", [])
        yield from results
        if len(results) < 50:
            return


def _adzuna_offer(p: dict) -> Offer | None:
    title = strip_html(p.get("title"))
    desc = strip_html(p.get("description"))
    if not looks_like_internship(title, p.get("contract_type") or "", desc[:300]):
        return None
    return Offer(source="adzuna", company=(p.get("company") or {}).get("display_name", "?"),
                 title=title, url=p.get("redirect_url", ""),
                 location=(p.get("location") or {}).get("display_name", ""), country="fr",
                 description=desc, posted_at=p.get("created", ""))


def adzuna(queries: list[str], companies: list[dict], s, pages: int = 2) -> list[Offer]:
    """queries : recherches par mots-clés. companies : grands groupes dont le site carrière bloque les robots ;
    on cherche "stage <nom>" et on ne garde que les annonces publiées par l'entreprise elle-même."""
    if not (os.getenv("ADZUNA_APP_ID") and os.getenv("ADZUNA_APP_KEY")):
        log.info("Adzuna ignoré : définir ADZUNA_APP_ID et ADZUNA_APP_KEY (gratuit sur developer.adzuna.com)")
        return []
    offers = [o for q in queries for p in _adzuna_search(s, q, pages) if (o := _adzuna_offer(p))]
    for c in companies:
        # l'éditeur doit COMMENCER par le nom ("Safran Aircraft Engines" oui, "Cabinet X pour Safran" non)
        own = re.compile(r"(groupe |l['’] ?)?(" + "|".join(re.escape(fold(m)) for m in c["match"]) + r")(?![a-z0-9])")
        kept, own_other, others = 0, 0, Counter()
        for p in _adzuna_search(s, f"stage {c['name']}", pages):
            publisher = fold((p.get("company") or {}).get("display_name", ""))
            if not own.match(publisher):
                others[publisher or "?"] += 1
                continue
            if not (o := _adzuna_offer(p)):
                own_other += 1
                continue
            o.company, o.sector, o.size = c["name"], c.get("sector", ""), c.get("size", "")
            offers.append(o)
            kept += 1
        # éditeurs écartés affichés dans le journal : permet de repérer un groupe publié sous un autre nom
        log.info("Adzuna %-20s %3d stage(s), %d autre(s) annonce(s) du groupe, autres éditeurs : %s", c["name"][:20],
                 kept, own_other, ", ".join(f"{n} ({k})" for n, k in others.most_common(8)) or "aucun")
    return offers


def manual(path: Path) -> list[Offer]:
    """CSV (séparateur ,) : company,title,url,location,description,sector,posted_at"""
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as f:
        return [Offer(source="manuel", company=row.get("company", "").strip(),
                      title=row.get("title", "").strip(), url=row.get("url", "").strip(),
                      location=row.get("location", "").strip(), description=row.get("description", ""),
                      sector=row.get("sector", "").strip(), posted_at=row.get("posted_at", "").strip())
                for row in csv.DictReader(f) if row.get("title")]
