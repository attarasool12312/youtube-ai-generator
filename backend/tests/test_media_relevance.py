import unittest
from unittest.mock import patch

from app.ai_pipeline import detect_video_style
from app.media_relevance import build_media_query_candidates, build_media_topic_context, score_media_candidate
from app.video_profiles import MANUAL_PROFILE_KEYS, VIDEO_PROFILE_KEYS, normalize_profile_key, script_profile_instructions

FINAL_PROFILES = {
    "auto", "general_explainer", "tech_science", "what_if", "historical_place",
    "historical_event", "geography_travel", "nature_wildlife", "space_astronomy",
    "business_economics", "educational_how_it_works", "health_wellness", "sports",
    "automotive_transport", "food_cooking", "environment_climate", "culture_society",
    "architecture_engineering",
}


class ProfileTests(unittest.TestCase):
    def test_final_eighteen_profiles(self):
        self.assertEqual(VIDEO_PROFILE_KEYS, FINAL_PROFILES)
        self.assertNotIn("historical_person", VIDEO_PROFILE_KEYS)
        self.assertIn("historical_place", VIDEO_PROFILE_KEYS)
        self.assertIn("historical_event", VIDEO_PROFILE_KEYS)

    def test_unknown_profile_is_general(self):
        self.assertEqual(normalize_profile_key("unknown"), "general_explainer")

    def test_new_profiles_have_script_guidance(self):
        for profile in FINAL_PROFILES - {"auto"}:
            self.assertTrue(script_profile_instructions(profile), profile)

    @patch("app.ai_pipeline.call_qwen", return_value="unsupported_profile")
    def test_auto_uncertain_is_general(self, _call):
        self.assertEqual(detect_video_style("The Strange Logic of Left-Handed Snails", "auto"), "general_explainer")

    @patch("app.ai_pipeline.call_qwen", return_value="nature_wildlife")
    def test_auto_clear_specialization(self, _call):
        self.assertEqual(detect_video_style("How Do Octopuses Change Color?", "auto"), "nature_wildlife")


class FlexibleRelevanceTests(unittest.TestCase):
    def context(self, title="How Do Octopuses Change Color and Texture So Quickly?", profile="nature_wildlife"):
        return build_media_topic_context(title, profile)

    def test_query_generation_is_bounded_and_subject_preserved(self):
        variants = build_media_query_candidates("octopus camouflage underwater chromatophore close up", self.context())
        self.assertLessEqual(len(variants), 4)
        self.assertTrue(any("octopus" in item.lower() for item in variants))

    def test_sparse_provider_metadata_is_accepted(self):
        result = score_media_candidate(self.context(), "octopus camouflage")
        self.assertTrue(result.accepted)

    def test_main_subject_general_broll_and_scene_detail_optional(self):
        result = score_media_candidate(self.context(), "octopus chromatophore", title="Octopus swimming underwater")
        self.assertTrue(result.accepted)
        self.assertTrue(result.entity_match)

    def test_obviously_unrelated_subject_is_rejected(self):
        result = score_media_candidate(self.context(), "octopus reef", title="Golden retriever running in city park")
        self.assertFalse(result.accepted)

    def test_nature_habitat_context_is_accepted(self):
        result = score_media_candidate(self.context(), "octopus coral reef", title="Colorful coral reef underwater")
        self.assertTrue(result.accepted)

    def test_how_it_works_component_is_accepted(self):
        context = build_media_topic_context("How Do Escalators Work?", "educational_how_it_works")
        result = score_media_candidate(context, "escalator drive motor", title="Electric drive motor and gears")
        self.assertTrue(result.accepted)

    def test_historical_context_is_accepted(self):
        context = build_media_topic_context("What Happened During the Great Fire of London?", "historical_event")
        result = score_media_candidate(context, "Great Fire London historical map", title="Historic map of London")
        self.assertTrue(result.accepted)

    def test_explicitly_conflicting_historical_date_is_rejected(self):
        context = build_media_topic_context("What Happened During the Great Fire of London?", "historical_event")
        result = score_media_candidate(context, "Great Fire London 1666", title="Great fire London June 23 1861")
        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "conflicting-named-date")
