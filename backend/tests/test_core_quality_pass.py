import sys
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.script_workflow import analyze_spoken_cadence, apply_spoken_cadence_repair, word_count
from app.youtube_thumbnail_generator import _draw_premium_headline, _typography_preset


class ThumbnailTypographyTests(unittest.TestCase):
    def test_exact_multiline_headline_and_safe_position(self):
        headline = "HOW BRIDGES CARRY THOUSANDS OF TONS"
        image = Image.new("RGBA", (1280, 720), (40, 50, 60, 255))
        lines, bounds, preset = _draw_premium_headline(image, headline)
        self.assertEqual(" ".join(lines), headline)
        self.assertGreater(len(lines), 1)
        self.assertGreaterEqual(bounds[0], 60)
        self.assertLessEqual(bounds[2], 800)
        self.assertLessEqual(bounds[3], 650)
        self.assertIn(preset, {"white_dimension", "gold_impact", "cool_premium"})

    def test_preset_is_deterministic_and_blank_does_not_crash(self):
        self.assertEqual(_typography_preset("EXACT TEXT"), _typography_preset("EXACT TEXT"))
        image = Image.new("RGBA", (1280, 720), (0, 0, 0, 255))
        lines, bounds, _ = _draw_premium_headline(image, "")
        self.assertEqual(lines, [])
        self.assertGreaterEqual(bounds[0], 60)


class CadenceTests(unittest.TestCase):
    def fixture(self):
        clause = "the bridge transfers heavy loads through cables into towers and foundations"
        sentences = []
        for index in range(6):
            sentences.append(", and this ".join([clause] * 6) + ".")
        text = " ".join(sentences)
        text += " If this explanation helped, like and subscribe, then share your answer in the comments. Which structural feature should we explain next?"
        return text

    def test_dense_bridge_pattern_gets_one_bounded_repair(self):
        original = self.fixture()
        before = analyze_spoken_cadence(original)
        repaired, reported_before, after, changed = apply_spoken_cadence_repair(original)
        self.assertTrue(before["poor"] and reported_before["poor"] and changed)
        self.assertEqual(word_count(repaired), word_count(original))
        self.assertLess(after["extreme_sentence_count"], before["extreme_sentence_count"])
        self.assertGreater(after["paragraph_count"], 1)
        self.assertIn("like and subscribe", repaired)
        self.assertTrue(repaired.endswith("Which structural feature should we explain next?"))

    def test_natural_script_skips_provider_free_repair(self):
        text = "Natural narration begins clearly. It develops one idea at a time. The ending asks a useful question?"
        repaired, before, after, changed = apply_spoken_cadence_repair(text)
        self.assertEqual(repaired, text)
        self.assertFalse(changed)
        self.assertEqual(before, after)


class QueuePollingTests(unittest.TestCase):
    def test_terminal_handoff_statuses_keep_polling_until_rendered(self):
        source = (ROOT / "backend/app/templates/videos.html").read_text()
        for status in ("waiting", "final_rendering", "youtube_preparing", "youtube_queued", "youtube_uploading"):
            self.assertIn(f'"{status}"', source)
        self.assertIn("if (hasActiveJob(document))", source)
        self.assertNotIn('"completed",\n        "failed",\n        "cancelled"', source)


if __name__ == "__main__":
    unittest.main()
