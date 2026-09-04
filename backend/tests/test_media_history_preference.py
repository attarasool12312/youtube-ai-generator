"""Cross-job history must not trigger a duplicate provider search."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.media_selector import select_media


def _fake_try_providers(results):
    calls = []

    def fake(providers, query, folder, number, extension, orientation, used_sources, topic_context=None, deadline=None):
        calls.append(set(used_sources))
        outcome = results.pop(0) if results else None
        if outcome is None:
            return None
        path = Path(folder) / f"scene-{number:03d}.{extension}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
        return {**outcome, "media_file": str(path)}

    return fake, calls


class HistoryPreferenceTests(unittest.TestCase):
    def test_cross_job_history_is_allowed_in_single_pass(self):
        weak = {"source": "old-mummy", "relevance_score": 20, "relevance_confidence": "weak"}
        fake, calls = _fake_try_providers([weak])
        with tempfile.TemporaryDirectory() as folder, patch("app.media_selector.try_providers", side_effect=fake), \
                patch("app.media_selector._try_licensed_historical_sources", return_value=None):
            result = select_media("mummy", folder, 2, "video", "landscape",
                                  used_sources={"same-job"}, history_sources={"old-mummy"})
            self.assertEqual(result["source"], "old-mummy")
        self.assertEqual(calls, [{"same-job"}])

    def test_same_job_source_stays_excluded(self):
        strong = {"source": "fresh-good", "relevance_score": 80, "relevance_confidence": "strong"}
        fake, calls = _fake_try_providers([strong])
        with tempfile.TemporaryDirectory() as folder, patch("app.media_selector.try_providers", side_effect=fake):
            result = select_media("mummy", folder, 2, "video", "landscape",
                                  used_sources={"same-job"}, history_sources={"old"})
            self.assertEqual(result["source"], "fresh-good")
        self.assertEqual(calls, [{"same-job"}])

    def test_no_history_is_single_pass(self):
        weak = {"source": "fresh", "relevance_score": 20, "relevance_confidence": "weak"}
        fake, calls = _fake_try_providers([weak])
        with tempfile.TemporaryDirectory() as folder, patch("app.media_selector.try_providers", side_effect=fake), \
                patch("app.media_selector._try_licensed_historical_sources", return_value=None):
            select_media("x", folder, 1, "video", "landscape")
        self.assertEqual(len(calls), 1)
