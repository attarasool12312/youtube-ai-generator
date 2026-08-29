"""History is a preference, not a ban: a weak fresh pick loses to a strong older asset."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.media_selector import select_media


def _fake_try_providers(results):
    calls = []

    def fake(providers, query, folder, number, extension, orientation, used_sources, topic_context=None):
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
    def test_strong_history_asset_beats_weak_fresh_asset(self):
        weak = {"source": "fresh-fox", "relevance_score": 6, "relevance_entity_match": False}
        strong = {"source": "old-mummy", "relevance_score": 40, "relevance_entity_match": True}
        fake, calls = _fake_try_providers([weak, strong])
        with tempfile.TemporaryDirectory() as folder, patch("app.media_selector.try_providers", side_effect=fake):
            result = select_media("mummy", folder, 2, "image", "landscape", used_sources={"same-job"},
                                  history_sources={"old-mummy"})
            self.assertEqual(result["source"], "old-mummy")
            self.assertTrue(result["history_reuse"])
            self.assertEqual(Path(result["media_file"]).parent, Path(folder))
            self.assertTrue(Path(result["media_file"]).exists())
        self.assertEqual(calls[0], {"same-job", "old-mummy"})
        self.assertEqual(calls[1], {"same-job"})  # history allowed, same-job still excluded

    def test_strong_fresh_asset_does_not_trigger_second_pass(self):
        strong = {"source": "fresh-good", "relevance_score": 36, "relevance_entity_match": True}
        fake, calls = _fake_try_providers([strong])
        with tempfile.TemporaryDirectory() as folder, patch("app.media_selector.try_providers", side_effect=fake):
            result = select_media("mummy", folder, 2, "image", "landscape", history_sources={"old"})
            self.assertEqual(result["source"], "fresh-good")
        self.assertEqual(len(calls), 1)

    def test_weak_fresh_kept_when_history_is_no_better(self):
        weak = {"source": "fresh", "relevance_score": 8, "relevance_entity_match": False}
        worse = {"source": "old", "relevance_score": 6, "relevance_entity_match": False}
        fake, calls = _fake_try_providers([weak, worse])
        with tempfile.TemporaryDirectory() as folder, patch("app.media_selector.try_providers", side_effect=fake):
            result = select_media("x", folder, 1, "image", "landscape", history_sources={"old"})
            self.assertEqual(result["source"], "fresh")
            self.assertFalse(list((Path(folder) / "history-alt").glob("*")))

    def test_no_history_means_single_pass(self):
        weak = {"source": "fresh", "relevance_score": 6, "relevance_entity_match": False}
        fake, calls = _fake_try_providers([weak])
        with tempfile.TemporaryDirectory() as folder, patch("app.media_selector.try_providers", side_effect=fake):
            select_media("x", folder, 1, "image", "landscape")
        self.assertEqual(len(calls), 1)
