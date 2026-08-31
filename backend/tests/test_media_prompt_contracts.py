"""Prompt text contracts: no broken newlines, universal query rules present."""
import unittest
from unittest.mock import patch

from app.ai_pipeline import _enrich_scene_batch_once
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
