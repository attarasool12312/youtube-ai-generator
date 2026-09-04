import pathlib
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch


BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.ai_pipeline import (  # noqa: E402
    detect_video_style,
    enrich_scene_batch,
    split_script_into_scenes,
)
from app.download_filenames import safe_video_download_name  # noqa: E402
from app.kokoro_service import MAX_CHUNK_CHARACTERS, chunk_narration_text  # noqa: E402
from app.media_relevance import (  # noqa: E402
    build_media_topic_context,
    query_has_topic_anchor,
    score_media_candidate,
    validate_scene_query,
)
from app.script_validator import (  # noqa: E402
    classify_cta_intents,
    get_word_count_limits,
)
from app.script_workflow import (  # noqa: E402
    ensure_ending_requirements,
    profile_ending_question,
    remove_sequential_word_markers,
)
from app.video_profiles import MANUAL_PROFILE_KEYS, VIDEO_PROFILE_KEYS  # noqa: E402
from app.youtube_thumbnail_generator import _fallback_headline  # noqa: E402


DURATIONS = (3, 5, 8, 10, 15, 20, 25, 30)
PROFILE_TITLES = {
    "auto": ("Why Soap Removes Grease", "The Science of Morning Fog"),
    "general_explainer": ("Why Zippers Get Stuck", "How Time Zones Shape Daily Life"),
    "tech_science": ("Perovskite Solar Cells", "How Lidar Maps a Forest"),
    "what_if": ("What If Earth Had Two Moons?", "What If All Satellites Failed?"),
    "historical_place": ("The History of Machu Picchu", "Borobudur Through Time"),
    "historical_event": ("The Meiji Restoration", "The Great Fire of London"),
    "geography_travel": ("The Okavango Delta", "Life Along the Danube River"),
    "nature_wildlife": ("Leafcutter Ants", "Kelp Forest Ecosystems"),
    "space_astronomy": ("Europa's Hidden Ocean", "The Pillars of Creation"),
    "business_economics": ("Why Shipping Containers Changed Trade", "How Credit Unions Work"),
    "educational_how_it_works": ("How Escalators Work", "Inside a Reverse Osmosis System"),
    "health_wellness": ("Why Sleep Affects Memory", "How Exercise Supports Health"),
    "sports": ("How Goalkeepers Read Penalties", "Why Tennis Uses Spin"),
    "automotive_transport": ("Regenerative Braking", "How Trains Stay on Rails"),
    "food_cooking": ("Sourdough Fermentation", "Why Onions Brown"),
    "environment_climate": ("Offshore Wind Farms", "How Wetlands Store Carbon"),
    "culture_society": ("Why Accents Develop", "How Traditions Change"),
    "architecture_engineering": ("Suspension Bridges", "How Arches Carry Loads"),
}


class Phase7ProfileMatrixTests(unittest.TestCase):
    def test_all_profiles_multiple_titles_keep_topic_contracts(self):
        self.assertEqual(set(PROFILE_TITLES), set(VIDEO_PROFILE_KEYS))
        for profile, titles in PROFILE_TITLES.items():
            for title in titles:
                with self.subTest(profile=profile, title=title):
                    context = build_media_topic_context(title, profile)
                    self.assertEqual(context.profile, profile)
                    self.assertTrue(context.primary_topic)
                    repaired = validate_scene_query("detailed close view", context)
                    self.assertTrue(query_has_topic_anchor(repaired.final_query, context))
                    accepted = score_media_candidate(
                        context,
                        repaired.final_query,
                        title=f"{context.primary_topic} detailed close view",
                    )
                    self.assertTrue(accepted.accepted)
                    self.assertTrue(_fallback_headline(title))
                    self.assertTrue(safe_video_download_name(title, 71).endswith(".mp4"))

                    ending = ensure_ending_requirements(
                        "This explanation closes with the most useful practical idea.",
                        3,
                        profile,
                    )
                    self.assertIn(profile_ending_question(profile), ending)
                    intents = [
                        intent
                        for sentence in ending.split(". ")
                        for intent in classify_cta_intents(sentence)
                    ]
                    self.assertEqual(len(intents), len(set(intents)))

    def test_auto_detection_and_manual_propagation(self):
        auto_cases = {
            "A Biography of Chien-Shiung Wu": "general_explainer",
            "What If Earth's Oceans Froze?": "what_if",
            "How Does a Ballpoint Pen Work?": "educational_how_it_works",
        }
        for title, expected in auto_cases.items():
            with self.subTest(title=title):
                with patch("app.ai_pipeline.call_qwen", return_value=expected):
                    self.assertEqual(detect_video_style(title, "auto"), expected)
        for profile in MANUAL_PROFILE_KEYS:
            with self.subTest(profile=profile):
                with patch("app.ai_pipeline.call_qwen") as detector:
                    self.assertEqual(detect_video_style("Unrelated title", profile), profile)
                    detector.assert_not_called()

    def test_scene_enrichment_repairs_every_batch_with_one_context(self):
        title = "How Escalators Work"
        scenes = [
            {"scene_number": number, "narration": f"Scene {number} explains a component safely."}
            for number in range(1, 26)
        ]

        def response(prompt):
            import json

            supplied = json.loads(prompt.split("Scenes: ", 1)[1])
            return {"scenes": [
                {
                    "scene_number": scene["scene_number"],
                    "search_query": "component close view",
                    "stock_keywords": ["component"],
                    "image_prompt": "Technical component",
                    "video_prompt": "Slow camera move",
                    "media_type": "stock_video",
                }
                for scene in supplied
            ]}

        real_builder = build_media_topic_context
        with patch("app.ai_pipeline.build_media_topic_context", wraps=real_builder) as builder, patch(
            "app.ai_pipeline.build_media_script_context", return_value={"summary": "test context"}
        ), patch("app.ai_pipeline.call_scene_json", side_effect=response):
            enriched = enrich_scene_batch(title, "educational_how_it_works", scenes)

        self.assertEqual(builder.call_count, 1)
        self.assertEqual(len(enriched), len(scenes))
        context = real_builder(title, "educational_how_it_works")
        self.assertTrue(all(query_has_topic_anchor(scene["search_query"], context) for scene in enriched))


class Phase7DurationMatrixTests(unittest.TestCase):
    def test_all_durations_preserve_targets_and_independent_contracts(self):
        title = "How Escalators Work"
        profile = "educational_how_it_works"
        baseline_headline = _fallback_headline(title)
        baseline_filename = safe_video_download_name(title, 82)
        context = build_media_topic_context(title, profile)
        baseline_query = validate_scene_query("drive chain diagram", context).final_query

        for minutes in DURATIONS:
            with self.subTest(minutes=minutes):
                target, minimum, maximum = get_word_count_limits(minutes)
                self.assertEqual(target, minutes * 145)
                self.assertLessEqual(minimum, target)
                self.assertGreaterEqual(maximum, target)

                words = [f"word{index}" for index in range(target)]
                scenes = split_script_into_scenes(" ".join(words), minutes)
                self.assertEqual(len(scenes), minutes * 8)
                self.assertEqual(sum(len(scene["narration"].split()) for scene in scenes), target)
                self.assertEqual(_fallback_headline(title), baseline_headline)
                self.assertEqual(safe_video_download_name(title, 82), baseline_filename)
                self.assertEqual(validate_scene_query("drive chain diagram", context).final_query, baseline_query)

                ending = ensure_ending_requirements("A concise final explanation.", minutes, profile)
                self.assertIn(profile_ending_question(profile), ending)
                self.assertNotRegex(remove_sequential_word_markers(ending), r"\(\d+\)")

    def test_long_kokoro_plan_is_bounded_complete_and_ordered(self):
        paragraphs = [
            " ".join(f"section{section}word{word}." for word in range(180))
            for section in range(25)
        ]
        narration = "\n\n".join(paragraphs)
        chunks = chunk_narration_text(narration)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(0 < len(chunk.text) <= MAX_CHUNK_CHARACTERS for chunk in chunks))
        rebuilt = " ".join(chunk.text for chunk in chunks)
        for section in range(25):
            self.assertIn(f"section{section}word0", rebuilt)
            self.assertIn(f"section{section}word179", rebuilt)
        positions = [rebuilt.index(f"section{section}word0") for section in range(25)]
        self.assertEqual(positions, sorted(positions))


if __name__ == "__main__":
    unittest.main()
