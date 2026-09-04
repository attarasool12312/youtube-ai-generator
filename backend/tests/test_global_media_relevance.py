import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app.media_selector import normalize_media_source, select_media
from app.scene_media import REUSE_MAX_USES, REUSE_MIN_SCENE_GAP, reuse_eligible_keys, _select_scene_media
from app.stock_providers import pixabay_query_candidates
from app.media_relevance import build_media_topic_context


class MediaModeTests(unittest.TestCase):
    def test_legacy_mixed_and_invalid_become_stock_only(self):
        self.assertEqual(normalize_media_source("mixed"), "stock_only")
        self.assertEqual(normalize_media_source("invalid"), "stock_only")
        self.assertEqual(normalize_media_source(None), "stock_only")

    def test_stock_only_exhaustion_raises_cleanly(self):
        with tempfile.TemporaryDirectory() as folder, \
             patch("app.media_selector.try_providers", return_value=None), \
             patch("app.media_selector._try_licensed_historical_sources", return_value=None):
            with self.assertRaises(RuntimeError):
                select_media(
                    "octopus",
                    folder,
                    1,
                    "video",
                    "landscape",
                    media_source="stock_only",
                )

    def test_legacy_ai_only_is_forced_to_stock_only(self):
        self.assertEqual(normalize_media_source("ai_only"), "stock_only")

        with tempfile.TemporaryDirectory() as folder, \
             patch("app.media_selector.try_providers", return_value=None), \
             patch("app.media_selector._try_licensed_historical_sources", return_value=None):
            with self.assertRaises(RuntimeError):
                select_media(
                    "prompt",
                    folder,
                    1,
                    "image",
                    "landscape",
                    media_source="ai_only",
                )


class QueryAndReuseTests(unittest.TestCase):
    def test_pixabay_receives_practical_variants(self):
        context = build_media_topic_context("How Do Octopuses Change Color?", "nature_wildlife")
        variants = pixabay_query_candidates("octopus camouflage underwater", context)
        self.assertGreaterEqual(len(variants), 2)
        self.assertLessEqual(len(variants), 4)
        self.assertTrue(any(item.lower() in {"octopus", "octopuses"} for item in variants))

    def test_reuse_gap_and_cap(self):
        usage = {"asset": {"last_scene": 2, "uses": 1}}
        self.assertNotIn("asset", reuse_eligible_keys(usage, 2 + REUSE_MIN_SCENE_GAP - 1))
        self.assertIn("asset", reuse_eligible_keys(usage, 2 + REUSE_MIN_SCENE_GAP))
        usage["asset"]["uses"] = REUSE_MAX_USES
        self.assertNotIn("asset", reuse_eligible_keys(usage, 20))

    def test_stock_exhaustion_hands_off_to_ai_without_reuse(self):
        usage = {"source-a": {"last_scene": 1, "uses": 1}}
        with patch("app.scene_media.select_media", side_effect=RuntimeError("fresh exhausted")):
            selected = _select_scene_media("motor", Path("/tmp"), 5, "image", "landscape", {"source-a"}, "prompt", "mixed", False, build_media_topic_context("How Motors Work", "educational_how_it_works"), set(), usage)
        self.assertEqual(selected["provider"], "stock-exhausted")
        self.assertTrue(selected["needs_ai_fallback"])
        self.assertEqual(selected["stock_decision"], "ai_fallback_pending_no_stock")
        self.assertNotIn("reused", selected)

    def test_info_logging_contract_exists(self):
        with tempfile.TemporaryDirectory() as folder, patch("app.media_selector.try_providers", return_value=None), patch("app.media_selector._try_licensed_historical_sources", return_value=None):
            with self.assertLogs("app.media_selector", logging.INFO) as logs, self.assertRaises(RuntimeError):
                select_media("octopus", folder, 3, "video", "landscape", media_source="stock_only")
        self.assertTrue(any("MEDIA scene=3 result=stock_exhausted" in line for line in logs.output))
