"""Connecteurs vers les flux publics des ATS (logiciels de recrutement) des entreprises.

Ces endpoints JSON sont ceux que les pages carrières des entreprises appellent elles-mêmes :
publics, sans authentification, prévus pour être affichés. On les interroge une fois par jour,
avec pause entre requêtes, et on ne télécharge le détail que des offres qui ressemblent à un stage.
"""
from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from ..filters import looks_like_internship, zone
from ..models import Offer
from ..text import strip_html

log = logging.getLogger(__name__)

MAX_DETAILS = 120  # détails téléchargés au plus par entreprise et par jour (intitulés "data" en premier)
SEARCH_TERMS = ["stage", "stagiaire", "intern", "internship"]
DATA_HINT = re.compile(r"data|donn[ée]e|stat|analy|machine|ia\b|ai\b|model|bi\b|scien|digital|r&d|recherche",
                       re.I)


def _xml(content: bytes) -> ET.Element:
    """Parse un flux RSS/XML, en réparant les '&' non échappés et caractères de contrôle de certains flux."""
    content = content.lstrip()  # certains flux ont des blancs avant la déclaration XML
    try:
        return ET.fromstring(content)
    except ET.ParseError:
        text = content.decode("utf-8", "replace")
        text = re.sub(r"&(?!#?\w+;)", "&amp;", text)
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
        return ET.fromstring(text.encode("utf-8"))


def _offer(cfg: dict, **kw) -> Offer:
    return Offer(company=cfg["name"], sector=cfg.get("sector", ""), **kw)


# --------------------------------------------------------------------------- SmartRecruiters
def smartrecruiters(cfg: dict, s) -> list[Offer]:
    cid = cfg["id"]
    base = f"https://api.smartrecruiters.com/v1/companies/{cid}/postings"
    postings: dict[str, dict] = {}
    for q in SEARCH_TERMS:
        offset = 0
        while True:
            r = s.get(base, params={"q": q, "limit": 100, "offset": offset})
            r.raise_for_status()
            data = r.json()
            for p in data.get("content", []):
                postings[p["id"]] = p
            offset += 100
            if offset >= data.get("totalFound", 0) or offset >= 1000:
                break

    offers = []
    for p in postings.values():
        level = (p.get("experienceLevel") or {}).get("id", "")
        contract = " ".join(f.get("valueLabel", "") for f in p.get("customField", [])
                            if "contract" in f.get("fieldLabel", "").lower())
        if not (level == "internship" or looks_like_internship(p["name"], contract)):
            continue
        if (p.get("location") or {}).get("country", "fr").lower() != "fr":
            continue
        if len(offers) >= MAX_DETAILS:
            break
        desc = ""
        try:
            d = s.get(f"{base}/{p['id']}").json()
            sections = (d.get("jobAd") or {}).get("sections") or {}
            desc = " ".join(strip_html((sections.get(k) or {}).get("text"))
                            for k in ("jobDescription", "qualifications", "additionalInformation"))
        except Exception as e:  # le détail est un bonus, pas bloquant
            log.debug("détail SR %s: %s", p["id"], e)
        loc = p.get("location") or {}
        offers.append(_offer(
            cfg, source="smartrecruiters", title=p["name"],
            url=f"https://jobs.smartrecruiters.com/{cid}/{p['id']}",
            location=loc.get("fullLocation") or loc.get("city", ""),
            country=(loc.get("country") or "").lower(),
            description=desc, posted_at=p.get("releasedDate", ""),
            contract_hint=contract or level,
        ))
    return offers


# --------------------------------------------------------------------------- Lever
def lever(cfg: dict, s) -> list[Offer]:
    host = "api.eu.lever.co" if cfg.get("region") == "eu" else "api.lever.co"
    r = s.get(f"https://{host}/v0/postings/{cfg['id']}", params={"mode": "json"})
    r.raise_for_status()
    offers = []
    for p in r.json():
        cat = p.get("categories") or {}
        commitment = cat.get("commitment") or ""
        if not looks_like_internship(p.get("text", ""), commitment):
            continue
        lists = " ".join(f"{l.get('text', '')} {strip_html(l.get('content'))}" for l in p.get("lists", []))
        created = p.get("createdAt")
        offers.append(_offer(
            cfg, source="lever", title=p.get("text", ""), url=p.get("hostedUrl", ""),
            location=cat.get("location") or "", country=(p.get("country") or "").lower(),
            description=f"{p.get('descriptionPlain', '')} {lists} {p.get('additionalPlain', '')}",
            posted_at=datetime.fromtimestamp(created / 1000, timezone.utc).isoformat() if created else "",
            contract_hint=commitment,
        ))
    return offers


# --------------------------------------------------------------------------- Greenhouse
def greenhouse(cfg: dict, s) -> list[Offer]:
    r = s.get(f"https://boards-api.greenhouse.io/v1/boards/{cfg['id']}/jobs", params={"content": "true"})
    r.raise_for_status()
    offers = []
    for p in r.json().get("jobs", []):
        if not looks_like_internship(p.get("title", "")):
            continue
        offers.append(_offer(
            cfg, source="greenhouse", title=p["title"], url=p.get("absolute_url", ""),
            location=(p.get("location") or {}).get("name", ""),
            description=strip_html(strip_html(p.get("content"))),  # contenu HTML échappé deux fois
            posted_at=p.get("first_published") or p.get("updated_at", ""),
        ))
    return offers


# --------------------------------------------------------------------------- Workday
def _workday_country_facet(s, api: str) -> dict:
    """Filtre pays France : l'identifiant de la facette change d'un client Workday à l'autre, on le lit
    dans la première réponse (facette `locationCountry`, parfois imbriquée dans un groupe)."""
    try:
        data = s.post(f"{api}/jobs", json={"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""}).json()
    except Exception:
        return {}
    stack = list(data.get("facets", []))
    while stack:
        f = stack.pop()
        for v in f.get("values", []):
            if "values" in v:
                stack.append(v)
            elif "country" in (f.get("facetParameter") or "").lower() and v.get("descriptor") == "France":
                return {f["facetParameter"]: [v["id"]]}
    return {}


def workday(cfg: dict, s) -> list[Offer]:
    host, site = cfg["host"], cfg["site"]
    tenant = cfg.get("tenant") or host.split(".")[0]
    api = f"https://{host}/wday/cxs/{tenant}/{site}"
    facets = _workday_country_facet(s, api)
    hits: dict[str, dict] = {}
    for q in SEARCH_TERMS:
        offset, total = 0, None
        while True:
            r = s.post(f"{api}/jobs", json={"appliedFacets": facets, "limit": 20, "offset": offset, "searchText": q})
            r.raise_for_status()
            data = r.json()
            postings = data.get("jobPostings", [])
            for p in postings:
                if p.get("externalPath"):
                    hits[p["externalPath"]] = p
            # Workday n'envoie le total qu'en première page (0 ensuite) : on garde celui de la première réponse
            if total is None:
                total = data.get("total", 0)
            offset += 20
            if not postings or offset >= total or offset >= 200:
                break

    offers = []
    # les grands groupes publient des centaines de stages : on télécharge d'abord le détail des intitulés "data"
    ordered = sorted(hits.items(), key=lambda kv: not DATA_HINT.search(kv[1].get("title", "") + " " + kv[0]))
    for path, p in ordered:
        # certains clients publient un intitulé générique ("Trainee") : le vrai titre est dans l'adresse de l'offre
        if len(p.get("title", "")) < 14 and "/job/" in path:
            slug = path.rsplit("/", 1)[-1].rsplit("_", 1)[0].replace("---", " - ").replace("-", " ").strip()
            if len(slug) > len(p.get("title", "")):
                p["title"] = slug
        if not looks_like_internship(p.get("title", "")):
            continue
        # filet de sécurité quand la facette pays n'existe pas : pas de détail pour les offres à l'étranger
        if zone("", p.get("locationsText", "")) == "etranger":
            continue
        if len(offers) >= MAX_DETAILS:
            break
        info = {}
        try:
            info = s.get(f"{api}{path}").json().get("jobPostingInfo", {})
        except Exception as e:
            log.debug("détail Workday %s: %s", path, e)
        country = (info.get("country") or {}).get("alpha2Code", "") or ""
        offers.append(_offer(
            cfg, source="workday", title=p["title"],
            url=info.get("externalUrl") or f"https://{host}/{site}{path}",
            location=info.get("location") or p.get("locationsText", ""),
            country=country.lower(),
            description=strip_html(info.get("jobDescription")),
            posted_at=info.get("startDate", ""),  # Workday : date de publication, pas de début de stage
            contract_hint=info.get("timeType", ""),
        ))
    return offers


# --------------------------------------------------------------------------- Teamtailor (RSS)
def teamtailor(cfg: dict, s) -> list[Offer]:
    url = cfg.get("url") or f"https://{cfg['id']}.teamtailor.com/jobs.rss"
    r = s.get(url)
    r.raise_for_status()
    offers = []
    for item in _xml(r.content).iter("item"):
        title = item.findtext("title", "")
        if not looks_like_internship(title):
            continue
        pub = item.findtext("pubDate")
        locs = [el.text for el in item.iter() if el.tag.endswith("city") and el.text]
        offers.append(_offer(
            cfg, source="teamtailor", title=title, url=item.findtext("link", ""),
            location=", ".join(locs), description=strip_html(item.findtext("description")),
            posted_at=parsedate_to_datetime(pub).isoformat() if pub else "",
        ))
    return offers


# --------------------------------------------------------------------------- Workable
def workable(cfg: dict, s) -> list[Offer]:
    r = s.get(f"https://apply.workable.com/api/v1/widget/accounts/{cfg['id']}", params={"details": "true"})
    r.raise_for_status()
    offers = []
    for p in r.json().get("jobs", []):
        if not looks_like_internship(p.get("title", ""), p.get("employment_type", "")):
            continue
        offers.append(_offer(
            cfg, source="workable", title=p["title"], url=p.get("url") or p.get("application_url", ""),
            location=", ".join(x for x in (p.get("city"), p.get("country")) if x),
            country=(p.get("country_code") or "").lower(),
            description=strip_html(p.get("description")), posted_at=p.get("published_on", ""),
            contract_hint=p.get("employment_type", ""),
        ))
    return offers


# --------------------------------------------------------------------------- Recruitee
def recruitee(cfg: dict, s) -> list[Offer]:
    r = s.get(f"https://{cfg['id']}.recruitee.com/api/offers/")
    r.raise_for_status()
    offers = []
    for p in r.json().get("offers", []):
        contract = p.get("employment_type_code", "")
        if not looks_like_internship(p.get("title", ""), contract):
            continue
        offers.append(_offer(
            cfg, source="recruitee", title=p["title"], url=p.get("careers_url", ""),
            location=p.get("location", ""), country=(p.get("country_code") or "").lower(),
            description=strip_html(f"{p.get('description', '')} {p.get('requirements', '')}"),
            posted_at=p.get("published_at") or p.get("created_at", ""), contract_hint=contract,
        ))
    return offers


# --------------------------------------------------------------------------- Ashby
def ashby(cfg: dict, s) -> list[Offer]:
    r = s.get(f"https://api.ashbyhq.com/posting-api/job-board/{cfg['id']}")
    r.raise_for_status()
    offers = []
    for p in r.json().get("jobs", []):
        contract = p.get("employmentType", "")
        if not looks_like_internship(p.get("title", ""), contract):
            continue
        addr = ((p.get("address") or {}).get("postalAddress") or {})
        location = ", ".join(x for x in (p.get("location"), addr.get("addressCountry")) if x)
        offers.append(_offer(
            cfg, source="ashby", title=p["title"], url=p.get("jobUrl", ""), location=location,
            description=p.get("descriptionPlain", ""), posted_at=p.get("publishedAt", ""),
            contract_hint=contract,
        ))
    return offers


# --------------------------------------------------------------------------- Personio (XML)
def personio(cfg: dict, s) -> list[Offer]:
    r = s.get(f"https://{cfg['id']}.jobs.personio.de/xml", params={"language": "fr"})
    r.raise_for_status()
    offers = []
    for p in _xml(r.content).iter("position"):
        title = p.findtext("name", "")
        contract = f"{p.findtext('employmentType', '')} {p.findtext('schedule', '')}"
        if not looks_like_internship(title, contract):
            continue
        desc = " ".join(strip_html(d.findtext("value")) for d in p.iter("jobDescription"))
        offers.append(_offer(
            cfg, source="personio", title=title,
            url=f"https://{cfg['id']}.jobs.personio.de/job/{p.findtext('id', '')}",
            location=p.findtext("office", ""), description=desc, posted_at=p.findtext("createdAt", ""),
            contract_hint=contract.strip(),
        ))
    return offers


# --------------------------------------------------------------------------- Breezy HR
def breezy(cfg: dict, s) -> list[Offer]:
    r = s.get(f"https://{cfg['id']}.breezy.hr/json")
    r.raise_for_status()
    offers = []
    for p in r.json():
        contract = (p.get("type") or {}).get("name", "")
        if not looks_like_internship(p.get("name", ""), contract):
            continue
        loc = p.get("location") or {}
        offers.append(_offer(
            cfg, source="breezy", title=p["name"], url=p.get("url", ""), location=loc.get("name", ""),
            country=((loc.get("country") or {}).get("id") or "").lower(),
            description=strip_html(p.get("description")), posted_at=p.get("published_date", ""),
            contract_hint=contract,
        ))
    return offers


# --------------------------------------------------------------------------- RSS (SuccessFactors, Talentsoft...)
def rss(cfg: dict, s) -> list[Offer]:
    """Flux RSS d'une page carrières. `url` peut contenir {q} : il est alors appelé pour chaque terme de
    recherche (les flux SuccessFactors ne renvoient que les 20 offres les plus récentes par requête)."""
    urls = [cfg["url"].replace("{q}", q) for q in SEARCH_TERMS] if "{q}" in cfg["url"] else [cfg["url"]]
    items: dict[str, ET.Element] = {}
    for url in urls:
        r = s.get(url, headers={"Accept": "application/rss+xml, application/xml, */*"})
        r.raise_for_status()
        for item in _xml(r.content).iter("item"):
            items[item.findtext("link", "")] = item
    offers = []
    for link, item in items.items():
        title = item.findtext("title", "")
        if not looks_like_internship(title):
            continue
        # SuccessFactors : "Intitulé (Ville, FR, 75014)"
        loc = title[title.rfind("(") + 1:-1] if title.endswith(")") and "," in title[title.rfind("("):] else ""
        pub = item.findtext("pubDate")
        try:
            posted = parsedate_to_datetime(pub).isoformat() if pub else ""
        except (TypeError, ValueError):
            posted = ""
        offers.append(_offer(
            cfg, source=cfg.get("ats", "rss"), title=title[:len(title) - len(loc) - 2].strip() if loc else title,
            url=link, location=loc, country="fr" if ", FR" in loc else "",
            description=strip_html(item.findtext("description")), posted_at=posted,
        ))
    return offers


# --------------------------------------------------------------------------- Eightfold
def eightfold(cfg: dict, s) -> list[Offer]:
    """Deux variantes d'API selon les clients : `pcsx` (Kering, Estée Lauder, Corteva) et `v2` (BCG, Forvia)."""
    base, domain = cfg["base"].rstrip("/"), cfg["id"]
    positions: dict[str, dict] = {}
    for q in SEARCH_TERMS:
        start = 0
        while start < 300:
            params = {"domain": domain, "start": start, "num": 50, "location": "France", "query": q}
            if cfg.get("flavor") == "v2":
                data = s.get(f"{base}/api/apply/v2/jobs", params=params).json()
                batch, total = data.get("positions", []), data.get("count", 0)
            else:
                data = s.get(f"{base}/api/pcsx/search", params=params).json().get("data", {})
                batch, total = data.get("positions", []), data.get("count", 0)
            for p in batch:
                positions[str(p["id"])] = p
            start += 50
            if not batch or start >= total:
                break
    offers = []
    for pid, p in positions.items():
        if not looks_like_internship(p.get("name", "")):
            continue
        ts = p.get("t_create") or p.get("postedTs")
        url = p.get("canonicalPositionUrl") or f"{base}{p.get('positionUrl') or f'/careers/job/{pid}'}"
        offers.append(_offer(
            cfg, source="eightfold", title=p["name"], url=url,
            location=p.get("location") or ", ".join(p.get("locations") or []),
            description=strip_html(p.get("job_description")),
            posted_at=datetime.fromtimestamp(int(ts), timezone.utc).isoformat() if ts else "",
        ))
    return offers


# --------------------------------------------------------------------------- Jibe (Danone, Garmin...)
def jibe(cfg: dict, s) -> list[Offer]:
    offers, seen = [], set()
    for q in SEARCH_TERMS:
        for page in range(1, 6):
            data = s.get(cfg["url"], params={"page": page, "limit": 100, "keywords": q,
                                             **({"country": cfg["country"]} if cfg.get("country") else {})}).json()
            jobs = data.get("jobs", [])
            for j in jobs:
                d = j.get("data", {})
                if d.get("req_id") in seen or not looks_like_internship(d.get("title", ""), d.get("employment_type", "")):
                    continue
                seen.add(d.get("req_id"))
                offers.append(_offer(
                    cfg, source="jibe", title=d["title"], url=d.get("apply_url", ""),
                    location=d.get("full_location") or d.get("location_name", ""),
                    country=(d.get("country_code") or "").lower(),
                    description=strip_html(" ".join(d.get(k) or "" for k in ("description", "responsibilities",
                                                                              "qualifications"))),
                    posted_at=d.get("posted_date", ""), contract_hint=d.get("employment_type", ""),
                ))
            if len(jobs) < 100:
                break
    return offers


# --------------------------------------------------------------------------- Algolia (LVMH)
def algolia(cfg: dict, s) -> list[Offer]:
    """Index de recherche public de la page carrières de l'employeur (clé "search-only" publiée par le site)."""
    headers = {"X-Algolia-Application-Id": cfg["app_id"], "X-Algolia-API-Key": cfg["api_key"],
               "Referer": cfg.get("referer", "")}
    url = f"https://{cfg['app_id']}-dsn.algolia.net/1/indexes/{cfg['index']}/query"
    offers, page = [], 0
    while page < 30:
        data = s.post(url, headers=headers, json={"params": f"query=&hitsPerPage=100&page={page}"
                                                            f"&facetFilters={cfg['facet_filters']}"}).json()
        for h in data.get("hits", []):
            offers.append(Offer(
                company=f"{cfg['name'].split(' (')[0]} · {h['maison']}" if h.get("maison") else cfg["name"],
                sector=cfg.get("sector", ""), source="lvmh", title=h.get("name", ""), url=h.get("link", ""),
                location=", ".join(x for x in (h.get("city"), h.get("country")) if x),
                description=strip_html(" ".join(h.get(k) or "" for k in ("description", "jobResponsabilities",
                                                                         "profile"))),
                posted_at=datetime.fromtimestamp(int(h["publicationTimestamp"]), timezone.utc).isoformat()
                if h.get("publicationTimestamp") else "",
                contract_hint=h.get("contract", ""),
            ))
        page += 1
        if page >= data.get("nbPages", 0):
            break
    return offers


# --------------------------------------------------------------------------- Capgemini
def capgemini(cfg: dict, s) -> list[Offer]:
    offers, page = [], 1
    while page < 40:
        data = s.get(cfg["url"], params={"page": page, "size": 100, "country_code": "fr-fr"}).json()
        for p in data.get("data", []):
            if not looks_like_internship(p.get("title", ""), p.get("contract_type", "")):
                continue
            offers.append(Offer(
                company=f"Capgemini · {p['brand']}" if p.get("brand") and p["brand"] != "Capgemini" else "Capgemini",
                sector=cfg.get("sector", ""), source="capgemini", title=p["title"], url=p.get("apply_job_url", ""),
                location=p.get("location", ""), country="fr", description=p.get("description_stripped", ""),
                posted_at=p.get("updated_at", ""), contract_hint=p.get("contract_type", ""),
            ))
        if page * 100 >= data.get("count", 0):
            break
        page += 1
    return offers


# --------------------------------------------------------------------------- DigitalRecruiters (Decathlon...)

def digitalrecruiters(cfg: dict, s) -> list[Offer]:
    """API publique des sites carrières DigitalRecruiters ; `id` = nom de domaine du site carrières."""
    api = "https://api.digitalrecruiters.com/public/v1/careers-site/job-ads"
    base = {"domainName": cfg["id"], "locale": "fr_FR"}
    items, page = [], 1
    while page <= 20:
        data = s.post(api, params={**base, "limit": 50, "page": page}, json={"filters": {}, "q": "stage"}).json()
        items += data.get("items", [])
        if page * 50 >= data.get("count", 0):
            break
        page += 1
    items = [i for i in items if looks_like_internship(i.get("title", ""), i.get("contract", ""))]
    # beaucoup de stages en magasin : on télécharge d'abord le détail des intitulés qui évoquent la data
    items.sort(key=lambda i: not DATA_HINT.search(f"{i.get('title', '')} {i.get('job', '')}"))
    offers = []
    for n, i in enumerate(items):
        desc, posted = "", ""
        if n < MAX_DETAILS:
            try:
                d = s.get(f"{api}/{i['job_ad_id']}", params=base).json()
                desc = strip_html(" ".join(str(v) for k, v in d.items()
                                           if isinstance(v, str) and any(w in k for w in ("description", "profile", "mission"))))
                posted = d.get("republished_at", "")
            except Exception as e:
                log.debug("détail DigitalRecruiters %s: %s", i.get("id"), e)
        offers.append(_offer(
            cfg, source="digitalrecruiters", title=f"{i['title']} · {i['job']}" if i.get("job") and len(i["title"]) < 12
            else i["title"], url=f"https://{cfg['id']}/fr/annonce/{i.get('url', '')}", location=i.get("location", ""),
            description=desc, posted_at=posted.replace(" ", "T"), contract_hint=i.get("contract", ""),
        ))
    return offers


# --------------------------------------------------------------------------- Oracle Recruiting Cloud (Hermès...)
def oracle_hcm(cfg: dict, s) -> list[Offer]:
    """API "candidate experience" d'Oracle HCM ; `host` = serveur *.oraclecloud.com, `site` = siteNumber (CX_1)."""
    api = f"https://{cfg['host']}/hcmRestApi/resources/latest"
    reqs: dict[str, dict] = {}
    for q in ("stage", "stagiaire", "intern"):
        offset = 0
        while offset < 500:
            data = s.get(f"{api}/recruitingCEJobRequisitions", params={
                "onlyData": "true", "expand": "requisitionList.secondaryLocations",
                "finder": f"findReqs;siteNumber={cfg['site']},keyword={q},limit=25,offset={offset},"
                          "sortBy=POSTING_DATES_DESC"}).json()
            batch = (data.get("items") or [{}])[0].get("requisitionList", [])
            for r in batch:
                reqs[r["Id"]] = r
            offset += 25
            if len(batch) < 25:
                break
    offers = []
    for rid, r in reqs.items():
        if not looks_like_internship(r.get("Title", "")) or (r.get("PrimaryLocationCountry") or "FR") != "FR":
            continue
        desc = ""
        if len(offers) < MAX_DETAILS:
            try:
                d = s.get(f"{api}/recruitingCEJobRequisitionDetails", params={
                    "onlyData": "true", "expand": "all",
                    "finder": f'ById;Id="{rid}",siteNumber={cfg["site"]}'}).json()
                it = (d.get("items") or [{}])[0]
                desc = strip_html(" ".join(it.get(k) or "" for k in ("ExternalDescriptionStr",
                                                                      "ExternalResponsibilitiesStr",
                                                                      "ExternalQualificationsStr")))
            except Exception as e:
                log.debug("détail Oracle %s: %s", rid, e)
        offers.append(_offer(
            cfg, source="oracle", title=r["Title"], url=cfg["job_url"].format(id=rid),
            location=r.get("PrimaryLocation", ""), country="fr", description=desc,
            posted_at=r.get("PostedDate", ""),
        ))
    return offers


# --------------------------------------------------------------------------- Phenom (Orange, Allianz, McCain...)
def phenom(cfg: dict, s) -> list[Offer]:
    """Recherche publique des sites carrières Phenom (POST /widgets, celle qu'appelle la page de résultats).
    cfg : host, ref (refNum du site), lang (fr_fr), country (valeur de la facette pays ; lue sur le site si absente),
    job_url (modèle d'adresse d'une offre, avec {id})."""
    api = f"https://{cfg['host']}/widgets"
    base = {"lang": cfg.get("lang", "fr_fr"), "deviceType": "desktop", "country": "fr", "refNum": cfg["ref"],
            "siteType": "external"}
    search = {**base, "pageName": "search-results", "ddoKey": "refineSearch", "all_fields": ["country"],
              "global": True, "locationData": {}, "sortBy": "", "subsearch": "", "clearAll": False,
              "jdsource": "facets", "isSliderEnable": False}
    # la valeur de la facette pays varie selon les sites ("FRANCE", "France") : on la lit dans les agrégations
    country = cfg.get("country")
    if not country:
        aggs = s.post(api, json={**search, "from": 0, "size": 1, "jobs": False, "counts": True, "keywords": "",
                                 "selected_fields": {}}).json().get("refineSearch", {}).get("data", {}).get("aggregations", [])
        values = next((a.get("value", {}) for a in aggs if a.get("field") == "country"), {})
        country = max((v for v in values if v.strip().lower() == "france"), key=lambda v: values[v], default=None)
    jobs: dict[str, dict] = {}
    for q in SEARCH_TERMS:
        start = 0
        while start < 500:
            body = {**base, "pageName": "search-results", "ddoKey": "refineSearch", "from": start, "size": 100,
                    "jobs": True, "counts": False, "all_fields": ["country"], "keywords": q, "global": True,
                    "selected_fields": {"country": [country]} if country else {}, "locationData": {},
                    "sortBy": "", "subsearch": "", "clearAll": False, "jdsource": "facets", "isSliderEnable": False}
            rs = s.post(api, json=body).json().get("refineSearch", {})
            batch = (rs.get("data") or {}).get("jobs", [])
            for j in batch:
                jobs[j["jobId"]] = j
            start += 100
            if not batch or start >= rs.get("totalHits", 0):
                break
    offers = []
    items = [j for j in jobs.values() if looks_like_internship(j.get("title", ""), j.get("contractType", ""))]
    items.sort(key=lambda j: not DATA_HINT.search(j.get("title", "")))
    for n, j in enumerate(items):
        desc = j.get("descriptionTeaser", "")
        if n < MAX_DETAILS:
            try:
                d = s.post(api, json={**base, "pageName": "job", "ddoKey": "jobDetail", "jobId": j["jobId"]}).json()
                desc = strip_html(((d.get("jobDetail") or {}).get("data") or {}).get("job", {}).get("description")) or desc
            except Exception as e:
                log.debug("détail Phenom %s: %s", j["jobId"], e)
        offers.append(_offer(
            cfg, source="phenom", title=j["title"], url=cfg["job_url"].format(id=j["jobId"]),
            location=j.get("cityStateCountry") or j.get("location", ""),
            country="fr" if (j.get("country") or "").strip().lower() == "france" else "",
            description=desc, posted_at=j.get("postedDate", ""), contract_hint=j.get("contractType", ""),
        ))
    return offers


# --------------------------------------------------------------------------- Radancy / TalentBrew (VINCI, FDJ...)
_RADANCY_ITEM = re.compile(r'<a[^>]+href="(/[a-z]{2}/[^"]+/\d+/\d+)"[^>]*>(.*?)</a>', re.S)


def radancy(cfg: dict, s) -> list[Offer]:
    """Résultats de recherche des sites Radancy (GET /<lang>/search-jobs/results, JSON contenant le HTML de la liste).
    cfg : host, lang (fr). Le détail vient du JSON-LD JobPosting de la page de l'offre."""
    from .boards import _jobposting, _ld_location
    host, lang = cfg["host"], cfg.get("lang", "fr")
    headers = {"Accept": "application/json, text/javascript, */*; q=0.01", "X-Requested-With": "XMLHttpRequest"}
    found: dict[str, str] = {}
    for q in ("stage", "stagiaire", "internship"):
        page = 1
        while page <= 30:
            data = s.get(f"https://{host}/{lang}/search-jobs/results", headers=headers, params={
                "CurrentPage": page, "RecordsPerPage": 100, "Keywords": q, "Location": "", "SearchType": 5,
                "ActiveFacetID": 0, "ShowRadius": "False", "IsPagination": "False", "SortCriteria": 0,
                "SortDirection": 0, "SearchResultsModuleName": "Search Results",
                "SearchFiltersModuleName": "Search Filters", "OrganizationIds": "", "FacetFilters": ""}).json()
            html = data.get("results") or ""
            items = _RADANCY_ITEM.findall(html)
            for href, inner in items:
                found.setdefault(href, strip_html(inner))
            pages = re.search(r'data-total-pages="(\d+)"', html)
            if not items or page >= int(pages.group(1) if pages else 1):
                break
            page += 1
    offers = []
    # l'intitulé de la liste contient titre, lieu et catégorie : on garde les stages, "data" d'abord
    items = [(h, t) for h, t in found.items() if looks_like_internship(t) or "/stage" in h]
    items.sort(key=lambda it: not DATA_HINT.search(it[1]))
    for n, (href, text) in enumerate(items[: MAX_DETAILS * 2]):
        url = f"https://{host}{href}"
        title, location, desc, posted = text, "", "", ""
        if n < MAX_DETAILS:
            try:
                jp = _jobposting(s.get(url, headers={"Accept": "text/html"}).text)
                title = strip_html(jp.get("title")) or title
                location = _ld_location(jp)
                desc = strip_html(strip_html(jp.get("description")))
                posted = jp.get("datePosted", "")
            except Exception as e:
                log.debug("détail Radancy %s: %s", href, e)
        if not looks_like_internship(title, "stage" if "/stage" in href else ""):
            continue
        offers.append(_offer(cfg, source="radancy", title=title, url=url, location=location or href.split("/")[3],
                             description=desc, posted_at=posted))
    return offers


# --------------------------------------------------------------------------- iCIMS (Carrefour, Garmin...)
# lien d'une offre : l'intitulé complet est dans l'attribut title ("146748 - Intitulé du poste")
_ICIMS_LINK = re.compile(r'href="(https://[^"]+/jobs/(\d+)/[^"/]+/job)[^"]*"[^>]*title="\d+ - ([^"]+)"')


def icims(cfg: dict, s) -> list[Offer]:
    """Pages de recherche publiques d'un portail iCIMS (id = sous-domaine, ex. recrute1-carrefour.icims.com)."""
    from .boards import _jobposting, _ld_location
    host = cfg["id"]
    found: dict[str, tuple[str, str]] = {}
    for q in ("stage", "stagiaire", "intern"):
        for page in range(0, 20):
            html = s.get(f"https://{host}/jobs/search", headers={"Accept": "text/html"},
                         params={"ss": "1", "searchKeyword": q, "in_iframe": "1", "pr": page}).text
            links = _ICIMS_LINK.findall(html)
            new = [l for l in links if l[1] not in found]
            for url, jid, title in links:
                found.setdefault(jid, (url, strip_html(title)))
            if not new:
                break
    offers = []
    items = [(jid, u, t) for jid, (u, t) in found.items() if looks_like_internship(t)]
    items.sort(key=lambda it: not DATA_HINT.search(it[2]))
    for n, (jid, url, title) in enumerate(items):
        location, desc, posted = "", "", ""
        if n < MAX_DETAILS:
            try:
                jp = _jobposting(s.get(url, params={"in_iframe": "1"}, headers={"Accept": "text/html"}).text)
                location, posted = _ld_location(jp), jp.get("datePosted", "")
                desc = strip_html(strip_html(jp.get("description")))
            except Exception as e:
                log.debug("détail iCIMS %s: %s", jid, e)
        offers.append(_offer(cfg, source="icims", title=title, url=url, location=location, country=cfg.get("country", ""),
                             description=desc, posted_at=posted))
    return offers


FETCHERS = {
    "phenom": phenom,
    "radancy": radancy,
    "icims": icims,
    "digitalrecruiters": digitalrecruiters,
    "oracle_hcm": oracle_hcm,
    "rss": rss,
    "sf_rss": rss,
    "talentsoft": rss,
    "eightfold": eightfold,
    "jibe": jibe,
    "algolia": algolia,
    "capgemini": capgemini,
    "personio": personio,
    "breezy": breezy,
    "smartrecruiters": smartrecruiters,
    "lever": lever,
    "greenhouse": greenhouse,
    "workday": workday,
    "teamtailor": teamtailor,
    "workable": workable,
    "recruitee": recruitee,
    "ashby": ashby,
}
