"""La veille de fond ne part pas quand les tests montent l'application."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from bot.store import Store
from web.server import create_app


class TestScheduler(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(path=Path(self.tmp.name) / "scheduler.db")

    def tearDown(self):
        os.environ["SENTINELLE_DISABLE_SCHEDULER"] = "1"
        try:
            self.store.close()
        except Exception:
            pass
        self.tmp.cleanup()

    def test_disabled_flag_skips_background_task_and_health_stays_ok(self):
        os.environ["SENTINELLE_DISABLE_SCHEDULER"] = "1"
        app = create_app(store=self.store)
        with mock.patch("web.server.asyncio.create_task") as create_task:
            with TestClient(app) as client:
                response = client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        create_task.assert_not_called()

    def test_without_flag_the_background_task_is_scheduled(self):
        os.environ.pop("SENTINELLE_DISABLE_SCHEDULER", None)
        started = {}

        async def fake_periodic(_store):
            started["yes"] = True

        app = create_app(store=self.store)
        with mock.patch("web.server.periodic_scraping_task", fake_periodic):
            with TestClient(app) as client:
                response = client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(started.get("yes"))


if __name__ == "__main__":
    unittest.main()
