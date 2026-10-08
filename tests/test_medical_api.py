import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# Forcer auth optionnelle + secret stable avant imports app
os.environ["MEDICAL_AUTH_OPTIONAL"] = "1"
os.environ["MEDICAL_AUTH_SECRET"] = "test-secret"
os.environ["SENTINELLE_DISABLE_SCHEDULER"] = "1"


class TestAuth(unittest.TestCase):
    def setUp(self):
        from bot.auth import AuthStore, create_token, decode_token, hash_password, verify_password
        self.AuthStore = AuthStore
        self.create_token = create_token
        self.decode_token = decode_token
        self.hash_password = hash_password
        self.verify_password = verify_password
        self.tmp = tempfile.TemporaryDirectory()
        self.store = AuthStore(path=Path(self.tmp.name) / "auth.db")

    def tearDown(self):
        try:
            self.store.close()
        except Exception:
            pass
        try:
            self.tmp.cleanup()
        except Exception:
            pass

    def test_password_roundtrip(self):
        stored = self.hash_password("secret123")
        self.assertTrue(self.verify_password("secret123", stored))
        self.assertFalse(self.verify_password("wrong", stored))

    def test_register_login_token(self):
        user = self.store.register("dr@hopital.cm", "LIC-12345", "motdepasse", "Dr Test")
        self.assertEqual(user["email"], "dr@hopital.cm")
        auth = self.store.authenticate("dr@hopital.cm", "motdepasse", "LIC-12345")
        self.assertIsNotNone(auth)
        token = self.create_token({"sub": auth["email"], "license": auth["license_number"], "uid": auth["id"]})
        payload = self.decode_token(token)
        self.assertEqual(payload["sub"], "dr@hopital.cm")


class TestCheatSheetGuards(unittest.TestCase):
    def test_refuse_poor_source(self):
        from bot.ai import generate_medical_cheat_sheet
        self.assertIsNone(generate_medical_cheat_sheet("Titre", "court", "x", nct_id="NCT1"))

    def test_calls_llm_when_rich(self):
        from bot import ai
        with mock.patch.object(ai, "get_gemini_key", return_value="fake"):
            with mock.patch.object(ai, "call_llm", return_value="### MÉMO\n* **Référence** : NCT999"):
                out = ai.generate_medical_cheat_sheet(
                    "Essai malaria",
                    "A" * 80,
                    "B" * 80,
                    nct_id="NCT999",
                    phase="PHASE2",
                    status="RECRUITING",
                    sponsor="WHO",
                )
                self.assertIn("NCT999", out)


class TestMedicalApi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ.pop("DATABASE_URL", None)
        from bot.medical_store import MedicalStore
        self.db = Path(self.tmp.name) / "med.db"
        # Tests hermétiques : pas d'appel réel à l'API d'embeddings
        self._ge_patch = mock.patch("bot.medical_store.get_embedding", return_value=None)
        self._ge_patch.start()
        self.addCleanup(self._ge_patch.stop)
        self.store = MedicalStore(path=self.db)
        self.store.upsert_record({
            "id": "NCTTEST1",
            "title": "Malaria trial Yaounde",
            "source": "clinicaltrials",
            "nct_id": "NCTTEST1",
            "url": "https://clinicaltrials.gov/study/NCTTEST1",
            "summary": "Study on malaria efficacy in Cameroon adults",
            "eligibility_criteria": "Adults over 18 with confirmed malaria",
            "phase": "PHASE2",
            "status": "RECRUITING",
            "conditions": "Malaria",
            "sponsor": "WHO",
            "location_name": "Yaounde Central",
            "city": "Yaounde",
            "country": "Cameroon",
            "latitude": 3.8480,
            "longitude": 11.5021,
            "ai_cheat_sheet": "### MÉMO CLINIQUE\n* **Référence** : NCTTEST1",
        })
        self.store.upsert_plant({
            "name": "Artemisia annua (Armoise annuelle)",
            "scientific_name": "Artemisia annua",
            "indications": "Paludisme (Malaria), fièvres tropicales",
            "active_compounds": "Artémisinine",
            "references": ["https://pubmed.ncbi.nlm.nih.gov/26484835/"],
        })

        import bot.medical_store as ms
        self._orig_init = ms.MedicalStore.__init__
        db_path = self.db

        def _init(inst, path=None):
            self._orig_init(inst, path=db_path)

        ms.MedicalStore.__init__ = _init

        from web.server import create_app
        from fastapi.testclient import TestClient
        self.client = TestClient(create_app())

    def tearDown(self):
        import bot.medical_store as ms
        ms.MedicalStore.__init__ = self._orig_init
        try:
            self.store.close()
        except Exception:
            pass
        try:
            self.tmp.cleanup()
        except Exception:
            pass

    def test_health(self):
        r = self.client.get("/api/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "ok")

    def test_search_and_detail(self):
        r = self.client.get("/api/medical/search", params={"q": "Malaria"})
        self.assertEqual(r.status_code, 200)
        results = r.json()["results"]
        self.assertTrue(any(x["id"] == "NCTTEST1" for x in results))
        d = self.client.get("/api/medical/NCTTEST1")
        self.assertEqual(d.status_code, 200)
        self.assertEqual(d.json()["nct_id"], "NCTTEST1")

    def test_nearby_and_contact(self):
        r = self.client.get("/api/medical/nearby", params={"lat": 3.85, "lon": 11.50, "max_km": 50})
        self.assertEqual(r.status_code, 200)
        self.assertGreaterEqual(len(r.json()), 1)
        c = self.client.post("/api/medical/NCTTEST1/contact-email", json={"case_summary": "Cas anonymisé"})
        self.assertEqual(c.status_code, 200)
        self.assertIn("NCTTEST1", c.json()["body"])

    def test_plants_related(self):
        r = self.client.get("/api/medical/plants", params={"search": "Artemisia"})
        self.assertEqual(r.status_code, 200)
        plants = r.json()
        self.assertGreaterEqual(len(plants), 1)
        self.assertIn("related_studies", plants[0])
        self.assertIn("evidence_level", plants[0])


class TestMedicalStoreExtras(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        from bot.medical_store import MedicalStore
        self.store = MedicalStore(path=Path(self.tmp.name) / "m.db")

    def tearDown(self):
        try:
            self.store.close()
        except Exception:
            pass
        try:
            self.tmp.cleanup()
        except Exception:
            pass

    def test_stats_and_missing_sheet(self):
        self.store.upsert_record({
            "id": "X1",
            "title": "T",
            "source": "pubmed",
            "summary": "summary long enough",
            "eligibility_criteria": "",
            "phase": "Publication",
            "status": "COMPLETED",
            "conditions": "x",
            "sponsor": "j",
            "location_name": "",
            "city": "",
            "country": "",
            "latitude": None,
            "longitude": None,
            "ai_cheat_sheet": None,
            "url": "http://example.com",
            "nct_id": None,
        })
        stats = self.store.get_stats()
        self.assertEqual(stats["total_records"], 1)
        missing = self.store.get_records_missing_cheat_sheet()
        self.assertEqual(len(missing), 1)
        self.store.update_cheat_sheet("X1", "fiche")
        self.assertEqual(self.store.get_record("X1")["ai_cheat_sheet"], "fiche")


if __name__ == "__main__":
    unittest.main()
