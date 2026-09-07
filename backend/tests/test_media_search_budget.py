import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import media_selector


def _provider(name, result, calls):
    def fake(query, path, orientation, used_sources=None, offset=0, topic_context=None):
        calls.append((name, query))
        if result is None:
            return None
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"x")
        return dict(result)
    fake.__name__ = name
    return fake


class MediaSearchBudgetTests(unittest.TestCase):
    def test_strong_80_short_circuits(self):
        calls = []
        strong = _provider("pexels_video", {
            "source": "good", "relevance_score": 80,
            "relevance_confidence": "strong",
        }, calls)
        later = _provider("coverr_video", {
            "source": "later", "relevance_score": 100,
            "relevance_confidence": "strong",
        }, calls)
        with tempfile.TemporaryDirectory() as folder, patch.object(
            media_selector, "provider_query_candidates", side_effect=lambda q, c=None: [q]
        ):
            result = media_selector.try_providers(
                [strong, later], "subject", Path(folder), 1, "mp4", "landscape", set()
            )
        self.assertEqual(result["source"], "good")
        self.assertEqual(len(calls), 1)

    def test_acceptable_video_still_checks_image_pass(self):
        video = {
            "source": "video", "relevance_score": 60,
            "relevance_confidence": "acceptable",
            "media_file": "/tmp/fake.mp4",
        }
        with patch("app.media_selector.try_providers", return_value=video) as mocked:
            result = media_selector._stock_pass(
                "subject", Path("/tmp"), 1, "video", "landscape", set(), None
            )
        self.assertEqual(result["source"], "video")
        self.assertEqual(mocked.call_count, 2)

    def test_strong_stock_image_wins_after_weak_video(self):
        video = {
            "source": "weak-video", "relevance_score": 35,
            "relevance_confidence": "weak", "media_file": "/tmp/fake-video.mp4",
        }
        image = {
            "source": "exact-image", "relevance_score": 90,
            "relevance_confidence": "strong", "media_file": "/tmp/fake-image.jpg",
            "relevance_penalties": [],
        }
        with patch("app.media_selector._search_exact_then_one_variant", side_effect=[video, image]) as mocked:
            result = media_selector._stock_pass(
                "exact subject", Path("/tmp"), 1, "video", "landscape", set(), None
            )
        self.assertEqual(mocked.call_count, 2)
        self.assertEqual(result["source"], "exact-image")
        self.assertFalse(result["needs_ai_fallback"])
        self.assertEqual(result["stock_decision"], "image_accepted_strong")

    def test_acceptable_stock_image_does_not_bypass_ai_fallback(self):
        video = {
            "source": "weak-video", "relevance_score": 35,
            "relevance_confidence": "weak", "media_file": "/tmp/fake-video.mp4",
        }
        image = {
            "source": "generic-image", "relevance_score": 70,
            "relevance_confidence": "acceptable", "media_file": "/tmp/fake-image.jpg",
            "relevance_penalties": [],
        }
        with patch("app.media_selector._search_exact_then_one_variant", side_effect=[video, image]):
            result = media_selector._stock_pass(
                "exact subject", Path("/tmp"), 1, "video", "landscape", set(), None
            )
        self.assertTrue(result["needs_ai_fallback"])
        self.assertEqual(result["stock_decision"], "ai_fallback_pending")

    def test_candidate_download_count_is_tightly_bounded(self):
        calls = []
        weak = _provider("pexels_video", {
            "source": "weak", "relevance_score": 20,
            "relevance_confidence": "weak",
        }, calls)
        with tempfile.TemporaryDirectory() as folder, patch.object(
            media_selector, "provider_query_candidates",
            side_effect=lambda q, c=None: [q + " a", q + " b", q + " c"]
        ):
            media_selector.try_providers(
                [weak], "subject", Path(folder), 1, "mp4", "landscape", set()
            )
        self.assertLessEqual(len(calls), media_selector.MAX_EXTRA_CANDIDATES)
