import pathlib
import tempfile
import unittest
from unittest.mock import Mock, patch

from app.media_relevance import build_media_topic_context
from app.openverse_media import _candidate, _license_details


def item(title="Great Fire of London painting", source="https://source.test/fire", creator="Archive", license_slug="by", description="", tags=None):
    return {
        "title": title, "foreign_landing_url": source, "creator": creator,
        "license": license_slug, "license_version": "4.0",
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "description": description, "tags": tags or [], "filetype": "jpg",
        "url": "https://images.test/fire.jpg",
    }


class OpenverseMediaTests(unittest.TestCase):
    def setUp(self):
        self.context = build_media_topic_context("What Happened During the Great Fire of London?", "historical_event")

    def test_license_acceptance_and_rejection(self):
        for slug in ("pdm", "cc0", "by"):
            self.assertIsNotNone(_license_details(item(license_slug=slug)))
        for slug in ("by-sa", "by-nc", "by-nd", "", "unknown"):
            self.assertIsNone(_license_details(item(license_slug=slug)))
        self.assertIsNone(_license_details(item(license_slug="by", creator="")))

    def test_exact_and_contextual_historical_media_are_accepted(self):
        exact, reason = _candidate(item(), "Great Fire London painting", self.context, set(), set())
        self.assertIsNone(reason)
        self.assertTrue(exact["entity_match"])
        contextual, reason = _candidate(
            item(title="Historic map of London", source="https://source.test/map"),
            "Great Fire London historical map", self.context, set(), set(),
        )
        self.assertIsNone(reason)
        self.assertIsNotNone(contextual)

    def test_obviously_wrong_event_is_rejected(self):
        candidate, reason = _candidate(
            item(title="French Revolution battle in Paris", description="Paris uprising"),
            "Great Fire London", self.context, set(), set(),
        )
        self.assertIsNone(candidate)
        self.assertEqual(reason, "relevance")


class HistoricalRoutingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = pathlib.Path(self.temp.name)
        self.context = build_media_topic_context("What Happened During the Great Fire of London?", "historical_event")

    def tearDown(self):
        self.temp.cleanup()

    def call(self):
        from app.media_selector import select_media
        return select_media(
            "Great Fire London", self.folder, 1, "image", "landscape", set(),
            "AI prompt", "stock_only", prefer_wikimedia=True,
            topic_context=self.context, history_sources=set(),
        )

    def test_wikimedia_then_openverse_and_no_ai(self):
        wiki = Mock(side_effect=RuntimeError("exhausted"))
        openverse = Mock(return_value={"provider": "openverse", "source": "ov"})
        with patch("app.media_selector.LICENSED_HISTORICAL_PROVIDERS", (("wikimedia", wiki), ("openverse", openverse))), patch("app.media_selector.flux_image") as ai:
            self.assertEqual(self.call()["provider"], "openverse")
        wiki.assert_called_once()
        openverse.assert_called_once()
        ai.assert_not_called()

    def test_exhaustion_never_invokes_ai(self):
        failed = Mock(side_effect=RuntimeError("exhausted"))
        with patch("app.media_selector.LICENSED_HISTORICAL_PROVIDERS", (("wikimedia", failed), ("openverse", failed))), patch("app.media_selector.try_providers", return_value=None), patch("app.media_selector.flux_image") as ai:
            with self.assertRaises(RuntimeError):
                self.call()
        ai.assert_not_called()
