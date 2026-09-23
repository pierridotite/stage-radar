"""Petites fonctions de nettoyage de texte partagées par toutes les sources."""
from __future__ import annotations

import html
import re
import unicodedata

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def strip_html(raw: str | None) -> str:
    if not raw:
        return ""
    text = _TAG_RE.sub(" ", raw)
    text = html.unescape(text)
    return _WS_RE.sub(" ", text).strip()


def fold(text: str | None) -> str:
    """Minuscules sans accents : sert à toutes les recherches de mots-clés."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return _WS_RE.sub(" ", text.lower()).strip()


def slug(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "-", fold(text)).strip("-")


def compile_terms(terms: list[str]) -> re.Pattern:
    """Compile une liste de mots-clés (déjà écrits sans accents) en une regex à frontières de mots.

    Un terme qui finit par '*' est un préfixe : 'statisti*' couvre statistique, statistician...
    """
    parts = []
    for term in terms:
        term = fold(term)
        if term.endswith("*"):
            parts.append(re.escape(term[:-1]) + r"\w*")
        else:
            parts.append(re.escape(term))
    return re.compile(r"(?<![\w+#])(?:" + "|".join(parts) + r")(?![\w+#])")
