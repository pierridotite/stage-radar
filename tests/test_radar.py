"""Tests hors ligne : notation par règles, lecture des offres pour le fit, Adzuna simulé.   python -m unittest"""
import os
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

import yaml

from radar.filters import prepare
from radar.fit import FitLexicon
from radar.models import Offer
from radar.scoring import Scorer
from radar.sources.aggregators import adzuna

ROOT = Path(__file__).resolve().parent.parent
load = lambda name: yaml.safe_load((ROOT / "config" / name).read_text(encoding="utf-8"))
TODAY = date(2026, 9, 24)


def offer(title, description, **kw):
    return Offer(source="test", company=kw.pop("company", "Ferme Numérique"), title=title,
                 url="https://example.org/o", location="Rennes", country="fr", description=description,
                 posted_at="2026-09-20", **kw)


class ScoringTest(unittest.TestCase):
    scorer = Scorer(load("scoring.yaml"), TODAY)

    def test_stage_data_bien_note(self):
        o = offer("Stage fin d'études Data Scientist", "Stage de 6 mois, début février 2027. Python, SQL, "
                  "machine learning et statistiques appliqués aux données agricoles et agronomiques.", size="pme")
        sc = self.scorer.score(o)
        self.assertIsNotNone(sc)
        self.assertEqual(sc["calendar"], "compatible")
        self.assertIn(sc["grade"], "AB")
        self.assertTrue(all(len(r) == 4 for r in sc["reasons"]), "raison = [points, libellé, termes, axe]")

    def test_stage_hors_data_ecarte(self):
        self.assertIsNone(self.scorer.score(offer("Stage commercial terrain", "Prospection et vente en magasin.")))

    def test_debut_trop_tot_hors_calendrier(self):
        o = offer("Stage Data Analyst", "Début septembre 2026 pour 6 mois. Python, SQL, Power BI.")
        sc = self.scorer.score(o)
        self.assertEqual((sc["calendar"], sc["grade"]), ("hors_calendrier", "X"))

    def test_date_hors_contexte_ignoree(self):
        o = offer("Stage Data Scientist 6 mois", "Équipe renforcée depuis septembre 2026. Python, SQL, statistiques.")
        self.assertNotEqual(self.scorer.score(o)["calendar"], "hors_calendrier")


class FitTest(unittest.TestCase):
    lexicon = FitLexicon(load("fit.yaml"))

    def features(self, title, text, sector="data"):
        return self.lexicon.offer_features(prepare(title), prepare(text), sector)

    def test_competences_exigees_et_appreciees(self):
        f = self.features("Stage Data Scientist", "Maîtrise de Python et SQL. Une connaissance de Spark serait un plus.")
        self.assertIn("python", f["skills"])
        self.assertIn("sql", f["skills"])
        self.assertNotIn("spark", f["skills"])
        self.assertIn("spark", f["nice"])
        self.assertIn("ds", f["roles"])

    def test_r_et_rnd(self):
        self.assertNotIn("r", self.features("Stage R&D", "Projet de recherche et développement.")["skills"])
        self.assertIn("r", self.features("Stage statisticien", "Analyses sous R et Python.")["skills"])

    def test_secteur_de_l_entreprise_compte_comme_domaine(self):
        self.assertIn("agro_food", self.features("Stage data", "Tableaux de bord.", sector="agro")["domains"])

    def test_export_navigateur(self):
        lex = self.lexicon.export()
        self.assertEqual(set(lex), {"weights", "levels", "skills", "domains", "roles", "languages"})
        self.assertTrue(all(t == t.lower() for s in lex["skills"] for t in s["terms"]))


class AdzunaTest(unittest.TestCase):
    def fake_session(self, pages):
        calls = []

        class S:
            def get(self, url, params):
                calls.append(params["what"])
                r = mock.Mock(status_code=200)
                r.json.return_value = {"results": pages.get(params["what"], [])}
                return r
        return S(), calls

    @staticmethod
    def job(company, title="Stage Data Analyst H/F", description="Stage de 6 mois en data.", contract="internship"):
        return {"title": title, "description": description, "company": {"display_name": company},
                "location": {"display_name": "Paris"}, "redirect_url": "https://adzuna.example/1",
                "created": "2026-09-20T10:00:00Z", "contract_type": contract}

    def test_sans_cle_rien_n_est_appele(self):
        s, calls = self.fake_session({})
        with mock.patch.dict(os.environ, {"ADZUNA_APP_ID": "", "ADZUNA_APP_KEY": ""}):
            self.assertEqual(adzuna(["stage data"], [], s), [])
        self.assertEqual(calls, [])

    def test_groupes_cibles_seules_leurs_annonces(self):
        s, calls = self.fake_session({"stage L'Oréal": [
            self.job("L’ORÉAL"), self.job("Cabinet Conseil (client L'Oréal)"), self.job("L'Oréal", "Directeur marketing", "CDI", "permanent")]})
        companies = [{"name": "L'Oréal", "match": ["oreal"], "sector": "luxe", "size": "grande"}]
        with mock.patch.dict(os.environ, {"ADZUNA_APP_ID": "x", "ADZUNA_APP_KEY": "y"}):
            found = adzuna([], companies, s)
        self.assertEqual(calls, ["stage L'Oréal"])
        self.assertEqual(len(found), 1, "cabinet tiers et poste hors stage écartés")
        self.assertEqual((found[0].company, found[0].sector, found[0].size), ("L'Oréal", "luxe", "grande"))
        s, _ = self.fake_session({"stage Safran": [self.job("Safran Aircraft Engines"), self.job("Hays pour Safran")]})
        with mock.patch.dict(os.environ, {"ADZUNA_APP_ID": "x", "ADZUNA_APP_KEY": "y"}):
            found = adzuna([], [{"name": "Safran", "match": ["safran"], "sector": "industrie", "size": "grande"}], s)
        self.assertEqual([o.company for o in found], ["Safran"])

    def test_configuration_des_groupes(self):
        for c in load("sources.yaml")["adzuna_companies"]:
            self.assertTrue(c["match"] and c["sector"] and c["size"], c)


class PublicationTest(unittest.TestCase):
    def test_page_sans_secret_ni_recherche_manuelle(self):
        page = (ROOT / "radar" / "dashboard.html").read_text(encoding="utf-8")
        self.assertIn("__LOGO__", page)
        for bad in ("sk-", "OPENAI_API_KEY", "TAVILY_API_KEY", "linkedin.com/search"):
            self.assertNotIn(bad, page)

    def test_criteres_de_contacts_connus_de_la_page(self):
        page = (ROOT / "radar" / "dashboard.html").read_text(encoding="utf-8")
        for c in load("network.yaml")["criteria"]:
            self.assertIn(f'"{c["id"]}"', page)

    def test_serveur_configure(self):
        self.assertTrue(load("server.yaml")["worker_url"].startswith("https://"))


if __name__ == "__main__":
    unittest.main()
