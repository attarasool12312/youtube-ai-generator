import unittest

from app.stock_providers import (
    MAX_SCORED_CANDIDATES, _pixabay_descriptive_title,
    pixabay_query_candidates, provider_query_candidates,
)
from app.media_relevance import build_media_topic_context, scene_context


class ProviderPrecisionTests(unittest.TestCase):
    def test_pixabay_uses_page_slug_as_descriptive_title_not_tag_bag(self):
        self.assertEqual(
            _pixabay_descriptive_title({
                "pageURL": "https://pixabay.com/photos/agar-breeding-ground-red-60571/"
            }),
            "agar breeding ground red",
        )
        self.assertEqual(
            _pixabay_descriptive_title({
                "pageURL": "https://pixabay.com/photos/colors-binding-red-yellow-blue-2440860/"
            }),
            "colors binding red yellow blue",
        )

    def test_provider_query_broadening_is_bounded_for_speed(self):
        base = build_media_topic_context(
            "How Deep Sea Cables Carry the Internet", "educational_how_it_works"
        )
        context = scene_context(
            base, "submarine fiber optic cable seabed",
            visual_subject="submarine fiber optic cable",
            search_variants=("undersea fiber cable", "ocean floor telecom cable", "subsea cable"),
        )
        self.assertLessEqual(
            len(provider_query_candidates(context.scene_query, context)), 2
        )
        self.assertLessEqual(
            len(pixabay_query_candidates(context.scene_query, context)), 2
        )
        self.assertLessEqual(MAX_SCORED_CANDIDATES, 12)
