import re
import tempfile
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf

from backend.app import kokoro_service as service
from backend.app.voice_registry import (
    APPROVED_NARRATOR_VOICES,
    DEFAULT_NARRATOR_VOICE,
    VOICE_BY_ID,
    get_narrator_voice,
    validate_voice_speed,
    validated_voice_pool,
    voice_preview_filename,
    voice_preview_path,
    voice_preview_url,
    voice_template_context,
)


class FakePipeline:
    def __init__(self, empty=False):
        self.calls = []
        self.empty = empty

    def __call__(self, text, voice, speed):
        self.calls.append((text, voice, speed))
        if self.empty:
            return iter(())
        return iter(((None, None, np.ones(2400, dtype=np.float32) * 0.1),))


class VoiceRegistryTests(unittest.TestCase):
    def test_registry_is_approved_english_and_unique(self):
        ids = [voice.voice_id for voice in APPROVED_NARRATOR_VOICES]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(v.approved for v in APPROVED_NARRATOR_VOICES))
        self.assertTrue(all(v.voice_id[:2] in {"af", "am", "bf", "bm", "hf", "hm"} for v in APPROVED_NARRATOR_VOICES))
        self.assertTrue(all(v.english_variant in {"American English", "British English", "Hindi"} for v in APPROVED_NARRATOR_VOICES))

    def test_existing_six_and_default_remain_valid(self):
        existing = {"am_michael", "am_fenrir", "am_puck", "af_heart", "af_bella", "af_nicole"}
        self.assertTrue(existing <= VOICE_BY_ID.keys())
        self.assertEqual(DEFAULT_NARRATOR_VOICE, "am_michael")

    def test_voice_and_pool_validation(self):
        self.assertEqual(validated_voice_pool(["am_michael", "af_heart", "am_michael"]), ["am_michael", "af_heart"])
        with self.assertRaises(ValueError):
            get_narrator_voice("not_a_voice")

    def test_speed_validation_bounds(self):
        self.assertEqual(validate_voice_speed("1.15"), 1.15)
        for value in (0.74, 1.26, "fast"):
            with self.assertRaises(ValueError):
                validate_voice_speed(value)

    def test_registry_context_renders_both_voice_selectors(self):
        from jinja2 import Environment

        environment = Environment(autoescape=True)
        context = voice_template_context()
        self.assertEqual(
            context["narrator_voices"],
            tuple(v for v in APPROVED_NARRATOR_VOICES if v.english_variant != "Hindi"),
        )
        for template_name in (
            "partials/create_video_form.html",
            "dashboard.html",
        ):
            source = Path("backend/app/templates", template_name).read_text()
            start = source.index('<select name="narrator_voice"')
            end = source.index("</select>", start) + len("</select>")
            rendered = environment.from_string(source[start:end]).render(
                **context,
            )
            for voice in (v for v in APPROVED_NARRATOR_VOICES if v.english_variant != "Hindi"):
                self.assertIn(f'value="{voice.voice_id}"', rendered)
                self.assertIn(voice.display_label, rendered)
            self.assertIn('value="am_michael" selected', rendered)

    def test_every_approved_voice_has_canonical_safe_preview_mapping(self):
        context = voice_template_context()
        self.assertEqual(context["voice_preview_url_root"], "/static/voice-previews")
        for voice in APPROVED_NARRATOR_VOICES:
            filename = voice_preview_filename(voice.voice_id)
            self.assertEqual(filename, f"{voice.voice_id}.mp3")
            self.assertEqual(voice_preview_path(voice.voice_id).name, filename)
            self.assertEqual(voice_preview_url(voice.voice_id), f"/static/voice-previews/{filename}")
        for unsafe in ("missing", "../am_michael", "am_michael/../../secret"):
            with self.assertRaises(ValueError):
                voice_preview_filename(unsafe)

    def test_templates_use_registry_context_and_preview_root(self):
        context = voice_template_context()
        for template_name in ("partials/create_video_form.html", "dashboard.html"):
            source = Path("backend/app/templates", template_name).read_text()
            self.assertIn("{% for voice in narrator_voices %}", source)
            self.assertIn('data-preview-root="{{ voice_preview_url_root }}"', source)
            self.assertNotIn('/static/voice-previews/am_', source)
            self.assertEqual(context["default_narrator_voice"], "am_michael")


class ChunkingTests(unittest.TestCase):
    def normalized(self, value):
        return re.sub(r"\s+", " ", value).strip()

    def test_paragraphs_and_sentences_preserve_order_without_loss(self):
        text = "First sentence. Second sentence follows.\n\nA new paragraph begins! It ends here."
        chunks = service.chunk_narration_text(text)
        self.assertEqual(self.normalized(" ".join(c.text for c in chunks)), self.normalized(text))
        self.assertTrue(any(c.paragraph_break_after for c in chunks[:-1]))

    def test_name_titles_are_not_split_from_names(self):
        chunks = service._sentences("Dr. Ada Lovelace explained the method. Another idea followed.")
        self.assertEqual(chunks[0], "Dr. Ada Lovelace explained the method.")

    def test_oversized_paragraph_is_bounded_and_not_duplicated(self):
        text = " ".join(f"word{i}" for i in range(500)) + "."
        chunks = service.chunk_narration_text(text)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk.text) <= service.MAX_CHUNK_CHARACTERS for chunk in chunks))
        self.assertEqual(" ".join(c.text for c in chunks), text)

    def test_tiny_final_sentence_is_merged(self):
        text = ("A substantial sentence provides enough narration detail to approach a useful chunk boundary. " * 8) + "Done."
        chunks = service.chunk_narration_text(text)
        self.assertGreaterEqual(len(chunks[-1].text), service.MIN_CHUNK_CHARACTERS)


class SynthesisTests(unittest.TestCase):
    def test_fake_pipeline_concatenates_chunks_and_paragraph_pause(self):
        fake = FakePipeline()
        text = ("One detailed sentence supports a natural narration segment. " * 14) + "\n\nFinal paragraph has a clear ending."
        expected = service.chunk_narration_text(text)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "narration.wav"
            service.generate_narration(text, "am_michael", 1.0, target, pipeline_override=fake)
            audio, rate = sf.read(target)
        self.assertEqual(rate, service.SAMPLE_RATE)
        self.assertEqual([call[0] for call in fake.calls], [chunk.text for chunk in expected])
        expected_samples = len(expected) * 2400
        for chunk in expected[:-1]:
            pause = service.PARAGRAPH_PAUSE_SECONDS if chunk.paragraph_break_after else service.CHUNK_PAUSE_SECONDS
            expected_samples += round(service.SAMPLE_RATE * pause)
        self.assertEqual(len(audio), expected_samples)

    def test_empty_audio_and_text_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "empty.wav"
            with self.assertRaises(ValueError):
                service.generate_narration("   ", "am_michael", 1.0, target, pipeline_override=FakePipeline())
            with self.assertRaises(RuntimeError):
                service.generate_narration("Useful text.", "am_michael", 1.0, target, pipeline_override=FakePipeline(empty=True))


    def test_unload_pipelines_clears_cached_instances(self):
        sentinel = object()
        original = dict(service._pipelines)
        try:
            service._pipelines.clear()
            service._pipelines["a"] = sentinel
            service.unload_pipelines()
            self.assertEqual(service._pipelines, {})
        finally:
            service._pipelines.clear()
            service._pipelines.update(original)

    def test_british_voice_selects_british_pipeline(self):
        self.assertEqual(get_narrator_voice("bf_emma").pipeline_language, "b")
        self.assertEqual(get_narrator_voice("af_heart").pipeline_language, "a")


if __name__ == "__main__":
    unittest.main()
