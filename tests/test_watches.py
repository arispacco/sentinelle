import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ["MEDICAL_AUTH_OPTIONAL"] = "1"
os.environ["MEDICAL_AUTH_SECRET"] = "test-secret"
os.environ["SENTINELLE_DISABLE_SCHEDULER"] = "1"


class TestWatchStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        from bot.watch_store import WatchStore
        self.store = WatchStore(path=Path(self.tmp.name) / "w.db")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_create_and_dashboard(self):
        w = self.store.create("Google", kind="company", query="Google Alphabet", ticker="GOOGL")
        self.assertEqual(w["slug"], "google")
        self.store.add_event(w["id"], "Google earnings", "https://ex.com/1", "Reuters", "Beats estimates", "earnings")
        self.store.add_price(w["id"], "2026-01-01T00:00:00+00:00", 100.0, 1e6)
        self.store.add_price(w["id"], "2026-02-01T00:00:00+00:00", 110.0, 1e6)
        dash = self.store.dashboard(w["id"])
        self.assertEqual(dash["stats"]["events_count"], 1)
        self.assertEqual(dash["stats"]["price_change_pct"], 10.0)

    def test_guess_ticker(self):
        from bot.watch_store import guess_kind_and_ticker
        kind, t = guess_kind_and_ticker("Google")
        self.assertEqual(kind, "company")
        self.assertEqual(t, "GOOGL")
        kind, t = guess_kind_and_ticker("Chine Taiwan")
        self.assertEqual(kind, "geopolitics")
        self.assertIsNone(t)


class TestWatchIntent(unittest.TestCase):
    def test_create_watch_intent(self):
        from bot.ai import parse_user_intent
        with mock.patch("bot.ai.get_gemini_key", return_value=None):
            r = parse_user_intent("je veux suivre Google")
            self.assertEqual(r["intent"], "create_watch")
            self.assertIn("Google", r["query"])


class TestWatchApi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        import bot.watch_store as ws
        self._orig = ws.get_watch_store
        path = Path(self.tmp.name) / "api_w.db"
        self.store = ws.WatchStore(path=path)
        ws._store = self.store
        from web.server import create_app
        from fastapi.testclient import TestClient
        self.client = TestClient(create_app())

    def tearDown(self):
        import bot.watch_store as ws
        self.store.close()
        ws._store = None
        self.tmp.cleanup()

    def test_create_list_get(self):
        from bot.auth import create_token
        token = create_token({"sub": "tester@sentinelle.ai", "uid": "t1"})
        headers = {"Authorization": f"Bearer {token}"}
        with mock.patch("bot.watch_engine.WatchNewsScraper.search", return_value=[
            {"title": "News", "url": "https://n.example/1", "provider": "X", "summary": "s", "event_type": "news", "published_at": None}
        ]):
            with mock.patch("bot.market_data.get_chart", return_value={
                "points": [
                    {"ts": "2026-01-01T00:00:00+00:00", "price": 10.0, "volume": 100, "source": "test"},
                    {"ts": "2026-01-02T00:00:00+00:00", "price": 12.0, "volume": 100, "source": "test"},
                ],
                "source": "test",
            }):
                r = self.client.post("/api/watches", json={"title": "Google", "refresh": True}, headers=headers)
                self.assertEqual(r.status_code, 200)
                slug = r.json()["slug"]
                lst = self.client.get("/api/watches")
                self.assertTrue(any(w["slug"] == slug for w in lst.json()))
                dash = self.client.get(f"/api/watches/{slug}")
                self.assertEqual(dash.status_code, 200)
                self.assertGreaterEqual(dash.json()["stats"]["prices_count"], 2)


if __name__ == "__main__":
    unittest.main()
