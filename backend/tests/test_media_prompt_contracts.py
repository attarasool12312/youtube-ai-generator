"""Prompt text contracts: no broken newlines, universal query rules present."""
import unittest
from unittest.mock import patch

from app.ai_pipeline import _enrich_scene_batch_once, build_media_script_context
from app.video_profiles import VIDEO_PROFILE_KEYS, profile_detection_prompt


class ProfilePromptTests(unittest.TestCase):
    def test_detection_prompt_has_real_newlines_and_every_profile(self):
        prompt = profile_detection_prompt("How Elevators Work")
        self.assertNotIn(".n-", prompt)
        for key in VIDEO_PROFILE_KEYS:
            self.assertIn(key, prompt)
        self.assertIn("archaeology", prompt)


class ScenePromptTests(unittest.TestCase):
    def test_scene_prompt_carries_universal_query_rules(self):
        captured = {}

        def fake_call(prompt):
            captured["prompt"] = prompt
            return {"scenes": [{"scene_number": 1, "search_query": "elevator counterweight",
                                "stock_keywords": ["elevator"], "media_type": "video"}]}

        with patch("app.ai_pipeline.call_scene_json", side_effect=fake_call):
            _enrich_scene_batch_once("How Elevators Work", "educational_how_it_works",
                                     [{"scene_number": 1, "narration": "The counterweight balances the car."}])
        prompt = captured["prompt"]
        for fragment in ("photographable nouns", "editorial or mood words", "animal", "use image when",
                         "abstract concept"):
            self.assertIn(fragment, prompt)

    def test_scene_prompt_carries_whole_video_and_neighbor_context(self):
        captured = {}
        scenes = [
            {"scene_number": 1, "narration": "A ship lowers submarine cable into the sea."},
            {"scene_number": 2, "narration": "Repeaters amplify the signal on the seabed."},
            {"scene_number": 3, "narration": "The cable reaches a landing station."},
        ]

        def fake_call(prompt):
            captured["prompt"] = prompt
            return {"scenes": [{
                "scene_number": 2,
                "visual_subject": "submarine cable repeater",
                "search_query": "submarine cable repeater seabed",
                "required_terms": ["submarine cable repeater"],
                "search_variants": ["undersea fiber repeater"],
                "stock_keywords": ["submarine cable", "repeater", "seabed"],
                "negative_keywords": ["wifi repeater", "road traffic"],
                "media_type": "image",
            }]}

        brief = {
            "summary": "Submarine fiber cables carry internet data across oceans.",
            "main_subject": "submarine fiber optic cables",
            "ambiguities": [{"term": "cable", "meaning": "submarine telecom cable", "avoid": ["cable car"]}],
        }
        with patch("app.ai_pipeline.call_scene_json", side_effect=fake_call):
            enriched = _enrich_scene_batch_once(
                "How Deep Sea Cables Carry the Internet",
                "educational_how_it_works",
                [scenes[1]],
                media_script_context=brief,
                all_scenes=scenes,
            )
        prompt = captured["prompt"]
        self.assertIn("Whole-video media context", prompt)
        self.assertIn("previous_narration", prompt)
        self.assertIn("next_narration", prompt)
        self.assertIn("negative_keywords", prompt)
        self.assertIn("required_terms", prompt)
        self.assertIn("Resolve ambiguous words", prompt)
        self.assertEqual(enriched[0]["visual_subject"], "submarine cable repeater")
        self.assertEqual(enriched[0]["required_terms"], ["submarine cable repeater"])
        self.assertEqual(enriched[0]["negative_keywords"], ["wifi repeater", "road traffic"])

    def test_media_script_context_is_clean_and_nonfatal(self):
        payload = {
            "summary": "  A video about submarine fiber cables.  ",
            "main_subject": " submarine fiber optic cables ",
            "entities": ["cable laying ship", "cable landing station"],
            "visual_terms": ["seabed", "repeater"],
            "ambiguities": [{
                "term": "cable",
                "meaning": "submarine telecommunications cable",
                "avoid": ["cable car", "bridge cable"],
            }],
        }
        with patch("app.ai_pipeline._call_media_context_json", return_value=payload):
            result = build_media_script_context("Deep Sea Cables", "general_explainer", "A complete script.")
        self.assertEqual(result["main_subject"], "submarine fiber optic cables")
        self.assertEqual(result["ambiguities"][0]["avoid"], ["cable car", "bridge cable"])

        with patch("app.ai_pipeline._call_media_context_json", side_effect=RuntimeError("offline")):
            self.assertEqual(build_media_script_context("Deep Sea Cables", "general_explainer", "script"), {})
