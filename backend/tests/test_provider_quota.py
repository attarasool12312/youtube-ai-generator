import unittest
from unittest.mock import Mock, patch

import httpx

from app import provider_quota


class ProviderQuotaTests(unittest.TestCase):
    def setUp(self):
        provider_quota.clear_quota_cache()

    @staticmethod
    def response(status=200, headers=None):
        return httpx.Response(status, headers=headers or {})

    @patch("app.provider_quota.get_api_key", return_value="secret-key")
    def test_pexels_headers_used_and_reset_are_parsed(self, _key):
        client = Mock()
        client.get.return_value = self.response(200, {
            "X-Ratelimit-Limit": "25000",
            "X-Ratelimit-Remaining": "24999",
            "X-Ratelimit-Reset": "1787384041",
        })

        item = provider_quota.check_pexels(client)

        self.assertEqual(item["limit"], 25000)
        self.assertEqual(item["remaining"], 24999)
        self.assertEqual(item["used"], 1)
        self.assertRegex(item["reset_at"], r"^2026-.*\+00:00$")
        self.assertNotIn("secret-key", repr(item))

    @patch("app.provider_quota.get_api_key", return_value="secret-key")
    def test_pexels_429_is_rate_limited(self, _key):
        client = Mock()
        client.get.return_value = self.response(429, {"Retry-After": "30"})
        item = provider_quota.check_pexels(client)
        self.assertEqual(item["status"], "rate_limited")
        self.assertEqual(item["retry_after"], 30)

    @patch("app.provider_quota.get_api_key", return_value="secret-key")
    def test_pixabay_short_window_headers(self, _key):
        client = Mock()
        client.get.return_value = self.response(200, {
            "X-RateLimit-Limit": "100",
            "X-RateLimit-Remaining": "87",
            "X-RateLimit-Reset": "34",
        })
        item = provider_quota.check_pixabay(client)
        self.assertEqual((item["limit"], item["remaining"]), (100, 87))
        self.assertEqual(item["window"], "60 seconds")
        self.assertEqual(item["reset_in_seconds"], 34)

    @patch("app.provider_quota.get_api_key", return_value="secret-key")
    def test_missing_headers_are_not_exposed_without_secret(self, _key):
        client = Mock()
        client.get.return_value = self.response()
        item = provider_quota.check_coverr(client)
        self.assertEqual(item["quota_message"], provider_quota.NOT_EXPOSED)
        self.assertEqual(item["status"], "not_exposed")
        self.assertNotIn("secret-key", repr(item))

    @patch("app.provider_quota.get_api_key", return_value="")
    def test_disconnected_provider_does_not_call_network(self, _key):
        client = Mock()
        item = provider_quota.check_pexels(client)
        client.get.assert_not_called()
        self.assertFalse(item["connected"])

    @patch("app.provider_quota.get_api_key", return_value="secret-key")
    def test_network_failure_is_safe(self, _key):
        client = Mock()
        client.get.side_effect = httpx.ConnectError("offline")
        item = provider_quota.check_pexels(client)
        self.assertEqual(item["status"], "unavailable")

    @patch("app.provider_quota._collect_uncached")
    def test_cache_and_manual_refresh(self, collect):
        collect.side_effect = [[{"provider": "first"}], [{"provider": "fresh"}]]
        first = provider_quota.get_provider_quotas()
        cached = provider_quota.get_provider_quotas()
        fresh = provider_quota.get_provider_quotas(force_refresh=True)
        self.assertEqual(first[0]["provider"], cached[0]["provider"])
        self.assertEqual(cached[0]["cache_state"], "Cached")
        self.assertEqual(fresh[0]["cache_state"], "Live")
        self.assertEqual(fresh[0]["provider"], "fresh")
        self.assertEqual(collect.call_count, 2)

    def test_settings_template_excludes_provider_monitoring_ui(self):
        with open("backend/app/templates/api_settings.html", encoding="utf-8") as page:
            html = page.read()
        self.assertNotIn("API Limits &amp; Quota Status", html)
        self.assertNotIn("Refresh Limits", html)
        self.assertNotIn("Provider Monitoring", html)
        self.assertNotIn("setInterval", html)


if __name__ == "__main__":
    unittest.main()
