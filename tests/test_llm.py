"""Tests de l'analyse IA sans appeler OpenAI : l'API est simulée.   python -m unittest discover tests"""
from __future__ import annotations

import json
import sqlite3
import unittest
from datetime import date
from pathlib import Path

import yaml

from radar.llm import LLM
from radar.models import Offer
from radar.scoring import Scorer

CONFIG = Path(__file__).resolve().parent.parent / "config"
load = lambda name: yaml.safe_load((CONFIG / name).read_text(encoding="utf-8"))


def fiche(**over) -> dict:
    base = {"is_internship": True, "is_data_role": True, "role": "ds", "domain": "agro_food",
            "required_skills": ["python", "sql"], "nice_to_have_skills": ["dataviz"], "other_skills": [],
            "degree_target": "ingenieur_ou_master", "start_month": "2027-02", "duration_months": 6,
            "languages_required": [], "data_intensity": 90, "data_reason": "Modélisation statistique quotidienne.",
            "accessibility": 82, "accessibility_reason": "Profil agro et statistique recherché.",
            "summary": "Construire des modèles de prévision des ventes."}
    return {**base, **over}


class FakeResponse:
    def __init__(self, status: int, payload: dict | None = None):
        self.status_code, self._payload, self.headers = status, payload or {}, {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    """Répond comme l'API Chat Completions ; peut refuser un modèle (404) pour tester la bascule."""
    def __init__(self, result: dict, missing_model: str | None = None):
        self.result, self.missing_model, self.calls = result, missing_model, []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append(json)
        if json["model"] == self.missing_model:
            return FakeResponse(404, {"error": {"code": "model_not_found"}})
        return FakeResponse(200, {
            "choices": [{"message": {"content": __import__("json").dumps(self.result)}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 2000, "completion_tokens": 300, "prompt_tokens_details": {"cached_tokens": 1200}},
        })


def make_llm(result: dict, missing_model: str | None = None) -> tuple[LLM, FakeSession]:
    llm = LLM(load("llm.yaml"), load("fit.yaml"), load("scoring.yaml")["school"])
    llm.key = "test"  # jamais de vraie clé dans les tests
    llm.session = FakeSession(result, missing_model)
    return llm, llm.session


def offer(**over) -> Offer:
    base = dict(source="test", company="Bonduelle", title="Stage Data Scientist - février 2027", url="https://x",
                location="Villeneuve-d'Ascq, France", country="fr", description="Stage de 6 mois en data science.",
                posted_at="2026-09-20", sector="agro", size="grande")
    return Offer(**{**base, **over})


def check_strict(schema: dict, path: str = "$") -> None:
    """Règles du mode strict d'OpenAI : tout objet liste toutes ses propriétés en required, sans propriété libre."""
    if schema.get("type") == "object" or "properties" in schema:
        assert schema.get("additionalProperties") is False, f"{path} : additionalProperties doit être false"
        assert set(schema["required"]) == set(schema["properties"]), f"{path} : toutes les propriétés en required"
        for k, v in schema["properties"].items():
            check_strict(v, f"{path}.{k}")
    if "items" in schema:
        check_strict(schema["items"], f"{path}[]")


class TestLLM(unittest.TestCase):
    def test_schema_is_strict_and_uses_dictionary_ids(self):
        llm, _ = make_llm(fiche())
        schema = llm._schema()
        check_strict(schema)
        fit_ids = {s["id"] for s in load("fit.yaml")["skills"]}
        self.assertEqual(set(schema["properties"]["required_skills"]["items"]["enum"]), fit_ids)

    def test_request_is_well_formed(self):
        llm, session = make_llm(fiche())
        llm._call("Intitulé : test")
        body = session.calls[0]
        self.assertEqual(body["response_format"]["type"], "json_schema")
        self.assertTrue(body["response_format"]["json_schema"]["strict"])
        self.assertEqual(body["reasoning_effort"], "minimal")
        self.assertNotIn("temperature", body)  # non accepté par les modèles gpt-5

    def test_cache_avoids_second_call_and_cost_is_logged(self):
        llm, session = make_llm(fiche())
        db = sqlite3.connect(":memory:")
        o = offer()
        self.assertEqual(llm.analyze_offers(db, [o])[o.key]["role"], "ds")
        self.assertEqual(len(session.calls), 1)
        self.assertGreater(llm.usage["cost"], 0)
        llm.analyze_offers(db, [o])
        self.assertEqual(len(session.calls), 1, "une offre inchangée ne doit pas être réanalysée")
        llm.analyze_offers(db, [offer(description="Texte modifié : stage de 6 mois en data science.")])
        self.assertEqual(len(session.calls), 2, "une offre modifiée doit être réanalysée")

    def test_fallback_model_when_first_is_unavailable(self):
        llm, session = make_llm(fiche(), missing_model="gpt-5-nano")
        llm._call("Intitulé : test")
        self.assertEqual(llm.model, "gpt-4.1-nano")
        self.assertNotIn("reasoning_effort", session.calls[-1])

    def test_no_key_means_no_call(self):
        llm, session = make_llm(fiche())
        llm.key = ""
        llm.analyze_offers(sqlite3.connect(":memory:"), [offer()])
        self.assertEqual(session.calls, [])


class TestScoringWithAI(unittest.TestCase):
    def setUp(self):
        self.scorer = Scorer(load("scoring.yaml"), date(2026, 9, 24))

    def test_ai_axes_and_calendar(self):
        sc = self.scorer.score(offer(), fiche())
        self.assertEqual((sc["data"], sc["profile"], sc["calendar"], sc["grade"]), (90, 82, "compatible", "A"))
        self.assertTrue(any("Analyse IA" in r[1] for r in sc["reasons"]))

    def test_start_in_2026_or_short_internship_is_out_of_calendar(self):
        self.assertEqual(self.scorer.score(offer(), fiche(start_month="2026-10"))["grade"], "X")
        self.assertEqual(self.scorer.score(offer(), fiche(start_month=None, duration_months=3))["grade"], "X")

    def test_non_data_or_non_internship_is_dropped(self):
        self.assertIsNone(self.scorer.score(offer(), fiche(is_data_role=False, data_intensity=20)))
        self.assertIsNone(self.scorer.score(offer(), fiche(is_internship=False)))

    def test_unknown_start_falls_back_to_rules(self):
        sc = self.scorer.score(offer(title="Stage Data Scientist"), fiche(start_month=None, duration_months=None))
        self.assertEqual(sc["calendar"], "a_verifier")


if __name__ == "__main__":
    unittest.main()
