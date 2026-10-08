"""Tests couche marché multi-providers."""
from __future__ import annotations

import os
import unittest
from unittest import mock

os.environ["MEDICAL_AUTH_OPTIONAL"] = "1"
os.environ["MEDICAL_AUTH_SECRET"] = "test-secret"
os.environ["SENTINELLE_DISABLE_SCHEDULER"] = "1"


class TestMarketNormalize(unittest.TestCase):
    def test_crypto_aliases(self):
        from bot.market_data import is_crypto_symbol, normalize_symbol
        self.assertEqual(normalize_symbol("btc"), "BTCUSDT")
        self.assertEqual(normalize_symbol("ETH"), "ETHUSDT")
        self.assertTrue(is_crypto_symbol("BTCUSDT"))
        self.assertFalse(is_crypto_symbol("GOOGL"))


class TestMarketProviders(unittest.TestCase):
    def test_binance_quote_mocked(self):
        from bot import market_data as md
        md._QUOTE_CACHE.clear()
        fake = {
            "lastPrice": "65000.5",
            "openPrice": "64000",
            "highPrice": "66000",
            "lowPrice": "63000",
            "volume": "1000",
            "priceChangePercent": "1.5",
        }
        with mock.patch("bot.market_data.requests.get") as g:
            resp = mock.Mock()
            resp.status_code = 200
            resp.json.return_value = fake
            g.return_value = resp
            q = md.binance_quote("BTC")
            self.assertIsNotNone(q)
            self.assertEqual(q["source"], "binance")
            self.assertAlmostEqual(q["price"], 65000.5)

    def test_get_quote_routes_crypto(self):
        from bot import market_data as md
        md._QUOTE_CACHE.clear()
        with mock.patch("bot.market_data.binance_quote", return_value={
            "symbol": "BTCUSDT", "price": 1.0, "source": "binance",
            "latency": "near_realtime", "delay_note": "x", "as_of": "t",
        }):
            q = md.get_quote("BTC", force=True)
            self.assertEqual(q["source"], "binance")

    def test_get_quote_routes_equity(self):
        from bot import market_data as md
        md._QUOTE_CACHE.clear()
        with mock.patch("bot.market_data.finnhub_quote", return_value=None):
            with mock.patch("bot.market_data.alphavantage_quote", return_value=None):
                with mock.patch("bot.market_data.yahoo_quote", return_value={
                    "symbol": "GOOGL", "price": 100.0, "source": "yahoo",
                    "latency": "delayed", "delay_note": "y", "as_of": "t",
                }):
                    q = md.get_quote("GOOGL", force=True)
                    self.assertEqual(q["source"], "yahoo")

    def test_sma(self):
        from bot.market_data import _sma
        s = _sma([1, 2, 3, 4, 5], 3)
        self.assertIsNone(s[0])
        self.assertEqual(s[2], 2.0)


class TestMarketApi(unittest.TestCase):
    def setUp(self):
        from web.server import create_app
        from fastapi.testclient import TestClient
        self.client = TestClient(create_app())

    def test_providers(self):
        r = self.client.get("/api/market/providers")
        self.assertEqual(r.status_code, 200)
        self.assertIn("binance", r.json())
        self.assertIn("yahoo", r.json())

    def test_quote_endpoint(self):
        with mock.patch("bot.market_data.get_quote", return_value={
            "symbol": "BTCUSDT", "price": 1.0, "source": "binance",
            "latency": "near_realtime", "as_of": "t",
        }):
            r = self.client.get("/api/market/quote?symbol=BTC")
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()["source"], "binance")

    def test_chart_endpoint(self):
        with mock.patch("bot.market_data.get_chart", return_value={
            "symbol": "GOOGL", "points": [{"ts": "2026-01-01T00:00:00+00:00", "price": 10}],
            "source": "yahoo", "count": 1, "sma20": [None],
        }):
            r = self.client.get("/api/market/chart?symbol=GOOGL&range=1mo")
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()["count"], 1)


class TestCryptoWatchGuess(unittest.TestCase):
    def test_bitcoin_ticker(self):
        from bot.watch_store import guess_kind_and_ticker
        kind, t = guess_kind_and_ticker("Bitcoin")
        self.assertEqual(kind, "crypto")
        self.assertEqual(t, "BTCUSDT")


if __name__ == "__main__":
    unittest.main()
