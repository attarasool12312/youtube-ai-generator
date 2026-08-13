import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import httpx

from app.api_health import test_provider as run_provider_test
from app.media_relevance import build_media_topic_context
from app.media_selector import try_providers
from app.scene_media import (
    REUSE_MIN_SCENE_GAP,
    _emergency_local_reuse,
    emergency_max_uses,
    emergency_topic_queries,
)


class ProviderHealthTests(unittest.TestCase):
    @patch("app.api_health.get_api_key", return_value="secret-value")
    def test_working_and_no_secret(self, _key):
        client = Mock()
        client.get.return_value = Mock(status_code=200)
        result = run_provider_test("pexels", client)
        self.assertEqual(result["status"], "Working")
        self.assertNotIn("secret-value", repr(result))

    @patch("app.api_health.get_api_key", return_value="secret-value")
    def test_auth_rate_timeout(self, _key):
        client = Mock()
        client.get.return_value = Mock(status_code=401)
        self.assertEqual(run_provider_test("pexels", client)["status"], "Authentication Failed")
        client.get.return_value = Mock(status_code=429)
        self.assertEqual(run_provider_test("pexels", client)["status"], "Rate Limited")
        client.get.side_effect = httpx.TimeoutException("slow")
        self.assertEqual(run_provider_test("pexels", client)["detail"], "Timeout")

    @patch("app.api_health.get_api_key", return_value="")
    def test_missing_config_no_network(self, _key):
        client = Mock()
        self.assertEqual(run_provider_test("pexels", client)["status"], "Configuration Missing")
        client.get.assert_not_called()

    def test_template_has_every_action_without_polling(self):
        html = Path("backend/app/templates/api_settings.html").read_text()
        for provider in ("pexels", "pixabay", "coverr", "openverse", "gemini", "together", "youtube"):
            self.assertIn(f"/settings/providers/{{{{ provider_key }}}}/test", html)
        self.assertIn("Test API", html)
        self.assertIn("Test Connection", html)
        self.assertNotIn("setInterval", html)


class EmergencyStockTests(unittest.TestCase):
    def test_queries_are_global_short_and_at_most_two(self):
        for title, profile in (("How Electric Cars Recover Energy", "technology_future"),
                               ("The History of Ancient Rome", "history_documentary")):
            values = emergency_topic_queries(build_media_topic_context(title, profile))
            self.assertLessEqual(len(values), 2)
            self.assertTrue(all(len(value.split()) <= 4 for value in values))

    def test_duration_scaling(self):
        self.assertEqual([emergency_max_uses(v) for v in (3, 10, 15, 20, 25, 30)],
                         [2, 2, 3, 3, 4, 4])

    def test_local_reuse_is_least_used_distant_and_preserves_attribution(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            older = root / "old.jpg"
            newer = root / "new.jpg"
            older.write_bytes(b"old")
            newer.write_bytes(b"new")
            usage = {
                "old": {"last_scene": 140, "uses": 1, "result": {
                    "media_file": str(older), "source": "source-old", "provider": "pexels",
                    "media_type": "image", "author": "Author", "license": "L", "license_url": "U"}},
                "new": {"last_scene": 190, "uses": 2, "result": {
                    "media_file": str(newer), "source": "source-new", "provider": "pixabay",
                    "media_type": "image"}},
            }
            result = _emergency_local_reuse(usage, root, 196, 25)
            self.assertEqual(result["source"], "source-old")
            self.assertEqual((result["author"], result["license"], result["license_url"]), ("Author", "L", "U"))
            self.assertTrue(result["emergency"])
            self.assertTrue(Path(result["media_file"]).is_file())

    def test_local_reuse_never_adjacent(self):
        with tempfile.TemporaryDirectory() as folder:
            media = Path(folder) / "a.jpg"
            media.write_bytes(b"x")
            usage = {"a": {"last_scene": 195, "uses": 1, "result": {
                "media_file": str(media), "source": "a", "provider": "pexels", "media_type": "image"}}}
            self.assertIsNone(_emergency_local_reuse(usage, folder, 196, 25))

    def test_pexels_429_breaks_without_aggressive_loop(self):
        response = httpx.Response(429, headers={"Retry-After": "30"},
                                  request=httpx.Request("GET", "https://example.test"))
        provider = Mock(side_effect=httpx.HTTPStatusError("limited", request=response.request, response=response))
        provider.__name__ = "pexels_video"
        with tempfile.TemporaryDirectory() as folder:
            self.assertIsNone(try_providers((provider,), "long query", Path(folder), 1,
                                            "mp4", "landscape", set(),
                                            build_media_topic_context("Bridge Engineering", "general_explainer")))
        provider.assert_called_once()


if __name__ == "__main__":
    unittest.main()
