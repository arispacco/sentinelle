"""Tests de robustesse pour /api/chat : fallbacks, absence de clé LLM, erreurs de scraping."""
from __future__ import annotations

import os
import unittest

os.environ["SENTINELLE_DISABLE_SCHEDULER"] = "1"

from fastapi.testclient import TestClient

from bot.store import Store
from web.server import create_app


class TestChatResilience(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = Store()
        cls.app = create_app(store=cls.store)
        cls.client = TestClient(cls.app)

    def test_chat_empty_message(self):
        resp = self.client.post("/api/chat", json={"message": ""})
        self.assertEqual(resp.status_code, 400)

    def test_chat_simple_greeting(self):
        resp = self.client.post("/api/chat", json={"message": "bonjour", "session_id": "test_greet"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data.get("intent"), "chat")
        self.assertIn("reply", data)
        self.assertTrue(len(data["reply"]) > 0)

    def test_chat_news_search_fallback(self):
        resp = self.client.post("/api/chat", json={"message": "actualité tech IA", "session_id": "test_news"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("reply", data)
        self.assertIn("sources", data)

    def test_chat_handles_exception_gracefully(self):
        resp = self.client.post("/api/chat", json={"message": "trouve des offres python", "session_id": "test_offers"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("reply", data)
        self.assertIsInstance(data.get("sources"), list)


if __name__ == "__main__":
    unittest.main()
