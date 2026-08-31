"""Weak first hits keep searching; the best candidate wins and lands on the scene path."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import media_selector
from app.media_selector import try_providers


def _provider(name, outcomes, calls):
    def fake(query, path, orientation, used_sources=None, offset=0, topic_context=None):
        calls.append((name, query))
        outcome = outcomes.pop(0) if outcomes else None
        if outcome is None:
            return None
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(name.encode())
        return dict(outcome)
    fake.__name__ = name
    return fake


class BestOfSearchTests(unittest.TestCase):
    def test_strong_first_result_returns_immediately(self):
        calls = []
        provider = _provider("pexels_video", [{"source": "a", "relevance_score": 40, "relevance_entity_match": True}], calls)
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(media_selector, "provider_query_candidates", side_effect=lambda q, c=None: [q]):
            result = try_providers([provider], "wolf pack", Path(folder), 1, "mp4", "landscape", set())
            self.assertEqual(result["source"], "a")
            self.assertTrue(Path(result["media_file"]).name == "scene-001.mp4")
        self.assertEqual(len(calls), 1)

    def test_weak_result_is_beaten_by_later_provider(self):
        calls = []
        weak = _provider("pexels_video", [{"source": "weak", "relevance_score": 12, "relevance_entity_match": False}], calls)
        strong = _provider("coverr_video", [{"source": "strong", "relevance_score": 36, "relevance_entity_match": True}], calls)
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(media_selector, "provider_query_candidates", side_effect=lambda q, c=None: [q]):
            result = try_providers([weak, strong], "blockbuster store", Path(folder), 2, "mp4", "landscape", set())
            self.assertEqual(result["source"], "strong")
            final = Path(folder) / "scene-002.mp4"
            self.assertEqual(Path(result["media_file"]), final)
            self.assertEqual(final.read_bytes(), b"coverr_video")
            self.assertEqual([name for name, _ in calls], ["pexels_video", "coverr_video"])
            self.assertEqual(list(Path(folder).glob("*")), [final])

    def test_all_weak_returns_best_of(self):
        calls = []
        weak1 = _provider("pexels_video", [{"source": "w1", "relevance_score": 8, "relevance_entity_match": False}], calls)
        weak2 = _provider("coverr_video", [{"source": "w2", "relevance_score": 14, "relevance_entity_match": False}], calls)
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(media_selector, "provider_query_candidates", side_effect=lambda q, c=None: [q]):
            result = try_providers([weak1, weak2], "netflix envelope", Path(folder), 3, "mp4", "landscape", set())
            self.assertEqual(result["source"], "w2")
            final = Path(folder) / "scene-003.mp4"
            self.assertEqual(final.read_bytes(), b"coverr_video")
            self.assertEqual(list(Path(folder).glob("*")), [final])

    def test_extra_candidate_cap_bounds_the_search(self):
        calls = []
        outcomes = [{"source": f"w{i}", "relevance_score": 10, "relevance_entity_match": False} for i in range(9)]
        provider = _provider("pexels_video", outcomes, calls)
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(media_selector, "provider_query_candidates",
                             side_effect=lambda q, c=None: [f"{q} v{i}" for i in range(9)]):
            result = try_providers([provider], "q", Path(folder), 4, "mp4", "landscape", set())
            self.assertIsNotNone(result)
        self.assertLessEqual(len(calls), media_selector.MAX_EXTRA_CANDIDATES + 2)
