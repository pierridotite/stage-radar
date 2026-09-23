from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field

from .text import slug


@dataclass
class Offer:
    source: str                 # smartrecruiters, workday, adzuna, manual...
    company: str
    title: str
    url: str
    location: str = ""
    country: str = ""           # code ISO2 en minuscules quand la source le donne ("fr")
    description: str = ""       # texte brut, sans HTML
    posted_at: str = ""         # ISO 8601 (date seule acceptée)
    sector: str = ""            # data, agro, luxe, sport, conseil, industrie (vide = à classer)
    size: str = ""              # startup, pme, eti, grande (vide = à déterminer via l'annuaire des entreprises)
    contract_hint: str = ""     # ce que la source dit du contrat ("Internship", "Stage"...)
    extra: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        """Identifiant stable pour dédoublonner entre sources et entre jours."""
        city = slug(self.location.split(",")[0]) if self.location else ""
        base = f"{slug(self.company)}|{slug(self.title)}|{city}"
        return hashlib.sha1(base.encode()).hexdigest()[:16]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["key"] = self.key
        return d
