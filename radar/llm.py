"""Analyse des offres par un petit modèle de langage (OpenAI, modèle "nano" configuré dans config/llm.yaml).

Pour chaque offre, le modèle renvoie une fiche structurée (schéma JSON strict) : est-ce un stage data, quel métier,
quel domaine, quelles compétences exigées / appréciées (choisies dans le dictionnaire config/fit.yaml), date de début,
durée, intensité data et accessibilité pour un·e élève de l'Institut Agro, avec une raison courte pour chaque note.

- La clé est lue dans OPENAI_API_KEY, jamais dans un fichier du dépôt. Sans clé : rien n'est appelé.
- Chaque fiche est mise en cache dans la base (table llm_offers) : une offre n'est réanalysée que si son texte
  ou la version du prompt change. Chaque exécution journalise le nombre d'appels et le coût.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import requests

log = logging.getLogger(__name__)

API_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/") + "/chat/completions"
ROLE_LABELS = {
    "ds": "data scientist / machine learning", "da": "data analyst / BI", "de": "data engineer",
    "stat": "statisticien / biostatisticien", "rnd": "R&D / recherche appliquée", "consult": "consultant data",
    "biz": "business analyst / finance / contrôle de gestion (peu de data science)", "other": "autre",
}
DEGREES = ["ingenieur_ou_master", "ecole_de_commerce", "doctorat", "indifferent"]

SCHEMA_TABLE = """CREATE TABLE IF NOT EXISTS llm_offers (
    key TEXT PRIMARY KEY, text_hash TEXT, prompt_version INTEGER, model TEXT, analysis TEXT,
    tokens_in INTEGER, tokens_cached INTEGER, tokens_out INTEGER, created TEXT)"""


def text_hash(*parts: str) -> str:
    return hashlib.sha1("\x1f".join(parts).encode()).hexdigest()[:16]


class LLM:
    def __init__(self, cfg: dict, fit_cfg: dict, school: dict):
        self.cfg = cfg
        self.key = os.getenv("OPENAI_API_KEY", "").strip()
        self.model = cfg["model"]
        self.session = requests.Session()
        self._lock = threading.Lock()
        self.usage = {"calls": 0, "errors": 0, "in": 0, "cached": 0, "out": 0, "cost": 0.0}
        self.skill_ids = [s["id"] for s in fit_cfg["skills"]]
        self.domain_ids = [d["id"] for d in fit_cfg["domains"]] + ["other"]
        self.lang_ids = [l["id"] for l in fit_cfg["languages"]]
        self.system = self._system_prompt(fit_cfg, school)

    @property
    def available(self) -> bool:
        return bool(self.cfg.get("enabled", True) and self.key)

    # ------------------------------------------------------------------ prompt et schéma
    def _system_prompt(self, fit: dict, school: dict) -> str:
        skills = "\n".join(f"- {s['id']} : {s['label']} ({', '.join(s['terms'][:6])})" for s in fit["skills"])
        domains = "\n".join(f"- {d['id']} : {d['label']}" for d in fit["domains"]) + "\n- other : autre"
        roles = "\n".join(f"- {k} : {v}" for k, v in ROLE_LABELS.items())
        langs = "\n".join(f"- {l['id']} : {l['label']}" for l in fit["languages"])
        return f"""Tu analyses des offres de stage pour les élèves de {school['name']} ({school['track']}).
Ils cherchent : {school['internship']}.

Profil type de ces élèves : ingénieurs agronomes spécialisés en science des données. Solides en statistique
(modèles linéaires et mixtes, GLM, ANOVA, plans d'expériences, analyse multivariée), Python et R, SQL, machine
learning classique (scikit-learn), visualisation ; notions de deep learning et d'IA générative selon les élèves.
Formation peu tournée vers le génie logiciel (Java, C++, cloud, Kubernetes, front-end). Culture scientifique en
agronomie, alimentation, biologie, environnement ; français natif, anglais courant.

Réponds uniquement avec le JSON demandé. Règles :
- is_internship : vrai si c'est un stage (pas une alternance, un CDI, un VIE ou une thèse).
- is_data_role : vrai si une part significative du travail est de l'analyse de données, de la statistique,
  du machine learning ou de l'ingénierie des données.
- role : le métier de l'intitulé et des missions, parmi :
{roles}
- domain : le domaine d'activité de l'entreprise ou du sujet, parmi :
{domains}
- required_skills / nice_to_have_skills : compétences explicitement demandées (exigées / appréciées), uniquement
  parmi ces identifiants ; ne rien inventer, ne rien déduire du seul intitulé :
{skills}
- other_skills : autres compétences techniques demandées absentes de la liste (5 au plus, en quelques mots).
- degree_target : formation visée. "ingenieur_ou_master" si école d'ingénieur, master ou équivalent sont cités
  (même avec l'école de commerce en alternative) ; "ecole_de_commerce" seulement si c'est le seul profil cité ;
  "doctorat" si un doctorat est exigé ; sinon "indifferent".
- start_month : date de début du stage au format AAAA-MM si elle est indiquée ; un mois sans année désigne la
  prochaine occurrence après la date de publication ; null si non précisée ou "dès que possible".
- duration_months : durée du stage en mois si indiquée, sinon null.
- languages_required : langues EXIGÉES autres que le français et l'anglais, parmi :
{langs}
- data_intensity (0-100) : part du travail quotidien consacrée à la data. 90-100 : data scientist, ML, statisticien ;
  60-85 : analyste avec SQL / Python / statistique réguliers ; 30-55 : poste métier avec reporting Excel / Power BI ;
  moins de 30 : pas un poste data.
- accessibility (0-100) : chances réelles d'un·e élève de ce profil face aux autres candidats, d'après les
  compétences et la formation demandées. 80-100 : profil agro / bio / statistique explicitement bienvenu et
  compétences alignées ; 60-79 : bon alignement, petits écarts ; 40-59 : écarts notables (génie logiciel poussé,
  préférence école de commerce, sélection très élitiste) ; moins de 40 : doctorat exigé, profil commerce uniquement,
  compétences très éloignées. Ne tiens compte NI du prestige de l'entreprise NI des dates : ils sont évalués à part.
- data_reason, accessibility_reason : une phrase de 15 mots au plus, en français, qui justifie la note.
- summary : la mission en une phrase de 25 mots au plus, en français."""

    def _schema(self) -> dict:
        arr = lambda enum: {"type": "array", "items": {"type": "string", "enum": enum}}
        props = {
            "is_internship": {"type": "boolean"},
            "is_data_role": {"type": "boolean"},
            "role": {"type": "string", "enum": list(ROLE_LABELS)},
            "domain": {"type": "string", "enum": self.domain_ids},
            "required_skills": arr(self.skill_ids),
            "nice_to_have_skills": arr(self.skill_ids),
            "other_skills": {"type": "array", "items": {"type": "string"}},
            "degree_target": {"type": "string", "enum": DEGREES},
            "start_month": {"type": ["string", "null"]},
            "duration_months": {"type": ["integer", "null"]},
            "languages_required": arr(self.lang_ids),
            "data_intensity": {"type": "integer"},
            "data_reason": {"type": "string"},
            "accessibility": {"type": "integer"},
            "accessibility_reason": {"type": "string"},
            "summary": {"type": "string"},
        }
        return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}

    # ------------------------------------------------------------------ appel
    def _call(self, user: str) -> tuple[dict, dict]:
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": self.system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": "analyse_offre", "strict": True, "schema": self._schema()}},
            "max_completion_tokens": self.cfg["max_output_tokens"],
        }
        if self.model.startswith("gpt-5") and self.cfg.get("reasoning_effort"):
            body["reasoning_effort"] = self.cfg["reasoning_effort"]
        headers = {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"}
        for attempt in range(5):
            r = self.session.post(API_URL, json=body, headers=headers, timeout=60)
            if r.status_code == 404 and self.model != self.cfg.get("fallback_model"):
                log.warning("modèle %s indisponible, bascule sur %s", self.model, self.cfg["fallback_model"])
                with self._lock:
                    self.model = self.cfg["fallback_model"]
                body["model"] = self.model
                body.pop("reasoning_effort", None)
                continue
            if r.status_code in (429, 500, 502, 503) and attempt < 4:
                time.sleep(min(float(r.headers.get("retry-after", 0) or 2 ** attempt * 2), 30))
                continue
            r.raise_for_status()
            data = r.json()
            msg = data["choices"][0]["message"]
            if msg.get("refusal") or not msg.get("content"):
                raise ValueError(f"réponse vide ou refus : {msg.get('refusal') or data['choices'][0].get('finish_reason')}")
            return json.loads(msg["content"]), data.get("usage", {})
        raise RuntimeError("trop de tentatives")

    def _account(self, usage: dict) -> tuple[int, int, int]:
        tin = usage.get("prompt_tokens", 0)
        cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
        tout = usage.get("completion_tokens", 0)
        p = self.cfg["prices"].get(self.model, {"input": 0, "cached": 0, "output": 0})
        cost = ((tin - cached) * p["input"] + cached * p["cached"] + tout * p["output"]) / 1e6
        with self._lock:
            self.usage["calls"] += 1
            self.usage["in"] += tin
            self.usage["cached"] += cached
            self.usage["out"] += tout
            self.usage["cost"] += cost
        return tin, cached, tout

    # ------------------------------------------------------------------ offres
    @staticmethod
    def offer_prompt(o, max_chars: int) -> str:
        return (f"Entreprise : {o.company}\nIntitulé : {o.title}\nLieu : {o.location}\n"
                f"Publiée le : {o.posted_at[:10] or 'inconnu'}\n\nAnnonce :\n{o.description[:max_chars]}")

    def analyze_offers(self, db, offers: list) -> dict[str, dict]:
        """Analyse les offres absentes du cache (ou modifiées) ; renvoie {clé: fiche} pour toutes les offres données."""
        db.execute(SCHEMA_TABLE)
        oc, version = self.cfg["offers"], self.cfg["prompt_version"]
        cached, todo = {}, []
        for o in offers:
            h = text_hash(o.title, o.description[:oc["max_chars"]])
            row = db.execute("SELECT analysis, text_hash, prompt_version FROM llm_offers WHERE key=?", (o.key,)).fetchone()
            if row and row[1] == h and row[2] == version:
                cached[o.key] = json.loads(row[0])
            else:
                todo.append((o, h))
        if not self.available:
            log.info("analyse IA : pas de clé OPENAI_API_KEY, %d fiches en cache utilisées", len(cached))
            return cached
        todo = todo[:oc["max_per_run"]]
        log.info("analyse IA : %d offres en cache, %d à analyser avec %s", len(cached), len(todo), self.model)

        def work(item):
            o, h = item
            try:
                result, usage = self._call(self.offer_prompt(o, oc["max_chars"]))
                return o, h, result, self._account(usage), ""
            except Exception as e:
                with self._lock:
                    self.usage["errors"] += 1
                return o, h, None, (0, 0, 0), f"{type(e).__name__}: {e}"[:200]

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with ThreadPoolExecutor(max_workers=oc["concurrency"]) as pool:
            for o, h, result, (tin, tc, tout), err in pool.map(work, todo):
                if result is None:
                    log.debug("analyse IA échouée pour %s : %s", o.title[:60], err)
                    continue
                cached[o.key] = result
                db.execute("INSERT OR REPLACE INTO llm_offers VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                           (o.key, h, version, self.model, json.dumps(result, ensure_ascii=False), tin, tc, tout, now))
        db.commit()
        u = self.usage
        log.info("analyse IA : %d appels, %d erreurs, %d jetons en entrée (%d en cache), %d en sortie, coût %.4f $",
                 u["calls"], u["errors"], u["in"], u["cached"], u["out"], u["cost"])
        return cached
