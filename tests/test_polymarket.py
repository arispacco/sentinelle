"""Tests Polymarket client + endpoints prédictions (réseau mocké)."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ["MEDICAL_AUTH_OPTIONAL"] = "1"
os.environ["MEDICAL_AUTH_SECRET"] = "test-secret"

SAMPLE_MARKET = {
    "id": "559651",
    "question": "Xi Jinping out before 2027?",
    "conditionId": "0xa467b14d51f01b957109d9cbb1d6c124fab2a089d52ed8f471d23c2812e743b7",
    "slug": "xi-jinping-out-before-2027",
    "outcomes": '["Yes", "No"]',
    "outcomePrices": '["0.0385", "0.9615"]',
    "clobTokenIds": (
        '["32338220190071351435772801779725302244575775216413325951443816017994629993401",'
        '"25659310674993675562345759665114759892400026242514633218387667107987341231962"]'
    ),
    "volume24hr": 24242.85,
    "liquidity": "288707.56",
    "liquidityNum": 288707.56,
    "image": "https://example.com/img.jpg",
    "active": True,
    "closed": False,
    "acceptingOrders": True,
    "bestBid": 0.038,
    "bestAsk": 0.039,
    "lastTradePrice": 0.039,
}


class _FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class TestPolymarketClient(unittest.TestCase):
    def setUp(self):
        from scrapers.polymarket_client import PolymarketClient
        self.client = PolymarketClient(timeout=8, cache_ttl=0)

    def test_validate_ids(self):
        self.assertEqual(self.client.validate_market_id("559651"), "559651")
        self.assertEqual(self.client.validate_market_id("xi-jinping-out-before-2027"), "xi-jinping-out-before-2027")
        with self.assertRaises(ValueError):
            self.client.validate_market_id("../evil")
        with self.assertRaises(ValueError):
            self.client.validate_token_id("abc")

    def test_normalize_market(self):
        n = self.client.normalize_market(SAMPLE_MARKET)
        self.assertEqual(n["id"], "559651")
        self.assertEqual(n["slug"], "xi-jinping-out-before-2027")
        self.assertAlmostEqual(n["yes_price"], 0.0385)
        self.assertAlmostEqual(n["no_price"], 0.9615)
        self.assertTrue(n["yes_token_id"].startswith("32338"))
        self.assertIn("polymarket.com/event/", n["url"])

    def test_list_markets_mocked(self):
        with mock.patch.object(self.client.session, "get", return_value=_FakeResp([SAMPLE_MARKET])) as g:
            markets = self.client.list_markets(limit=5)
            self.assertEqual(len(markets), 1)
            self.assertTrue(g.called)
            kwargs = g.call_args.kwargs
            self.assertEqual(kwargs.get("timeout"), 8)

    def test_price_and_midpoint(self):
        token = "32338220190071351435772801779725302244575775216413325951443816017994629993401"

        def side_effect(url, **kwargs):
            if "/price" in url:
                return _FakeResp({"price": "0.038"})
            if "/midpoint" in url:
                return _FakeResp({"mid": "0.0385"})
            raise AssertionError(url)

        with mock.patch.object(self.client.session, "get", side_effect=side_effect):
            self.assertAlmostEqual(self.client.get_price(token, "buy"), 0.038)
            self.assertAlmostEqual(self.client.get_midpoint(token), 0.0385)

    def test_quote_price_prefers_clob(self):
        norm = self.client.normalize_market(SAMPLE_MARKET)
        with mock.patch.object(self.client, "get_midpoint", return_value=0.041):
            with mock.patch.object(self.client, "get_price", return_value=0.04):
                px = self.client.quote_price_for_outcome(norm, "YES")
                self.assertAlmostEqual(px, 0.041)

    def test_book_top_levels(self):
        token = "32338220190071351435772801779725302244575775216413325951443816017994629993401"
        payload = {
            "market": SAMPLE_MARKET["conditionId"],
            "asset_id": token,
            "bids": [{"price": "0.03", "size": "10"}, {"price": "0.038", "size": "100"}],
            "asks": [{"price": "0.039", "size": "50"}, {"price": "0.05", "size": "20"}],
        }
        with mock.patch.object(self.client.session, "get", return_value=_FakeResp(payload)):
            book = self.client.get_book(token, depth=5)
            self.assertEqual(book["bids"][0]["price"], "0.038")
            self.assertEqual(book["asks"][0]["price"], "0.039")


class TestPredictionsScraper(unittest.TestCase):
    def test_markets_as_dicts(self):
        from scrapers.polymarket_client import PolymarketClient
        from scrapers.predictions import PredictionsScraper

        client = PolymarketClient(cache_ttl=0)
        with mock.patch.object(client, "list_markets", return_value=[SAMPLE_MARKET]):
            scraper = PredictionsScraper(client=client)
            rows = scraper.markets_as_dicts(limit=10)
            self.assertEqual(rows[0]["offer_type"], "prediction")
            self.assertEqual(rows[0]["id"], "559651")

    def test_run_builds_offer_url(self):
        from scrapers.polymarket_client import PolymarketClient
        from scrapers.predictions import PredictionsScraper

        client = PolymarketClient(cache_ttl=0)
        with mock.patch.object(client, "list_markets", return_value=[SAMPLE_MARKET]):
            offers = PredictionsScraper(client=client).run()
            self.assertTrue(offers[0].url.startswith("https://polymarket.com/"))
            self.assertEqual(offers[0].offer_type, "prediction")


class TestPredictionsApi(unittest.TestCase):
    def setUp(self):
        """Évite web.server (app module-level) si la DB locale est corrompue."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from bot.store import Store
        from web.api import create_router

        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.store = Store(path=Path(self.tmp.name) / "api.db")
        app = FastAPI()
        app.include_router(create_router(self.store))
        self.client = TestClient(app)

    def tearDown(self):
        try:
            self.store.conn.close()
        except Exception:
            pass
        try:
            self.tmp.cleanup()
        except Exception:
            pass

    def test_markets_endpoint(self):
        fake = [{
            "id": "1",
            "slug": "test-mkt",
            "title": "Test?",
            "question": "Test?",
            "url": "https://polymarket.com/event/test-mkt",
            "source": "polymarket",
            "provider": "Polymarket",
            "offer_type": "prediction",
            "description": "Probabilité : 40.0% Oui",
            "yes_price": 0.4,
            "no_price": 0.6,
        }]
        with mock.patch("scrapers.predictions.PredictionsScraper.markets_as_dicts", return_value=fake):
            r = self.client.get("/api/predictions/markets?limit=5")
            self.assertEqual(r.status_code, 200)
            body = r.json()
            self.assertEqual(body["count"], 1)
            self.assertEqual(body["markets"][0]["slug"], "test-mkt")

    def test_market_detail_invalid_id(self):
        r = self.client.get("/api/predictions/market/not%20valid!!!")
        self.assertEqual(r.status_code, 400)

    def test_market_detail_ok(self):
        detail = {
            "id": "559651",
            "slug": "xi-jinping-out-before-2027",
            "yes_price": 0.0385,
            "clob_yes_price": 0.0385,
            "order_book": {"bids": [], "asks": []},
            "price_history": [{"t": 1, "p": 0.04}],
        }
        with mock.patch("scrapers.predictions.PredictionsScraper.market_detail", return_value=detail):
            r = self.client.get("/api/predictions/market/559651")
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()["id"], "559651")

    def test_bet_sources_clob_price(self):
        import bot.paper_trading as pt

        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        store = pt.PaperTradingStore(path=Path(tmp.name) / "paper.db")
        self.addCleanup(store.close)

        norm = {
            "id": "559651",
            "slug": "xi-jinping-out-before-2027",
            "question": "Xi?",
            "yes_token_id": "32338220190071351435772801779725302244575775216413325951443816017994629993401",
            "no_token_id": "25659310674993675562345759665114759892400026242514633218387667107987341231962",
            "token_ids": {"Yes": "32338220190071351435772801779725302244575775216413325951443816017994629993401"},
            "yes_price": 0.0385,
            "gamma_yes_price": 0.0385,
        }

        with mock.patch("bot.paper_trading.get_paper_store", return_value=store):
            with mock.patch("scrapers.polymarket_client.get_polymarket_client") as gc:
                fake_client = mock.MagicMock()
                fake_client.validate_market_id.side_effect = lambda x: x
                fake_client.get_market.return_value = SAMPLE_MARKET
                fake_client.normalize_market.return_value = norm
                fake_client.quote_price_for_outcome.return_value = 0.042
                gc.return_value = fake_client
                r = self.client.post(
                    "/api/predictions/bet",
                    json={
                        "market_id": "559651",
                        "outcome": "YES",
                        "amount": 10,
                        "price": 0.99,  # doit être ignoré
                    },
                )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["status"], "success")
        self.assertEqual(body["price_source"], "clob")
        self.assertAlmostEqual(body["buy_price"], 0.042)

    def test_bet_rejects_client_price_without_server_quote(self):
        import bot.paper_trading as pt

        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        store = pt.PaperTradingStore(path=Path(tmp.name) / "paper.db")
        self.addCleanup(store.close)
        cash_before = float(store.account()["cash"])

        norm = {
            "id": "559651",
            "slug": "xi-jinping-out-before-2027",
            "question": "Xi?",
            "yes_price": None,
            "gamma_yes_price": None,
            "gamma_no_price": None,
        }

        with mock.patch("bot.paper_trading.get_paper_store", return_value=store):
            with mock.patch("scrapers.polymarket_client.get_polymarket_client") as gc:
                fake_client = mock.MagicMock()
                fake_client.validate_market_id.side_effect = lambda x: x
                fake_client.get_market.return_value = SAMPLE_MARKET
                fake_client.normalize_market.return_value = norm
                fake_client.quote_price_for_outcome.return_value = None
                gc.return_value = fake_client
                refused = self.client.post(
                    "/api/predictions/bet",
                    json={
                        "market_id": "559651",
                        "outcome": "YES",
                        "amount": 10,
                        "price": 0.01,
                    },
                )
                missing = self.client.post(
                    "/api/predictions/bet",
                    json={"outcome": "YES", "amount": 10, "price": 0.01},
                )

        self.assertEqual(refused.status_code, 400)
        self.assertEqual(refused.json()["error"], "cours serveur indisponible")
        self.assertEqual(missing.status_code, 400)
        self.assertEqual(missing.json()["error"], "marché requis")
        self.assertEqual(store.list_predictions(10), [])
        self.assertAlmostEqual(float(store.account()["cash"]), cash_before)


class TestSpacesPredictions(unittest.TestCase):
    def test_load_predictions_dicts(self):
        from bot import spaces
        fake = [{
            "id": "1",
            "title": "Will X happen?",
            "url": "https://polymarket.com/event/x",
            "source": "polymarket",
            "provider": "Polymarket",
            "offer_type": "prediction",
            "description": "55%",
            "yes_price": 0.55,
        }]
        with mock.patch("scrapers.predictions.PredictionsScraper.markets_as_dicts", return_value=fake):
            data = spaces.load_space_items("predictions")
            self.assertEqual(data["items"][0]["offer_type"], "prediction")
            self.assertAlmostEqual(data["items"][0]["yes_price"], 0.55)


if __name__ == "__main__":
    unittest.main()
