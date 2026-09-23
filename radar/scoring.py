"""Scores : pertinence data, accessibilité Institut Agro, et match avec chaque profil de la promo.

Chaque score renvoie aussi ses raisons, affichées telles quelles dans le tableau de bord :
un score qu'on ne peut pas expliquer ne sert à rien pour décider où candidater.
"""
from __future__ import annotations

from datetime import date, datetime

from .filters import prepare, zone
from .models import Offer
from .text import compile_terms, fold


def _days_since(iso: str, today: date) -> int | None:
    if not iso:
        return None
    try:
        return (today - datetime.fromisoformat(iso.replace("Z", "+00:00")[:25]).date()).days
    except ValueError:
        try:
            return (today - date.fromisoformat(iso[:10])).days
        except ValueError:
            return None


class Scorer:
    def __init__(self, scoring_cfg: dict, profiles_cfg: dict, today: date | None = None):
        self.today = today or date.today()
        rel = scoring_cfg["relevance"]
        self.rel_min = rel["min_score"]
        self.rel_title = compile_terms(rel["title_terms"])
        self.rel_desc = compile_terms(rel["description_terms"])
        self.rel_exclude = compile_terms(rel.get("title_exclude") or ["__none__"])

        acc = scoring_cfg["accessibility"]
        self.acc = acc
        self.rules = [dict(r, rx=compile_terms(r["terms"])) for r in acc["rules"]]
        self.network_rx = compile_terms(acc["network_companies"]["names"])
        self.demand_rx = compile_terms(acc["high_demand_companies"]["names"])

        self.profiles = {"promo": profiles_cfg["promo"], **profiles_cfg.get("students", {})}
        for p in self.profiles.values():
            p["_kw"] = compile_terms(p.get("keywords") or ["__none__"])
            p["_strong"] = compile_terms(p.get("strong") or ["__none__"])

    # ------------------------------------------------------------ pertinence
    def relevance(self, title: str, text: str) -> int:
        if self.rel_exclude.search(title):
            return 0
        t_hits = set(self.rel_title.findall(title))
        d_hits = set(self.rel_desc.findall(text))
        score = (60 if t_hits else 0) + min(10 * len(d_hits), 40)
        return min(score, 100)

    # ------------------------------------------------------------ accessibilité
    def accessibility(self, o: Offer, title: str, text: str, zone_: str) -> tuple[int, list[list]]:
        acc, full = self.acc, f"{title} {text}"
        reasons: list[list] = []

        for r in self.rules:
            found = set(r["rx"].findall(full))
            if not found:
                continue
            if "per_match" in r:
                pts = r["per_match"] * len(found)
                pts = max(pts, r["cap"]) if r["cap"] < 0 else min(pts, r["cap"])
            else:
                pts = r["points"]
            reasons.append([pts, r["label"], sorted(found)[:4]])

        sp = acc["sector_points"].get(o.sector, 0)
        if sp:
            reasons.append([sp, f"Secteur {o.sector} : réseau de l'école", []])

        company = fold(o.company)
        if self.network_rx.search(company):
            reasons.append([acc["network_companies"]["points"], "Entreprise qui recrute déjà dans l'école", []])
        if self.demand_rx.search(company):
            reasons.append([acc["high_demand_companies"]["points"], "Marque très convoitée : forte concurrence", []])

        age = _days_since(o.posted_at, self.today)
        fr = acc["freshness"]
        if age is not None and age <= fr["new_days"]:
            reasons.append([fr["new_points"], f"Publiée il y a {max(age, 0)} j", []])
        elif age is not None and age >= fr["stale_days"]:
            reasons.append([fr["stale_points"], f"Publiée il y a {age} j : peut-être pourvue", []])

        score = max(5, min(98, acc["base"] + sum(r[0] for r in reasons)))
        reasons.sort(key=lambda r: -abs(r[0]))
        return score, reasons

    def grade(self, score: int) -> str:
        g = self.acc["grades"]
        return "A" if score >= g["A"] else "B" if score >= g["B"] else "C" if score >= g["C"] else "D"

    # ------------------------------------------------------------ match profil
    def matches(self, o: Offer, title: str, text: str, zone_: str) -> dict[str, int]:
        out = {}
        for pid, p in self.profiles.items():
            if pid == "promo":
                continue
            strong_t, strong_d = set(p["_strong"].findall(title)), set(p["_strong"].findall(text))
            kw_t, kw_d = set(p["_kw"].findall(title)), set(p["_kw"].findall(text))
            s = 25 if o.sector in p.get("sectors", []) else 0
            s += min(20 * len(strong_t) + 8 * len(strong_d - strong_t), 45)
            s += min(8 * len(kw_t) + 3 * len(kw_d - kw_t), 30)
            out[pid] = max(0, min(100, s))
        return out

    # ------------------------------------------------------------ tout
    def score(self, o: Offer) -> dict | None:
        """Renvoie None si l'offre n'est pas un stage data (écartée)."""
        title = prepare(o.title)
        text = prepare(o.description)
        rel = self.relevance(title, text)
        if rel < self.rel_min:
            return None
        z = zone(o.country, o.location)
        acc, reasons = self.accessibility(o, title, text, z)
        return {
            "relevance": rel, "zone": z, "accessibility": acc, "grade": self.grade(acc),
            "reasons": reasons, "match": self.matches(o, title, text, z),
            "age_days": _days_since(o.posted_at, self.today),
        }
