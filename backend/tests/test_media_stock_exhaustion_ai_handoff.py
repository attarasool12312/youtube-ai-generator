import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.scene_media import download_scene_images


class StockExhaustionAiHandoffTests(unittest.TestCase):
    def test_stock_exhaustion_is_handed_to_zimage_instead_of_failing_job(self):
        scenes = [{
            "scene_number": 3,
            "narration": "A cable-laying ship lowers a fiber optic cable into the Atlantic Ocean.",
            "visual_subject": "cable-laying ship lowering submarine fiber optic cable",
            "search_query": "cable laying ship submarine fiber optic cable",
            "stock_keywords": ["cable laying ship", "submarine cable"],
            "media_type": "video",
        }]

        with tempfile.TemporaryDirectory() as folder, patch(
            "app.scene_media.select_media",
            side_effect=RuntimeError("no stock candidate"),
        ):
            output = Path(folder)
            result = download_scene_images(
                scenes,
                output,
                video_title="How Submarine Internet Cables Connect the World",
                duration_minutes=5,
            )

            scene = result[0]
            self.assertTrue(scene["needs_ai_fallback"])
            self.assertEqual(scene["stock_decision"], "ai_fallback_pending_no_stock")
            self.assertEqual(scene["provider"], "stock-exhausted")
            self.assertEqual(scene["media_file"], "")

            manifest = [
                json.loads(line)
                for line in (output / "media-manifest.jsonl").read_text().splitlines()
                if line.strip()
            ]
            self.assertEqual(len(manifest), 1)
            self.assertTrue(manifest[0]["needs_ai_fallback"])
            self.assertEqual(manifest[0]["stock_decision"], "ai_fallback_pending_no_stock")

            attributions = json.loads((output / "attributions.json").read_text())
            self.assertEqual(attributions, [])


if __name__ == "__main__":
    unittest.main()
