"""Sources transverses : API Adzuna (agrégateur, clé gratuite) et offres ajoutées à la main.

Les agrégateurs qui interdisent l'indexation de leurs recherches dans leur robots.txt ou leurs CGU
(LinkedIn, Indeed, HelloWork, JobTeaser, 1jeune1solution/stages) ne sont volontairement pas scrapés :
les offres repérées là-bas s'ajoutent dans data/manual_offers.csv et passent par le même scoring.
"""
from __future__ import annotations

import csv
import logging
import os
from pathlib import Path

from ..filters import looks_like_internship
from ..models import Offer
from ..text import strip_html

log = logging.getLogger(__name__)


def adzuna(queries: list[str], s, pages: int = 2) -> list[Offer]:
    app_id, app_key = os.getenv("ADZUNA_APP_ID"), os.getenv("ADZUNA_APP_KEY")
    if not (app_id and app_key):
        log.info("Adzuna ignoré : définir ADZUNA_APP_ID et ADZUNA_APP_KEY (gratuit sur developer.adzuna.com)")
        return []
    offers = []
    for q in queries:
        for page in range(1, pages + 1):
            r = s.get(f"https://api.adzuna.com/v1/api/jobs/fr/search/{page}", params={
                "app_id": app_id, "app_key": app_key, "what": q, "results_per_page": 50,
                "max_days_old": 30, "content-type": "application/json",
            })
            if r.status_code != 200:
                log.warning("Adzuna %r page %s : HTTP %s", q, page, r.status_code)
                break
            results = r.json().get("results", [])
            for p in results:
                title = strip_html(p.get("title"))
                desc = strip_html(p.get("description"))
                if not looks_like_internship(title, p.get("contract_type") or "", desc[:300]):
                    continue
                offers.append(Offer(
                    source="adzuna", company=(p.get("company") or {}).get("display_name", "?"),
                    title=title, url=p.get("redirect_url", ""),
                    location=(p.get("location") or {}).get("display_name", ""), country="fr",
                    description=desc, posted_at=p.get("created", ""),
                ))
            if len(results) < 50:
                break
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
