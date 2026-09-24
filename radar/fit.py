"""Lecture des offres pour le "fit CV" (voir config/fit.yaml).

Côté Python, on extrait de chaque offre, sans IA, les compétences (exigées / appréciées), domaines, métiers et
langues qu'elle demande. Le CV est lu dans le navigateur de l'élève ; seul son texte part au serveur (cv-worker/,
route /cv) qui en tire les filtres du profil avec un petit modèle, limité aux identifiants de ce dictionnaire.
Le score de fit est ensuite calculé dans le navigateur, sans IA, avec les poids de config/fit.yaml.
"""
from __future__ import annotations

import re

from .text import compile_terms, fold

SENTENCE = re.compile(r"(?<=[.;!?•])\s+|\s+-\s+|\s+•\s+")
NICE = compile_terms(["un plus", "serait un plus", "est un plus", "apprecie*", "souhaite*", "idealement", "nice to have",
                      "is a plus", "would be a plus", "a plus", "bonus", "atout", "optionnel*", "de preference"])

# Secteur de l'entreprise (config) -> domaines du dictionnaire
SECTOR_DOMAINS = {"agro": ["agro_food", "agri"], "sport": ["sport"], "luxe": ["luxe"], "industrie": ["industry"],
                  "conseil": ["consulting"]}


class FitLexicon:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.skills = [(s["id"], compile_terms(s["terms"])) for s in cfg["skills"]]
        self.domains = [(d["id"], compile_terms(d["terms"])) for d in cfg["domains"]]
        self.roles = [(r["id"], compile_terms(r["terms"])) for r in cfg["roles"]]
        self.langs = [(l["id"], compile_terms(l["offer"])) for l in cfg["languages"]]

    def offer_features(self, title: str, text: str, sector: str) -> dict:
        """title et text déjà passés par filters.prepare (repliés, R&D neutralisé).

        Une compétence citée dans une phrase « un plus / apprécié / idéalement / nice to have » est rangée dans
        les compétences appréciées ; ailleurs, elle est considérée comme exigée."""
        full = f"{title} {text}"
        sentences = SENTENCE.split(text)
        nice_text = " ".join(x for x in sentences if NICE.search(x))
        must_text = " ".join([title] + [x for x in sentences if not NICE.search(x)])
        domains = set(SECTOR_DOMAINS.get(sector, []))
        for did, rx in self.domains:
            # une mention isolée dans l'annonce ("mutuelle santé") ne suffit pas : 2 termes distincts, ou l'intitulé
            if rx.search(title) or len(set(rx.findall(text))) >= 2:
                domains.add(did)
        must = [sid for sid, rx in self.skills if rx.search(must_text)]
        return {
            "skills": must,
            "nice": [sid for sid, rx in self.skills if sid not in must and rx.search(nice_text)],
            "domains": sorted(domains),
            "roles": [rid for rid, rx in self.roles if rx.search(title)],
            "langs": [lid for lid, rx in self.langs if rx.search(full)],
        }

    def export(self) -> dict:
        """Dictionnaire envoyé au navigateur (termes déjà repliés, même syntaxe '*' qu'en Python)."""
        norm = lambda items, key="terms": [dict(i, **{key: [fold(t) for t in i[key]]}) for i in items]
        return {
            "weights": self.cfg["weights"], "levels": self.cfg["levels"],
            "skills": norm(self.cfg["skills"]), "domains": norm(self.cfg["domains"]), "roles": norm(self.cfg["roles"]),
            "languages": [dict(l, offer=[fold(t) for t in l["offer"]], cv=[fold(t) for t in l["cv"]])
                          for l in self.cfg["languages"]],
        }
