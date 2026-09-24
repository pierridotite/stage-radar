"""Notation des offres selon trois axes indépendants, plus un filtre calendrier (voir config/scoring.yaml).

    DATA        (0-100)  est-ce un vrai poste data ?
    PROFIL      (0-100)  un·e élève data science de l'Institut Agro correspond-il à ce qui est demandé ?
    CONCURRENCE          faible / moyenne / forte
    CALENDRIER           compatible / à vérifier / hors calendrier (début février 2027, 6 mois)

Chaque point attribué est accompagné de sa raison et de son axe, affichés tels quels dans le tableau de bord :
une note qu'on ne peut pas expliquer ne sert à rien pour décider où candidater.
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


def _rx(terms: list[str] | None):
    return compile_terms(terms or ["__none__"])


def _capped(found: set, per_match: int, cap: int) -> int:
    pts = per_match * len(found)
    return max(pts, cap) if cap < 0 else min(pts, cap)


class Scorer:
    def __init__(self, cfg: dict, today: date | None = None):
        self.today = today or date.today()
        self.cfg = cfg
        d, p, c, k = cfg["data"], cfg["profile"], cfg["competition"], cfg["calendar"]

        self.d_keep = d["min_to_keep"]
        self.d_strong, self.d_weak = _rx(d["title_strong"]["terms"]), _rx(d["title_weak"]["terms"])
        self.d_skills, self.d_not = _rx(d["skills"]["terms"]), _rx(d["not_data"]["terms"])
        self.d_exclude = _rx(d.get("title_exclude"))

        self.p_rules = [dict(r, rx=_rx(r["terms"])) for r in p["rules"]]
        self.p_known, self.p_missing = _rx(p["skills_known"]["terms"]), _rx(p["skills_missing"]["terms"])
        bs = p["business_school"]
        self.bs_rx, self.bs_alt = _rx(bs["terms"]), _rx(bs["alternatives"])

        self.c_network, self.c_demand = _rx(c["network_companies"]), _rx(c["high_demand_companies"])
        self.c_elite = _rx(c["elite_targeting"]["terms"])

        self.k_ok, self.k_early = _rx(k["compatible"]), _rx(k["too_early"])
        self.k_short, self.k_long = _rx(k["short"]), _rx(k["long_ok"])
        self.k_ctx, self.k_dur = _rx(k["start_context"]), _rx(k["duration_context"])


    # ------------------------------------------------------------ DATA
    def data_axis(self, title: str, text: str) -> tuple[int, list]:
        d, reasons = self.cfg["data"], []
        if self.d_exclude.search(title):
            return 0, [[0, "Intitulé hors data (juridique, RH, vente, contenu...)", [], "data"]]
        strong, weak = set(self.d_strong.findall(title)), set(self.d_weak.findall(title))
        if strong:
            reasons.append([d["title_strong"]["points"], "Intitulé de poste data", sorted(strong)[:3], "data"])
        elif weak:
            reasons.append([d["title_weak"]["points"], "Intitulé d'analyse, data à confirmer", sorted(weak)[:3], "data"])
        skills = set(self.d_skills.findall(f"{title} {text}"))
        if skills:
            reasons.append([_capped(skills, d["skills"]["per_match"], d["skills"]["cap"]),
                            f"{len(skills)} compétence(s) data demandée(s)", sorted(skills)[:5], "data"])
        lab_title, lab_text = set(self.d_not.findall(title)), set(self.d_not.findall(text))
        if lab_title or len(lab_text) >= 2:
            reasons.append([d["not_data"]["points"], "Travail de paillasse, de terrain ou de vente",
                            sorted(lab_title | lab_text)[:3], "data"])
        return max(0, min(100, sum(r[0] for r in reasons))), reasons

    # ------------------------------------------------------------ PROFIL
    def _business_only(self, text: str) -> bool:
        """Vrai si l'annonce ne cite QUE l'école de commerce (aucune alternative à proximité)."""
        hits = list(self.bs_rx.finditer(text))
        if not hits:
            return False
        w = self.cfg["profile"]["business_school"]["window"]
        return not any(self.bs_alt.search(text[max(0, m.start() - w):m.end() + w]) for m in hits)

    def profile_axis(self, o: Offer, title: str, text: str) -> tuple[int, list]:
        p, full, reasons = self.cfg["profile"], f"{title} {text}", []
        for r in self.p_rules:
            found = set(r["rx"].findall(full))
            if not found and r["label"].startswith("Domaine") and o.sector == "agro":
                found = {"secteur agro"}
            if found:
                reasons.append([r["points"], r["label"], sorted(found)[:4], "profil"])
        known, missing = set(self.p_known.findall(full)), set(self.p_missing.findall(full))
        if known:
            sk = p["skills_known"]
            reasons.append([_capped(known, sk["per_match"], sk["cap"]), "Outils que la promo maîtrise",
                            sorted(known)[:5], "profil"])
        if missing:
            sm = p["skills_missing"]
            reasons.append([_capped(missing, sm["per_match"], sm["cap"]), "Outils hors cursus demandés",
                            sorted(missing)[:5], "profil"])
        if self._business_only(text):
            bs = p["business_school"]
            reasons.append([bs["only_points"], bs["label_only"], [], "profil"])
        return max(0, min(100, p["base"] + sum(r[0] for r in reasons))), reasons

    # ------------------------------------------------------------ CONCURRENCE
    def competition_axis(self, o: Offer, text: str) -> tuple[str, list]:
        c, company = self.cfg["competition"], fold(o.company)
        elite = set(self.c_elite.findall(text))
        if self.c_demand.search(company):
            level, why, terms = "forte", "Marque très convoitée", []
        elif elite:
            level, why, terms = "forte", "Annonce qui cible les écoles les plus sélectives", sorted(elite)[:3]
        elif self.c_network.search(company):
            level, why, terms = "faible", "Entreprise qui recrute déjà à l'Institut Agro", []
        elif o.size in c["small_sizes"]:
            label = {"pme": "PME", "startup": "Startup", "public": "Structure publique ou de recherche"}[o.size]
            level, why, terms = "faible", f"{label} : moins de candidats", []
        else:
            level, why, terms = "moyenne", "Concurrence habituelle", []
        return level, [[c["points"][level], why, terms, "concurrence"]]

    # ------------------------------------------------------------ CALENDRIER
    def _in_context(self, rx, ctx, title: str, text: str) -> set:
        """Termes trouvés dans l'intitulé, ou dans l'annonce juste après un mot de contexte (début, durée...)."""
        w = self.cfg["calendar"]["context_window"]
        found = set(rx.findall(title))
        for m in rx.finditer(text):
            if "2027" in m.group(0) or ctx.search(text[max(0, m.start() - w):m.start()]):
                found.add(m.group(0))
        return found

    def calendar_axis(self, o: Offer, title: str, text: str) -> tuple[str, list]:
        k, full = self.cfg["calendar"], f"{title} {text}"
        ok = self._in_context(self.k_ok, self.k_ctx, title, text)
        early = self._in_context(self.k_early, self.k_ctx, title, text)
        short = self._in_context(self.k_short, self.k_dur, title, text)
        long_ = set(self.k_long.findall(full))
        if early and not ok:
            status = "hors_calendrier"
            reason = [0, "Début en 2026 : avant la fin des cours", sorted(early)[:2], "calendrier"]
        elif short and not long_:
            status = "hors_calendrier"
            reason = [0, "Stage court, pas un stage de fin d'études", sorted(short)[:2], "calendrier"]
        elif ok:
            status = "compatible"
            reason = [k["points"]["compatible"], "Début compatible avec février 2027", sorted(ok)[:2], "calendrier"]
        else:
            status = "a_verifier"
            reason = [k["points"]["a_verifier"], "Date de début non précisée : à vérifier", [], "calendrier"]
        reasons = [reason]
        age = _days_since(o.posted_at, self.today)
        if age is not None and age >= k["stale_days"]:
            reasons.append([k["stale_points"], f"Annonce publiée il y a {age} jours : peut-être pourvue", [],
                            "calendrier"])
        return status, reasons

    # ------------------------------------------------------------ note finale
    def grade(self, score: int, data: int, profile: int, calendar: str) -> str:
        if calendar == "hors_calendrier":
            return "X"
        g = self.cfg["grades"]
        if score >= g["A"]["score"] and data >= g["A"]["min_data"] and profile >= g["A"]["min_profile"]:
            return "A"
        if score >= g["B"]["score"] and data >= g["B"]["min_data"]:
            return "B"
        return "C" if score >= g["C"] else "D"

    # ------------------------------------------------------------ axes issus de l'analyse IA (radar/llm.py)
    def ai_calendar(self, ai: dict) -> tuple[str, list] | None:
        """Calendrier d'après la date de début et la durée extraites par le modèle ; None si non précisées."""
        start, months = ai.get("start_month"), ai.get("duration_months")
        k = self.cfg["calendar"]
        if months is not None and months <= 4:
            return "hors_calendrier", [[0, f"Stage de {months} mois : pas un stage de fin d'études", [], "calendrier"]]
        if not start or len(start) < 7:
            return None
        if start < "2027-01":
            return "hors_calendrier", [[0, f"Début annoncé en {start} : avant la fin des cours", [], "calendrier"]]
        if start <= "2027-04":
            return "compatible", [[k["points"]["compatible"], f"Début {start} : compatible avec février 2027", [],
                                   "calendrier"]]
        return "a_verifier", [[0, f"Début {start} : plus tard que prévu, à vérifier", [], "calendrier"]]

    def score(self, o: Offer, ai: dict | None = None) -> dict | None:
        """Renvoie None si l'offre n'est pas un stage data (écartée du tableau de bord).

        Avec une fiche IA : les axes Data et Profil et, si elle est connue, la date de début viennent du modèle ;
        la concurrence (listes d'entreprises) et l'ancienneté de l'annonce restent calculées par les règles."""
        title, text = prepare(o.title), prepare(o.description)
        if ai:
            if not ai["is_internship"] or (not ai["is_data_role"] and ai["data_intensity"] < self.d_keep):
                return None
            data = max(0, min(100, int(ai["data_intensity"])))
            profile = max(0, min(100, int(ai["accessibility"])))
            if data < self.d_keep:
                return None
            r_data = [[0, f"Analyse IA : {ai['data_reason']}", [], "data"]]
            r_prof = [[0, f"Analyse IA : {ai['accessibility_reason']}", [], "profil"]]
        else:
            data, r_data = self.data_axis(title, text)
            if data < self.d_keep:
                return None
            profile, r_prof = self.profile_axis(o, title, text)
        comp, r_comp = self.competition_axis(o, text)
        cal, r_cal = self.calendar_axis(o, title, text)
        if ai and (ai_cal := self.ai_calendar(ai)):
            stale = [r for r in r_cal if "peut-être pourvue" in r[1]]
            cal, r_cal = ai_cal[0], ai_cal[1] + stale
        score = max(0, min(100, round((data + profile) / 2 + sum(r[0] for r in r_comp + r_cal))))
        return {
            "score": score, "grade": self.grade(score, data, profile, cal),
            "data": data, "profile": profile, "competition": comp, "calendar": cal,
            "zone": zone(o.country, o.location), "reasons": r_data + r_prof + r_comp + r_cal,
            "age_days": _days_since(o.posted_at, self.today), "_title": title, "_text": text,
        }
