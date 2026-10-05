import os
import tempfile
import unittest
from unittest import mock


class HealthEndpointTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _client(self):
        from fastapi.testclient import TestClient
        from bot.store import Store
        from web.server import create_app

        store = Store(os.path.join(self._tmp.name, "health.db"))
        self.addCleanup(store.conn.close)
        return TestClient(create_app(store))

    def test_health_returns_ok_in_development_by_default(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PYTHON_ENV", None)
            os.environ.pop("DATABASE_URL", None)
            response = self._client().get("/health")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["environment"], "development")

    def test_health_reports_production_when_python_env_is_production(self):
        with mock.patch.dict(os.environ, {"PYTHON_ENV": "production"}, clear=False):
            os.environ.pop("DATABASE_URL", None)
            response = self._client().get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["environment"], "production")


if __name__ == "__main__":
    unittest.main()
