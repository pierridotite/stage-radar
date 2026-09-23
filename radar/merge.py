"""Fusionne des listes d'entreprises (sorties de radar.discover ou recherches) dans config/companies.yaml.

    python -m radar.merge nouvelles.yaml [autres.yaml ...]

Dédoublonne par nom normalisé et par flux (même ATS + même identifiant). Une entrée existante non collectée
(wttj / unsupported) est remplacée si le nouveau fichier apporte un flux collectable pour la même entreprise.
Les nouvelles lignes sont ajoutées en fin de fichier, sous un commentaire daté ; le reste du fichier est intact.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import yaml

from .sources.ats import FETCHERS
from .text import slug

PATH = Path(__file__).resolve().parent.parent / "config" / "companies.yaml"
KEY_ORDER = ["name", "sector", "size", "ats", "id", "host", "site", "url", "base", "flavor", "region", "country",
             "platform", "enabled"]


def name_key(c: dict) -> str:
    return slug(c["name"].split("(")[0]).replace("-", "")


def feed_key(c: dict) -> tuple:
    ident = c.get("id") or c.get("url") or f"{c.get('host')}/{c.get('site')}"
    return (c.get("ats"), str(ident).lower())


def collectable(c: dict) -> bool:
    return c.get("ats") in FETCHERS and c.get("enabled", True)


def flow(c: dict) -> str:
    keys = [k for k in KEY_ORDER if k in c] + [k for k in c if k not in KEY_ORDER and not k.startswith("_")]
    return "  - " + yaml.safe_dump({k: c[k] for k in keys}, default_flow_style=True, allow_unicode=True,
                                   width=1000, sort_keys=False).strip()


def main(argv=None):
    files = argv or sys.argv[1:]
    text = PATH.read_text(encoding="utf-8")
    existing = yaml.safe_load(text)["companies"]
    by_name = {name_key(c): c for c in existing}
    feeds = {feed_key(c) for c in existing if collectable(c)}
    added, replaced = [], []

    for f in files:
        for c in yaml.safe_load(Path(f).read_text(encoding="utf-8")).get("companies", []):
            c = {k: v for k, v in c.items() if k not in ("verified_jobs", "notes")}
            nk, fk = name_key(c), feed_key(c)
            if collectable(c) and fk in feeds:
                continue
            old = by_name.get(nk)
            if old and (collectable(old) or not collectable(c)):
                continue
            if old:  # l'entrée connue n'était pas collectée : on la désactive, la nouvelle prend le relais
                replaced.append(old["name"])
                text = text.replace(flow_line(text, old), "", 1)
            added.append(c)
            by_name[nk] = c
            if collectable(c):
                feeds.add(fk)

    if added:
        text = text.rstrip() + f"\n\n  # ------------------------------------------------------------------ ajouts du {date.today()}\n"
        text += "\n".join(flow(c) for c in sorted(added, key=lambda c: (c.get("sector", ""), c["name"]))) + "\n"
        PATH.write_text(text, encoding="utf-8")
    print(f"{len(added)} entreprises ajoutées ({sum(map(collectable, added))} collectables), "
          f"{len(replaced)} entrées non collectées remplacées : {', '.join(replaced) or '-'}")


def flow_line(text: str, c: dict) -> str:
    """Retrouve la ligne d'origine d'une entreprise (format '- {name: X, ...}') pour pouvoir la retirer."""
    for line in text.splitlines(keepends=True):
        s = line.strip()
        try:
            if s.startswith("- {") and yaml.safe_load(s[2:]).get("name") == c["name"]:
                return line
        except yaml.YAMLError:
            continue
    return "\0"  # introuvable (entrée sur plusieurs lignes) : on la laisse


if __name__ == "__main__":
    main()
