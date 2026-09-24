"""Lecture des offres pour le "fit CV" (voir config/fit.yaml).

Côté Python, on extrait de chaque offre les compétences, domaines, métiers et langues qu'elle demande.
Le CV, lui, est lu dans le navigateur de l'élève avec le même dictionnaire (exporté dans offers.json),
et le score de fit y est calculé : le CV ne quitte jamais l'ordinateur.
"""
from __future__ import annotations

from .text import compile_terms, fold

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
        """title et text déjà passés par filters.prepare (repliés, R&D neutralisé)."""
        full = f"{title} {text}"
        domains = set(SECTOR_DOMAINS.get(sector, []))
        for did, rx in self.domains:
            # une mention isolée dans l'annonce ("mutuelle santé") ne suffit pas : 2 termes distincts, ou l'intitulé
            if rx.search(title) or len(set(rx.findall(text))) >= 2:
                domains.add(did)
        return {
            "skills": [sid for sid, rx in self.skills if rx.search(full)],
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
