import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ["MEDICAL_AUTH_OPTIONAL"] = "1"
os.environ["MEDICAL_AUTH_SECRET"] = "test-secret"
os.environ["SENTINELLE_DISABLE_SCHEDULER"] = "1"


class TestIntentRouting(unittest.TestCase):
    def test_crypto_intent(self):
        from bot.ai import parse_user_intent
        with mock.patch("bot.ai.get_gemini_key", return_value=None):
            r = parse_user_intent("prix du bitcoin aujourd'hui")
            self.assertEqual(r["intent"], "search_crypto")

    def test_geo_intent(self):
        from bot.ai import parse_user_intent
        with mock.patch("bot.ai.get_gemini_key", return_value=None):
            r = parse_user_intent("actu conflit Ukraine")
            self.assertEqual(r["intent"], "search_geopolitics")

    def test_predictions_intent(self):
        from bot.ai import parse_user_intent
        with mock.patch("bot.ai.get_gemini_key", return_value=None):
            r = parse_user_intent("probabilités polymarket election")
            self.assertEqual(r["intent"], "search_predictions")

    def test_grounded_digest_without_llm(self):
        from bot.ai import synthesize_digest
        from bot.models import Offer
        with mock.patch("bot.ai.get_gemini_key", return_value=None):
            with mock.patch.dict(os.environ, {"PREFER_OLLAMA": "false", "OLLAMA_HOST": ""}):
                o = Offer(
                    title="Bitcoin up",
                    url="https://example.com",
                    source="coingecko",
                    provider="CoinGecko",
                    offer_type="crypto",
                    description="Prix : 100",
                )
                text = synthesize_digest("bitcoin", [o])
                self.assertIn("Bitcoin up", text)
                self.assertIn("example.com", text)


class TestSpacesCatalog(unittest.TestCase):
    def test_list_spaces(self):
        from bot.spaces import list_spaces, get_space_meta
        spaces = list_spaces()
        slugs = {s["slug"] for s in spaces}
        self.assertTrue({"crypto", "geopolitique", "predictions", "sante", "actualite"} <= slugs)
        self.assertIsNotNone(get_space_meta("crypto"))

    def test_load_crypto_space_mocked(self):
        from bot import spaces
        from bot.models import Offer
        fake = [Offer(title="BTC", url="https://x", source="coingecko", provider="CoinGecko", offer_type="crypto", description="ok")]
        with mock.patch("scrapers.crypto.CryptoScraper.run", return_value=fake):
            data = spaces.load_space_items("crypto")
            self.assertEqual(data["slug"], "crypto")
            self.assertGreaterEqual(len(data["items"]), 1)

    def test_load_predictions_mocked(self):
        from bot import spaces
        from bot.models import Offer
        fake = [Offer(title="Will X happen?", url="https://polymarket.com", source="polymarket", provider="Polymarket", offer_type="prediction", description="55%")]
        with mock.patch("scrapers.predictions.PredictionsScraper.run", return_value=fake):
            data = spaces.load_space_items("predictions")
            self.assertEqual(data["items"][0]["offer_type"], "prediction")


class TestSpacesApi(unittest.TestCase):
    def setUp(self):
        from web.server import create_app
        from fastapi.testclient import TestClient
        from bot.auth import create_token
        self.client = TestClient(create_app())
        self.token = create_token({"sub": "test@sentinelle.ai", "uid": "u1"})
        self.headers = {"Authorization": f"Bearer {self.token}"}

    def test_spaces_catalog(self):
        r = self.client.get("/api/spaces")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(any(s["slug"] == "crypto" for s in r.json()))

    def test_chat_greeting_grounded(self):
        r = self.client.post("/api/chat", json={"message": "bonjour", "session_id": "test_sess"}, headers=self.headers)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["intent"], "chat")
        self.assertIn("sources", body)

    def test_history(self):
        self.client.post("/api/chat", json={"message": "salut", "session_id": "hist1"}, headers=self.headers)
        r = self.client.get("/api/chat/history")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(any(s["session_id"] == "hist1" for s in r.json()))


if __name__ == "__main__":
    unittest.main()
