"""Jobboards de niche collectés via un canal officiel ou autorisé (RSS publié, API/MCP publique,
sitemap + JSON-LD, index de recherche public du site) pour attraper les stages des PME, startups,
instituts et organismes publics absents de companies.yaml.

Statut juridique vérifié le 23/09/2026 (robots.txt + CGU) : voir boards_report.md.
Toutes les fonctions : une poignée de requêtes par jour, via la PoliteSession (délai + UA explicite).
"""
from __future__ import annotations

import json
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from ..filters import looks_like_internship
from ..models import Offer
from ..text import strip_html

log = logging.getLogger(__name__)

MAX_DETAILS = 60
_LDJSON_RE = re.compile(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', re.S)


def _jobposting(html: str) -> dict:
    """Premier bloc JSON-LD de type JobPosting d'une page d'offre ({} si absent)."""
    for raw in _LDJSON_RE.findall(html):
        try:
            d = json.loads(raw)
        except ValueError:
            continue
        for x in d if isinstance(d, list) else [d]:
            if isinstance(x, dict) and x.get("@type") == "JobPosting":
                return x
    return {}


def _ld_location(jp: dict) -> str:
    loc = jp.get("jobLocation") or {}
    loc = loc[0] if isinstance(loc, list) and loc else loc
    a = (loc or {}).get("address") or {}
    return ", ".join(x for x in (a.get("postalCode"), a.get("addressLocality"), a.get("addressRegion")) if x)


# --------------------------------------------------------------------------- Apecita (agri/agro)
def apecita(cfg: dict | None, s) -> list[Offer]:
    """apecita.com, filtre contrat = Stage (id 5). ~80-100 stages en ligne, 20 par page.
    Liste HTML (robots.txt : Allow /) + JSON-LD JobPosting de chaque offre pour la description."""
    cfg = cfg or {}
    base = "https://www.apecita.com"
    cards, page = [], 1
    while page <= cfg.get("max_pages", 10):
        r = s.get(f"{base}/offres", params={"offer_search[contract][0]": 5, "page": page})
        r.raise_for_status()
        html = r.content.decode("utf-8", "replace")
        chunks = html.split('<div class="offers-list-item">')[1:]
        for c in chunks:
            m = re.search(r'<a href="(/offres/(\d+)-[^"?]+)[^"]*"[^>]*>\s*(.*?)\s*</a>', c, re.S)
            if not m:
                continue
            company = re.search(r'<img[^>]*alt="([^"]*)"', c)
            loc = re.search(r'<span class="location">.*?</svg>\s*([^<]+)</span>', c, re.S)
            tags = re.findall(r'<span class="tag">([^<]+)', c)
            date = re.search(r'<time class="date" datetime="([^"]+)"', c)
            cards.append({
                "path": m.group(1), "id": m.group(2),
                "title": strip_html(m.group(3).split('<span class="offers-list-item-title-special"')[0]),
                "company": strip_html(company.group(1)) if company else "",
                "location": strip_html(loc.group(1)) if loc else "", "tags": [t.strip() for t in tags],
                "date": date.group(1) if date else "", "partner": "Offre partenaire" in c,
            })
        if len(chunks) < 20 or f"page={page + 1}" not in html:
            break
        page += 1

    offers = []
    for c in cards:
        contract = " ".join(c["tags"])
        if not looks_like_internship(c["title"], contract or "stage"):
            continue
        desc, company, location = "", c["company"], c["location"]
        # Les "offres partenaires" redirigent vers un site tiers (reseau-tee.net...) : pas de détail.
        if not c["partner"] and len(offers) < cfg.get("max_details", MAX_DETAILS):
            try:
                d = s.get(base + c["path"], allow_redirects=False)
                if d.status_code == 200:
                    jp = _jobposting(d.content.decode("utf-8", "replace"))
                    desc = strip_html(strip_html(jp.get("description")))  # HTML doublement échappé
                    company = (jp.get("hiringOrganization") or {}).get("name") or company
                    location = _ld_location(jp) or location
            except Exception as e:  # le détail est un bonus
                log.debug("détail Apecita %s: %s", c["id"], e)
        offers.append(Offer(
            source="apecita", company=company or "?", title=c["title"], url=base + c["path"],
            location=location, country="fr", description=desc, posted_at=c["date"],
            sector=cfg.get("sector", "agro"), contract_hint=contract or "Stage",
            extra={"partner": c["partner"]},
        ))
    return offers


# --------------------------------------------------------------------------- INRAE (jobs.inrae.fr)
INRAE_ALGOLIA = {"app_id": "DVUTVWXJFU", "api_key": "1e2d2d60b4de29e857a2d1be26bb2fcd", "index": "inrae_prod"}


def inrae(cfg: dict | None, s) -> list[Offer]:
    """Index Algolia public (clé search-only publiée dans la page jobs.inrae.fr/listingOffre).
    Type 'temporary_offers' = stages, CDD, postdocs, thèses ; on garde field_offer_agreement == 'Stage'.
    Contenus sous Licence Ouverte Etalab 2.0 (CGU jobs.inrae.fr)."""
    cfg = {**INRAE_ALGOLIA, **(cfg or {})}
    url = f"https://{cfg['app_id']}-dsn.algolia.net/1/indexes/{cfg['index']}/query"
    headers = {"X-Algolia-Application-Id": cfg["app_id"], "X-Algolia-API-Key": cfg["api_key"],
               "Referer": "https://jobs.inrae.fr/"}
    hits, page = [], 0
    while page < 10:
        data = s.post(url, headers=headers, json={
            "query": "", "hitsPerPage": 200, "page": page,
            "filters": "type:temporary_offers AND search_api_language:fr",
            "attributesToRetrieve": ["title", "url", "field_offer_agreement", "field_region_value",
                                     "field_related_center_name", "created", "rendered_item_teaser_jobs"],
        }).json()
        hits += data.get("hits", [])
        page += 1
        if page >= data.get("nbPages", 0):
            break

    offers = []
    for h in hits:
        contract = h.get("field_offer_agreement", "")
        if contract != "Stage" and not looks_like_internship(h.get("title", "")):
            continue
        teaser = strip_html(h.get("rendered_item_teaser_jobs"))
        city = re.search(r"\b(\d{5}) ([^|]+?)\s+Voir l'offre", teaser)
        location = ", ".join(x for x in (city and f"{city.group(1)} {city.group(2).strip()}",
                                          h.get("field_region_value")) if x)
        link = "https://jobs.inrae.fr" + h.get("url", "")
        desc, start = "", ""
        if len(offers) < cfg.get("max_details", MAX_DETAILS):
            try:
                page_txt = strip_html(re.sub(r"<script.*?</script>|<style.*?</style>", " ",
                                             s.get(link).text, flags=re.S))
                # corps de l'offre : de "Environnement de travail" à "Votre qualité de vie"
                i = page_txt.find("Environnement de travail, missions")
                j = page_txt.find("Votre qualité de vie", i)
                desc = page_txt[max(i, 0):j if j > i else None][:6000]
                m = re.search(r"Début du contrat : (\d\d/\d\d/\d{4})", page_txt)
                start = m.group(1) if m else ""
            except Exception as e:
                log.debug("détail INRAE %s: %s", link, e)
        offers.append(Offer(
            source="inrae", company=f"INRAE · {h.get('field_related_center_name', '')}".rstrip(" ·"),
            title=h.get("title", ""), url=link, location=location, country="fr", description=desc,
            posted_at=datetime.fromtimestamp(int(h["created"]), timezone.utc).date().isoformat()
            if h.get("created") else "",
            sector=cfg.get("sector", "agro"), contract_hint=contract, extra={"start": start},
        ))
    return offers


# --------------------------------------------------------------------------- PASS (stages fonction publique)
def pass_fonction_publique(cfg: dict | None, s) -> list[Offer]:
    """Flux RSS officiel des offres de stage de la fonction publique (pass.fonction-publique.gouv.fr).
    ~450 stages, un seul appel (~1 Mo). Champs Dublin Core : publisher, contributor, date, coverage."""
    cfg = cfg or {}
    r = s.get(cfg.get("url", "https://www.pass.fonction-publique.gouv.fr/flux/offres_stages"),
              headers={"Accept": "application/rss+xml, application/xml, */*"})
    r.raise_for_status()
    dc = "{http://purl.org/dc/elements/1.1/}"
    offers = []
    for it in ET.fromstring(r.content).iter("item"):
        title = (it.findtext("title") or "").strip()
        if not looks_like_internship(title, "stage"):  # flux 100 % stages : sert à exclure alternance/3e
            continue
        pub, org = it.findtext(dc + "publisher") or "", it.findtext(dc + "contributor") or ""
        offers.append(Offer(
            source="pass", company=" · ".join(x for x in (org.strip(), pub.strip()) if x) or "?",
            title=title, url=(it.findtext("link") or "").strip() or "https://www.pass.fonction-publique.gouv.fr/",
            location=(it.findtext(dc + "coverage") or "").strip(), country="fr",
            description=strip_html(it.findtext("description")),
            posted_at=(it.findtext(dc + "date") or "").strip(), sector=cfg.get("sector", ""),
            contract_hint="Stage", extra={"ref": it.findtext(dc + "identifier") or ""},
        ))
    return offers


# --------------------------------------------------------------------------- Vitijob / Jobagri (sitemap)
def jobagri_family(cfg: dict | None, s) -> list[Offer]:
    """Vitijob (vin/spiritueux, ~150 stages) et Jobagri (agri, peu de stages) : même plateforme.
    Le sitemap liste les stages sous /stage/<id>/<slug> avec lastmod ; détail via JSON-LD JobPosting.
    Les URLs de recherche (?contrat=, ?keyword=...) sont interdites par robots.txt : on ne les utilise pas."""
    cfg = cfg or {}
    site = cfg.get("site", "https://www.vitijob.com")
    since = (datetime.now(timezone.utc) - timedelta(days=cfg.get("days", 30))).date().isoformat()
    sm = s.get(f"{site}/sitemap.xml").text
    urls = [(u, d[:10]) for u, d in re.findall(r"<loc>([^<]+/stage/\d+/[^<]+)</loc>\s*<lastmod>([^<]+)", sm)
            if d[:10] >= since]
    urls.sort(key=lambda x: x[1], reverse=True)
    offers = []
    for u, lastmod in urls[:cfg.get("max_details", MAX_DETAILS)]:
        try:
            jp = _jobposting(s.get(u).text)
        except Exception as e:
            log.debug("détail %s: %s", u, e)
            continue
        title = strip_html(jp.get("title")) or u.rsplit("/", 1)[-1].replace("-", " ")
        contract = jp.get("employmentType") or "Stage"
        if not looks_like_internship(title, contract):
            continue
        desc = strip_html(jp.get("description"))  # résumé "X cherche un Stage ..., à Ville (dpt). Postulez."
        city = re.search(r", à (.+?) \((\w{2,3})\)\. Postulez", desc)
        offers.append(Offer(
            source=site.split("//")[1].replace("www.", "").split(".")[0], title=title, url=u,
            company=strip_html((jp.get("identifier") or {}).get("name")) or "?",  # hiringOrganization = le site
            location=f"{city.group(1)} ({city.group(2)})" if city else strip_html(_ld_location(jp)),
            country="fr", description=desc,
            posted_at=jp.get("datePosted") or lastmod, sector=cfg.get("sector", "agro"), contract_hint=contract,
        ))
    return offers


# --------------------------------------------------------------------------- iQuesta (serveur MCP officiel)
IQUESTA_MCP = "https://mcp.iquesta.com/api/mcp"
# (matières iQuesta) 50 Data/Maths appliquées, 20 Statistiques, 22 Econométrie, 31 Agroalimentaire,
# 26 Informatique-Conseil, 79 Conseil/Stratégie ; + recherches plein texte sur la description.
IQUESTA_QUERIES = [{"matieres": [50]}, {"matieres": [20, 22]}, {"matieres": [31]}, {"matieres": [79]},
                   {"description": "python"}, {"description": "machine learning"},
                   {"description": "data science"}, {"term": "data"}]


def iquesta(cfg: dict | None, s) -> list[Offer]:
    """Outil MCP 'search_jobs' publié par iQuesta pour les assistants IA (JSON-RPC sur HTTP, sans clé).
    20 résultats max par requête, pas de pagination : on multiplie les requêtes ciblées."""
    cfg = cfg or {}
    found: dict[str, dict] = {}
    for i, q in enumerate(cfg.get("queries", IQUESTA_QUERIES)):
        r = s.post(IQUESTA_MCP, headers={"Accept": "application/json, text/event-stream"}, json={
            "jsonrpc": "2.0", "id": i, "method": "tools/call",
            "params": {"name": "search_jobs", "arguments": {"contracts": "1", "limit": 20, **q}}})
        res = r.json().get("result")
        if not res:
            log.warning("iQuesta %s : %s", q, r.text[:200])
            continue
        for j in json.loads(res["content"][0]["text"]) or []:
            found[str(j["id"])] = j
    return [Offer(source="iquesta", company=j.get("company") or "?", title=j.get("title", ""),
                  url=j.get("url", ""), location=j.get("location", ""), country="fr",
                  description=strip_html(j.get("summary")), contract_hint=j.get("type", "Stage"))
            for j in found.values()
            if j.get("company") != "iQuesta" and looks_like_internship(j.get("title", ""), j.get("type", ""))]


# --------------------------------------------------------------------------- RSS "WP Job Manager" (sport...)
def wp_job_feed(cfg: dict, s) -> list[Offer]:
    """Flux RSS standard du plugin WordPress 'WP Job Manager' (?feed=job_feed), p.ex. Sport Jobs Hunter :
    cfg = {"name": "Sport Jobs Hunter", "url": "https://www.sportjobshunter.com/", "job_types": "stage",
           "sector": "sport"}. Les titres ne disent pas "stage" : on s'appuie sur <job_listing:job_type>."""
    params = {"feed": "job_feed", "posts_per_page": 200}
    if cfg.get("job_types"):
        params["job_types"] = cfg["job_types"]
    r = s.get(cfg["url"], params=params, headers={"Accept": "application/rss+xml, */*"})
    r.raise_for_status()

    def field(it, tag):
        return next(((c.text or "").strip() for c in it if c.tag.split("}")[-1] == tag), "")

    offers = []
    for it in ET.fromstring(r.content).iter("item"):
        title, jtype = strip_html(field(it, "title")), field(it, "job_type")
        if not looks_like_internship(title, jtype):
            continue
        try:
            posted = parsedate_to_datetime(field(it, "pubDate")).isoformat()
        except (TypeError, ValueError):
            posted = ""
        offers.append(Offer(
            source=cfg.get("source", "wpjob"), company=strip_html(field(it, "company")) or "?", title=title,
            url=field(it, "link"), location=strip_html(field(it, "location")), country="fr",
            description=strip_html(field(it, "encoded") or field(it, "description")), posted_at=posted,
            sector=cfg.get("sector", ""), contract_hint=jtype,
        ))
    return offers


# --------------------------------------------------------------------------- Emploi-Environnement (RSS)
def emploi_environnement(cfg: dict | None, s) -> list[Offer]:
    """Flux RSS officiel (50 dernières offres, tous contrats). Peu de stages : filet de sécurité agro/env."""
    cfg = cfg or {}
    r = s.get(cfg.get("url", "https://www.emploi-environnement.com/rss.xml"))
    r.raise_for_status()
    offers = []
    for it in ET.fromstring(r.content).iter("item"):
        # le flux transforme "&eacute;" en "eteacute;" : on répare avant de nettoyer
        raw = re.sub(r"et([a-zA-Z]{2,8});", lambda m: "&" + m.group(1) + ";", it.findtext("description") or "")
        title, desc = strip_html(it.findtext("title")), strip_html(raw)
        if not looks_like_internship(title):
            continue
        company = desc.split(" - ", 1)[0] if " - " in desc[:80] else "?"
        try:
            posted = parsedate_to_datetime(it.findtext("pubDate")).isoformat()
        except (TypeError, ValueError):
            posted = ""
        offers.append(Offer(
            source="emploi-environnement", company=company, title=title,
            url=(it.findtext("link") or "").split("#")[0], location=it.findtext("region") or "",
            country="fr" if (it.findtext("country") or "") == "France" else "", description=desc,
            posted_at=posted, sector=cfg.get("sector", ""), contract_hint="Stage",
        ))
    return offers
