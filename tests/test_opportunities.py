"""Tests profil, opportunités, digest, intents, paper trading."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ["MEDICAL_AUTH_OPTIONAL"] = "1"
os.environ["MEDICAL_AUTH_SECRET"] = "test-secret"
os.environ["SENTINELLE_DISABLE_SCHEDULER"] = "1"


class TestUserProfile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        from bot.user_profile import UserProfileStore
        self.store = UserProfileStore(path=Path(self.tmp.name) / "p.db")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_update_and_get(self):
        p = self.store.update({
            "display_name": "Aristide",
            "skills": "Python, Flutter",
            "interests": ["tech", "finance"],
            "city": "Douala",
            "country": "Cameroun",
            "preferred_companies": "Google",
            "trading_level": "beginner",
        })
        self.assertEqual(p["display_name"], "Aristide")
        self.assertIn("Python", p["skills"])
        self.assertEqual(p["city"], "Douala")
        self.assertEqual(self.store.get()["country"], "Cameroun")


class TestOpportunityScoring(unittest.TestCase):
    def test_score_boosts_skills(self):
        from bot.opportunities import score_offer
        from models import Offer
        o = Offer(
            title="Python Developer @ Acme",
            url="https://ex.com/1",
            source="t",
            provider="Acme",
            offer_type="job",
            description="Remote Python Douala",
            keywords_matched=["python"],
        )
        profile = {"skills": ["Python"], "interests": ["tech"], "city": "Douala", "country": "Cameroun", "preferred_companies": []}
        sc = score_offer(o, profile)
        self.assertGreaterEqual(sc, 5)


class TestOpportunityIntents(unittest.TestCase):
    def test_scholarship_intent(self):
        from bot.ai import parse_user_intent
        with mock.patch("bot.ai.get_gemini_key", return_value=None):
            r = parse_user_intent("cherche bourses France")
            self.assertEqual(r["intent"], "search_scholarships")

    def test_travel_intent(self):
        from bot.ai import parse_user_intent
        with mock.patch("bot.ai.get_gemini_key", return_value=None):
            r = parse_user_intent("opportunités de mobilité Europe")
            self.assertEqual(r["intent"], "search_travel")

    def test_events_intent(self):
        from bot.ai import parse_user_intent
        with mock.patch("bot.ai.get_gemini_key", return_value=None):
            r = parse_user_intent("events tech Yaoundé")
            self.assertEqual(r["intent"], "search_events")


class TestProfileOppApi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        import bot.user_profile as up
        path = Path(self.tmp.name) / "api_p.db"
        self.store = up.UserProfileStore(path=path)
        up._profile = self.store
        from web.server import create_app
        from fastapi.testclient import TestClient
        self.client = TestClient(create_app())

    def tearDown(self):
        import bot.user_profile as up
        self.store.close()
        up._profile = None
        self.tmp.cleanup()

    def test_profile_roundtrip(self):
        r = self.client.put("/api/profile", json={
            "display_name": "Aristide",
            "skills": ["Python"],
            "city": "Douala",
            "country": "Cameroun",
            "interests": ["tech"],
            "trading_level": "beginner",
        })
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["display_name"], "Aristide")
        g = self.client.get("/api/profile")
        self.assertEqual(g.json()["city"], "Douala")

    def test_opportunities_and_digest(self):
        from models import Offer
        fake = [
            Offer(
                title="Stage Python Douala @ Kmer",
                url="https://ex.com/job1",
                source="test",
                provider="Kmer",
                offer_type="job",
                description="Python stage Douala",
                keywords_matched=["python"],
            )
        ]
        with mock.patch("bot.opportunities.collect_opportunities", return_value=[
            {
                "title": fake[0].title,
                "url": fake[0].url,
                "source": "test",
                "provider": "Kmer",
                "offer_type": "job",
                "description": fake[0].description,
                "keywords_matched": ["python"],
                "match_score": 8.0,
            }
        ]):
            with mock.patch("bot.opportunities.feed_for_profile", return_value=[
                {
                    "title": fake[0].title,
                    "url": fake[0].url,
                    "source": "test",
                    "provider": "Kmer",
                    "offer_type": "job",
                    "description": fake[0].description,
                    "keywords_matched": ["python"],
                    "match_score": 8.0,
                }
            ]):
                with mock.patch("bot.opportunity_digest.call_llm", return_value="Bienvenue Aristide, stage Python chez Kmer."):
                    feed = self.client.get("/api/opportunities/feed")
                    self.assertEqual(feed.status_code, 200)
                    self.assertGreaterEqual(feed.json()["count"], 1)
                    dig = self.client.get("/api/opportunities/digest")
                    self.assertEqual(dig.status_code, 200)
                    self.assertIn("Aristide", dig.json()["message"])
                    opp = self.client.get("/api/opportunities?type=job")
                    self.assertEqual(opp.status_code, 200)


class TestPaperTrading(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        import bot.paper_trading as pt
        import bot.user_profile as up
        up._profile = up.UserProfileStore(path=Path(self.tmp.name) / "p.db")
        self.store = pt.PaperTradingStore(path=Path(self.tmp.name) / "paper.db")
        pt._paper = self.store

    def tearDown(self):
        import bot.paper_trading as pt
        import bot.user_profile as up
        self.store.close()
        up._profile.close()
        up._profile = None
        pt._paper = None
        self.tmp.cleanup()

    def test_buy_and_portfolio(self):
        with mock.patch.object(self.store, "_last_price", return_value=100.0):
            res = self.store.place_order("GOOGL", "buy", 2, "market")
            self.assertEqual(res["status"], "filled")
            port = self.store.portfolio()
            self.assertEqual(len(port["positions"]), 1)
            self.assertLess(port["cash"], 10000)

    def test_limit_order_then_sweep_fills_when_favorable(self):
        # Niveau intermediate pour autoriser les ordres limites
        self.store.set_level("intermediate")
        # Prix 100 : limit buy a 95 -> reste open
        with mock.patch.object(self.store, "_last_price", return_value=100.0):
            r = self.store.place_order("AAPL", "buy", 1, "limit", 95.0)
        self.assertEqual(r["status"], "open")
        # Prix descend a 90 -> condition favorable (prix <= limit) -> sweep doit remplir
        with mock.patch.object(self.store, "_last_price", return_value=90.0):
            sweep = self.store.sweep_open_orders()
            self.assertEqual(sweep["swept"], 1)
            self.assertEqual(sweep["filled"][0]["symbol"], "AAPL")
            self.assertEqual(sweep["filled"][0]["fill_price"], 95.0)
            port = self.store.portfolio()
        aapl = next(p for p in port["positions"] if p["symbol"] == "AAPL")
        self.assertEqual(aapl["qty"], 1.0)
        # Cash = 10000 - 95 = 9905 (le mark-to-market ne change pas le cash)
        self.assertAlmostEqual(port["cash"], 9905.0, places=2)

    def test_limit_order_sweep_keeps_open_when_unfavorable(self):
        self.store.set_level("intermediate")
        with mock.patch.object(self.store, "_last_price", return_value=100.0):
            self.store.place_order("AAPL", "buy", 1, "limit", 95.0)
        # Prix remonte a 105 -> toujours defavorable -> reste open
        with mock.patch.object(self.store, "_last_price", return_value=105.0):
            sweep = self.store.sweep_open_orders()
        self.assertEqual(sweep["swept"], 0)
        # L'ordre est toujours open
        orders = self.store.orders()
        self.assertEqual(orders[0]["status"], "open")


if __name__ == "__main__":
    unittest.main()
