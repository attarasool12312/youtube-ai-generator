import json
import pathlib
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.download_filenames import content_disposition_for_video, safe_video_download_name  # noqa: E402
from app.script_validator import classify_cta_intents, deduplicate_cta_intents  # noqa: E402
from app.script_workflow import (  # noqa: E402
    MAX_EXPANSION_ATTEMPTS, clean_script, create_validated_script, ensure_ending_requirements, profile_ending_question,
    remove_sequential_word_markers, trim_script_to_max_words,
)
from app.video_profiles import VIDEO_PROFILE_KEYS  # noqa: E402
from app.youtube_thumbnail_generator import (  # noqa: E402
    _clean_headline, _fallback_headline, _generate_thumbnail_concept, _local_thumbnail_concept,
    generate_youtube_thumbnail_prompt,
)

PROFILES = tuple(sorted(VIDEO_PROFILE_KEYS))
TITLES = {
    "auto": ("Ocean Currents Explained", "The Story of Paper"),
    "general_explainer": ("Why Maps Distort Continents", "How Calendars Work"),
    "tech_science": ("Quantum Computing", "CRISPR Gene Editing"),
    "what_if": ("What If Gravity Doubled?", "What If Pollinators Vanished?"),
    "historical_place": ("The History of Petra", "Angkor Wat Through Time"),
    "historical_event": ("The Haitian Revolution", "The Apollo 11 Landing"),
    "geography_travel": ("The Atacama Desert", "The Mekong Delta"),
    "nature_wildlife": ("Monarch Butterflies", "Mangrove Forests"),
    "space_astronomy": ("The Andromeda Galaxy", "Saturn's Rings"),
    "business_economics": ("Compound Interest", "The Economics of Coffee"),
    "educational_how_it_works": ("How Do Heat Pumps Work?", "Inside Water Treatment"),
    "health_wellness": ("Why Sleep Affects Memory", "How Exercise Supports Health"),
    "sports": ("How Goalkeepers Read Penalties", "Why Tennis Uses Spin"),
    "automotive_transport": ("Regenerative Braking", "How Trains Stay on Rails"),
    "food_cooking": ("Sourdough Fermentation", "Why Onions Brown"),
    "environment_climate": ("Offshore Wind Farms", "How Wetlands Store Carbon"),
    "culture_society": ("Why Accents Develop", "How Traditions Change"),
    "architecture_engineering": ("Suspension Bridges", "How Arches Carry Loads"),
}


class ThumbnailQualityTests(unittest.TestCase):
    def make_job(self, title="Ocean Currents Explained"):
        temp = tempfile.TemporaryDirectory()
        path = pathlib.Path(temp.name)
        (path / "package.json").write_text(json.dumps({
            "title": title, "script": "A detailed final script about the exact topic. " * 20,
        }))
        return temp, SimpleNamespace(output_directory=str(path), title=title, script="")

    def test_clean_headline_exposes_blank_and_malformed_values(self):
        self.assertEqual(_clean_headline(""), "")
        self.assertEqual(_clean_headline("   \n "), "")
        self.assertEqual(_clean_headline("<> -:|"), "")
        self.assertEqual(_clean_headline("  A REAL   HEADLINE  "), "A REAL HEADLINE")

    def test_fallback_is_title_derived_and_nonblank(self):
        self.assertEqual(_fallback_headline("How Does a Jet Engine Work?"), "How Does a Jet Engine Work?")
        self.assertNotEqual(_fallback_headline("A History of Glass"), "THE HIDDEN STORY")
        self.assertTrue(_fallback_headline("  "))

    def test_valid_external_ai_prompt_contains_exact_headline_and_no_contradiction(self):
        temp, job = self.make_job()
        responses = [
            SimpleNamespace(text="Useful topic summary", provider="gemini"),
            SimpleNamespace(text='{"headline":"OCEANS IN MOTION"}', provider="gemini"),
            SimpleNamespace(
                text='{"same_video":true,"replacement_headline":""}',
                provider="gemini",
            ),
            SimpleNamespace(text='{"image_prompt":"Render OCEANS IN MOTION over a dramatic ocean current map"}', provider="gemini"),
        ]
        try:
            with patch("app.youtube_thumbnail_generator.call_gemini", side_effect=responses):
                result = generate_youtube_thumbnail_prompt(job)
        finally:
            temp.cleanup()
        self.assertEqual(result.headline, "OCEANS IN MOTION")
        self.assertIn(result.headline, result.visual_prompt)
        self.assertNotIn("do not render text", result.visual_prompt.casefold())
        self.assertNotIn("render no text", result.visual_prompt.casefold())

    def test_external_ai_text_free_contradiction_is_rejected(self):
        temp, job = self.make_job("Ocean Currents")
        responses = [
            SimpleNamespace(text="Ocean circulation summary", provider="gemini"),
            SimpleNamespace(text='{"headline":"OCEANS IN MOTION"}', provider="gemini"),
            SimpleNamespace(text='{"image_prompt":"A text-free ocean background without text"}', provider="gemini"),
        ]
        try:
            with patch("app.youtube_thumbnail_generator.call_gemini", side_effect=responses), patch(
                "app.youtube_thumbnail_generator._generate_thumbnail_concept", side_effect=RuntimeError("retry")
            ):
                result = generate_youtube_thumbnail_prompt(job)
        finally:
            temp.cleanup()
        self.assertNotIn("text-free", result.visual_prompt.casefold())
        self.assertIn(result.headline, result.visual_prompt)


    def test_blank_whitespace_malformed_and_ai_failure_use_deterministic_fallback(self):
        for bad in ("", "   ", "not json"):
            with self.subTest(value=bad):
                temp, job = self.make_job("Why Leaves Change Color")
                try:
                    with patch("app.youtube_thumbnail_generator.call_gemini", side_effect=RuntimeError(bad)), patch(
                        "app.youtube_thumbnail_generator._generate_thumbnail_concept", side_effect=RuntimeError("retry failed")
                    ):
                        result = generate_youtube_thumbnail_prompt(job)
                finally:
                    temp.cleanup()
                self.assertTrue(result.headline)
                self.assertIn(result.headline, result.visual_prompt)
                self.assertIn("Why Leaves Change Color", result.visual_prompt)
                self.assertNotIn("do not render any text", result.visual_prompt.casefold())

    def test_local_ai_failure_uses_text_free_topic_fallback(self):
        with patch("app.youtube_thumbnail_generator._generate_thumbnail_concept", side_effect=RuntimeError("offline")):
            headline, prompt, provider = _local_thumbnail_concept(
                "How Solar Panels Work", "A final script."
            )
        self.assertTrue(headline)
        self.assertIn("Solar Panels", prompt)
        self.assertIn("Do not render text", prompt)
        self.assertIn("deterministic-local-fallback", provider)


    def test_local_concept_prompt_is_text_free_and_headline_is_separate(self):
        response = SimpleNamespace(
            text='{"headline":"QUANTUM POWER","visual_prompt":"A cryogenic quantum processor with a clean text-safe area"}',
            provider="gemini",
        )
        with patch("app.youtube_thumbnail_generator.call_gemini", return_value=response) as call:
            headline, visual, _provider = _generate_thumbnail_concept(
                title="Quantum Computing", script="Quantum processors use qubits."
            )
        self.assertEqual(headline, "QUANTUM POWER")
        self.assertNotIn(headline, visual)
        self.assertTrue(
            any(
                "Do NOT ask the image model to render text" in item.args[0]
                for item in call.call_args_list
            )
        )

    def test_profiles_titles_and_durations_have_consistent_fallback_package(self):
        self.assertEqual(set(TITLES), set(PROFILES))
        for profile, titles in TITLES.items():
            for title in titles:
                for duration in (3, 5, 8, 10, 15, 20, 25, 30):
                    with self.subTest(profile=profile, title=title, duration=duration):
                        headline = _fallback_headline(title)
                        self.assertTrue(headline)
                        self.assertIn(headline, f'Render this exact headline: "{headline}"')

    def test_regression_external_prompt_is_complete_not_blank_space(self):
        temp, job = self.make_job("how to earn money online in 2026?")
        try:
            with patch("app.youtube_thumbnail_generator.call_gemini", side_effect=RuntimeError("offline")), patch(
                "app.youtube_thumbnail_generator._generate_thumbnail_concept", side_effect=RuntimeError("offline")
            ):
                result = generate_youtube_thumbnail_prompt(job)
        finally:
            temp.cleanup()
        self.assertTrue(result.headline)
        self.assertIn(result.headline, result.visual_prompt)
        self.assertIn("money online", result.visual_prompt.casefold())
        self.assertNotIn("reserve a deliberate text-safe area", result.visual_prompt.casefold())


class DownloadFilenameTests(unittest.TestCase):
    def test_safe_filename_matrix(self):
        cases = (
            ("A Normal Video", "A-Normal-Video.mp4"),
            ("Why This?", "Why-This.mp4"),
            (r"Path / slash \\ test", "Path-slash-test.mp4"),
            ('Quotes " and: colon', "Quotes-and-colon.mp4"),
            ("Café 東京", "Café-東京.mp4"),
            ("Emoji 🚀 Launch", "Emoji-Launch.mp4"),
            ("CON", "video-CON.mp4"),
            ("   ", "video-7.mp4"),
            ("safe\r\nX-Evil: yes", "safe-X-Evil-yes.mp4"),
        )
        for title, expected in cases:
            with self.subTest(title=title):
                self.assertEqual(safe_video_download_name(title, 7), expected)

    def test_long_names_are_bounded_stable_and_disambiguatable(self):
        name = safe_video_download_name("Very long title " * 40, 3)
        self.assertLessEqual(len(name), 124)
        self.assertTrue(name.endswith(".mp4"))
        self.assertNotEqual(safe_video_download_name("", 3), safe_video_download_name("", 4))

    def test_content_disposition_has_safe_ascii_and_utf8_filename(self):
        header = content_disposition_for_video("Café 東京\r\nInjected: yes", 9)
        self.assertNotIn("\r", header)
        self.assertNotIn("\n", header)
        self.assertIn('filename="Cafe-Injected-yes.mp4"', header)
        self.assertIn("filename*=UTF-8''", header)
        self.assertIn("%E6%9D%B1%E4%BA%AC", header)

    def test_endpoint_serves_existing_physical_final_video(self):
        from app import main
        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder)
            job_dir = root / "42"
            job_dir.mkdir()
            physical = job_dir / "final-video.mp4"
            physical.write_bytes(b"video-bytes")
            job = SimpleNamespace(id=42, title="My Finished Video?", output_directory=str(job_dir))
            db = Mock()
            db.get.return_value = job
            request = SimpleNamespace(session={"user_id": 1})
            with patch.object(main, "OUTPUT_ROOT", root):
                response = main.download_job_video(42, request, db)
            self.assertEqual(response.headers["content-length"], str(len(b"video-bytes")))
            self.assertIn("My-Finished-Video.mp4", response.headers["content-disposition"])
            self.assertTrue(physical.is_file())


class CTAQualityTests(unittest.TestCase):
    def test_single_leaked_cta_is_moved_to_ending(self):
        text = "Subscribe for more. First narration sentence. Final narration sentence?"
        cleaned = deduplicate_cta_intents(text)
        self.assertTrue(cleaned.endswith("Subscribe for more."))


    def test_valid_combined_cta_survives(self):
        text = "A useful conclusion. If you enjoyed this video, like it and subscribe for more."
        self.assertEqual(deduplicate_cta_intents(text), text)

    def test_duplicate_like_subscribe_and_paraphrases_are_deduplicated(self):
        cases = (
            "Give this video a like. If you enjoyed this video, like it and subscribe for more.",
            "Subscribe for more. Don't forget to subscribe to the channel.",
            "If you enjoyed this, like it and subscribe for more. Give the video a like.",
            "Please support our channel. Help this channel by subscribing for more.",
        )
        for text in cases:
            with self.subTest(text=text):
                cleaned = deduplicate_cta_intents(text)
                intents = [classify_cta_intents(x) for x in cleaned.split(". ")]
                flattened = [intent for group in intents for intent in group]
                self.assertEqual(len(flattened), len(set(flattened)))

    def test_normal_like_and_subscribe_words_are_not_ctas(self):
        from app.script_validator import validate_script
        body = "Clouds look like waves over the mountain. " * 70
        report = validate_script(body + " Which pattern stands out most? Share your answer in the comments. Subscribe for more.", 3)
        self.assertIn("Ending is missing a natural like CTA.", report["issues"])
        self.assertFalse(classify_cta_intents("Clouds look like waves over the mountain."))
        self.assertFalse(classify_cta_intents("Clients subscribe to an event stream through the API."))

    def test_profile_questions_are_preserved_with_single_cta(self):
        for profile in PROFILES:
            with self.subTest(profile=profile):
                question = profile_ending_question(profile)
                text = (
                    "This conclusion summarizes the topic clearly. " + question + " "
                    "Share your answer in the comments. Give this video a like. "
                    "If you enjoyed this video, like it and subscribe for more."
                )
                cleaned = clean_script(text)
                self.assertIn(question, cleaned)
                self.assertEqual(cleaned.casefold().count("subscribe"), 1)
                self.assertEqual(sum("LIKE" in classify_cta_intents(s) for s in cleaned.split(". ")), 1)

    def test_valid_generic_question_is_completed_with_profile_question(self):
        body = " ".join(
            f"Sentence {index} explains a distinct and useful technical detail."
            for index in range(45)
        )
        script = (
            body
            + " What detail surprised you most? Share your answer in the comments. "
            + "If you enjoyed this video, like it and subscribe for more."
        )
        repaired = ensure_ending_requirements(script, 3, "tech_science")
        self.assertIn(profile_ending_question("tech_science"), repaired)
        intents = [
            intent
            for sentence in repaired.split(". ")
            for intent in classify_cta_intents(sentence)
        ]
        self.assertEqual(len(intents), len(set(intents)))

    def test_validated_workflow_enforces_profile_question_even_when_generic_question_is_valid(self):
        generic = (
            "A sufficiently detailed narration closes naturally. " * 60
            + "What surprised you most? Share your answer in the comments. "
            + "If you enjoyed this video, like it and subscribe for more."
        )
        generated = {
            "sections": [generic],
            "target_words": 435,
            "outline": ["Complete explanation"],
            "outline_provider": "mock",
            "section_providers": ["mock"],
        }
        valid = {
            "valid": True, "issues": [], "word_count": 435,
            "target_words": 435, "minimum_words": 405, "maximum_words": 498,
        }
        with patch("app.script_workflow.generate_initial_script", return_value=generated), patch(
            "app.script_workflow.validate_script", return_value=valid
        ):
            result = create_validated_script("Technical Topic", 3, "tech_science")
        self.assertIn(profile_ending_question("tech_science"), result["script"])

    def test_underlength_workflow_has_two_bounded_expansion_attempts(self):
        self.assertEqual(MAX_EXPANSION_ATTEMPTS, 2)

    def test_marker_cleanup_and_max_word_trimming_remain(self):
        marked = "We (10) learn (11) how (12) systems (13) work."
        self.assertNotIn("(10)", remove_sequential_word_markers(marked))
        script = "Opening sentence introduces the complete topic clearly. " + " ".join(
            f"Middle sentence {i} contains useful explanatory detail." for i in range(20)
        ) + " Which step matters most? Share your answer in the comments. If you enjoyed this video, like it and subscribe for more."
        trimmed = trim_script_to_max_words(script, 45)
        self.assertLessEqual(len(trimmed.split()), 45)
        self.assertIn("subscribe", trimmed.casefold())

    def test_all_durations_can_receive_profile_specific_ending(self):
        for profile in PROFILES:
            for duration in (3, 5, 8, 10, 15, 20, 25, 30):
                with self.subTest(profile=profile, duration=duration):
                    # Use a duration-sized body so ending repair remains inside limits.
                    body = " ".join(["This topic has important details to understand."] * (duration * 20))
                    repaired = ensure_ending_requirements(body, duration, profile)
                    self.assertIn(profile_ending_question(profile), repaired)
                    self.assertIn("subscribe", repaired.casefold())


if __name__ == "__main__":
    unittest.main()
