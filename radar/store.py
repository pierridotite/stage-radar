"""Historique SQLite : permet de savoir quelles offres sont nouvelles et lesquelles ont disparu."""
from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from .models import Offer

SCHEMA = """
CREATE TABLE IF NOT EXISTS offers (
    key TEXT PRIMARY KEY, source TEXT, company TEXT, title TEXT, url TEXT, location TEXT, country TEXT,
    description TEXT, posted_at TEXT, sector TEXT, size TEXT, contract_hint TEXT,
    first_seen TEXT, last_seen TEXT, active INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS runs (
    day TEXT, source TEXT, company TEXT, found INTEGER, error TEXT
);
"""
FIELDS = ["source", "company", "title", "url", "location", "country", "description", "posted_at", "sector", "size",
          "contract_hint"]


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def upsert(self, offers: list[Offer], today: date) -> int:
        """Insère ou rafraîchit les offres ; renvoie le nombre de nouvelles."""
        d = today.isoformat()
        new = 0
        for o in offers:
            row = self.db.execute("SELECT 1 FROM offers WHERE key=?", (o.key,)).fetchone()
            values = [getattr(o, f) for f in FIELDS]
            if row:
                sets = ", ".join(f"{f}=?" for f in FIELDS)
                self.db.execute(f"UPDATE offers SET {sets}, last_seen=?, active=1 WHERE key=?", [*values, d, o.key])
            else:
                new += 1
                self.db.execute(
                    f"INSERT INTO offers (key, {', '.join(FIELDS)}, first_seen, last_seen, active) "
                    f"VALUES (?, {', '.join('?' * len(FIELDS))}, ?, ?, 1)", [o.key, *values, d, d])
        self.db.commit()
        return new

    def expire(self, today: date, grace_days: int = 3) -> int:
        """Une offre absente des flux depuis plus de `grace_days` jours est considérée pourvue/retirée.
        Le délai de grâce évite de tout perdre quand une source est en panne une journée."""
        cutoff = (today - timedelta(days=grace_days)).isoformat()
        cur = self.db.execute("UPDATE offers SET active=0 WHERE active=1 AND last_seen < ?", (cutoff,))
        self.db.commit()
        return cur.rowcount

    def log_run(self, today: date, source: str, company: str, found: int, error: str = ""):
        self.db.execute("INSERT INTO runs VALUES (?, ?, ?, ?, ?)", (today.isoformat(), source, company, found, error))
        self.db.commit()

    def active(self) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM offers WHERE active=1").fetchall()


def offer_from_row(row: sqlite3.Row) -> Offer:
    return Offer(**{f: row[f] or "" for f in FIELDS})


def dump_json(path: Path, payload: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
