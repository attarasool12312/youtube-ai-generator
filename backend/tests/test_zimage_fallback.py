import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import zimage_fallback


class _FakeProcess:
    def __init__(self, lines=None, return_code=0):
        self.stdout = iter(lines or [])
        self._return_code = return_code

    def wait(self):
        return self._return_code


class _WarmProcess:
    def __init__(self):
        self.terminated = False

    def poll(self):
        return 0 if self.terminated else None

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0


class ZImageFallbackTests(unittest.TestCase):
    def test_prompt_is_literal_and_blocks_wrong_meanings(self):
        prompt = zimage_fallback.build_zimage_prompt(
            {
                "visual_subject": "submarine fiber optic cable repeater",
                "narration": "A repeater restores the optical signal along the cable.",
                "negative_keywords": ["cable car", "extension cord"],
            },
            "How Submarine Cables Carry the Internet",
            "educational_how_it_works",
        )
        self.assertIn("submarine fiber optic cable repeater", prompt)
        self.assertNotIn("cable car", prompt)
        self.assertIn("without readable writing", prompt)

    def test_success_replaces_weak_stock_and_updates_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "job"
            output.mkdir()
            stock = output / "media" / "scene-001.mp4"
            stock.parent.mkdir()
            stock.write_bytes(b"stock")
            scene = {
                "scene_number": 1,
                "narration": "A cable-laying ship lowers a submarine fiber cable.",
                "visual_subject": "cable-laying ship submarine fiber cable",
                "search_query": "cable laying ship submarine cable",
                "media_file": str(stock),
                "provider": "pixabay",
                "media_provider": "pixabay",
                "media_type": "video",
                "needs_ai_fallback": True,
                "stock_decision": "ai_fallback_pending",
            }
            (output / "media-manifest.jsonl").write_text(
                json.dumps({
                    "scene_number": 1,
                    "provider": "pixabay",
                    "media_type": "video",
                    "needs_ai_fallback": True,
                    "stock_decision": "ai_fallback_pending",
                }) + "\n",
                encoding="utf-8",
            )
            (output / "attributions.json").write_text(
                json.dumps([{"scene_number": 1, "provider": "pixabay"}]),
                encoding="utf-8",
            )
            fake_python = root / "python"
            fake_script = root / "zimage_batch.py"
            fake_python.write_text("")
            fake_script.write_text("")

            def fake_popen(command, **kwargs):
                request = json.loads((output / "zimage-request.json").read_text(encoding="utf-8"))
                item = request["items"][0]
                generated = Path(item["output_path"])
                generated.parent.mkdir(parents=True, exist_ok=True)
                generated.write_bytes(b"x" * 2048)
                result_path = Path(command[command.index("--result") + 1])
                result_path.write_text(json.dumps({"items": [{
                    "scene_number": 1,
                    "ok": True,
                    "output_path": str(generated),
                    "seconds": 14.2,
                }]}), encoding="utf-8")
                return _FakeProcess(["ZIMAGE_PROGRESS|1|1|1\n"])

            progress = []
            with patch.object(zimage_fallback, "_zimage_python", return_value=fake_python), patch.object(
                zimage_fallback, "_batch_script", return_value=fake_script
            ), patch.object(zimage_fallback, "_release_ollama_gpu"), patch.object(
                zimage_fallback.subprocess, "Popen", side_effect=fake_popen
            ):
                result = zimage_fallback.apply_zimage_fallbacks(
                    [scene],
                    output,
                    video_title="How Submarine Cables Carry the Internet",
                    progress_callback=lambda done, total: progress.append((done, total)),
                )

            self.assertEqual(result[0]["provider"], "z-image-turbo")
            self.assertEqual(result[0]["media_type"], "image")
            self.assertFalse(result[0]["needs_ai_fallback"])
            self.assertEqual(result[0]["stock_decision"], "ai_generated")
            self.assertEqual(result[0]["stock_fallback_file"], str(stock))
            self.assertTrue(Path(result[0]["media_file"]).is_file())
            self.assertEqual(progress, [(1, 1)])

            manifest = json.loads((output / "media-manifest.jsonl").read_text().strip())
            self.assertEqual(manifest["provider"], "z-image-turbo")
            self.assertEqual(manifest["stock_decision"], "ai_generated")
            self.assertEqual(json.loads((output / "attributions.json").read_text()), [])

    def test_failed_generation_keeps_stock_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "job"
            output.mkdir()
            stock = output / "scene-001.mp4"
            stock.write_bytes(b"stock")
            scene = {
                "scene_number": 1,
                "narration": "Scene text",
                "search_query": "specific subject",
                "media_file": str(stock),
                "provider": "pexels",
                "media_type": "video",
                "needs_ai_fallback": True,
            }
            fake_python = root / "python"
            fake_script = root / "script.py"
            fake_python.write_text("")
            fake_script.write_text("")

            def fake_popen(command, **kwargs):
                result_path = Path(command[command.index("--result") + 1])
                result_path.write_text(json.dumps({"items": [{
                    "scene_number": 1,
                    "ok": False,
                    "error": "generation failed",
                }]}), encoding="utf-8")
                return _FakeProcess(return_code=1)

            with patch.object(zimage_fallback, "_zimage_python", return_value=fake_python), patch.object(
                zimage_fallback, "_batch_script", return_value=fake_script
            ), patch.object(zimage_fallback, "_release_ollama_gpu"), patch.object(
                zimage_fallback.subprocess, "Popen", side_effect=fake_popen
            ):
                result = zimage_fallback.apply_zimage_fallbacks([scene], output)

            self.assertEqual(result[0]["media_file"], str(stock))
            self.assertTrue(stock.is_file())
            self.assertEqual(result[0]["stock_decision"], "ai_fallback_failed_stock_retained")


    def test_warmup_starts_waiting_worker_without_request_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_python = root / "python"
            fake_script = root / "zimage_batch.py"
            fake_python.write_text("")
            fake_script.write_text("")
            fake_process = _WarmProcess()

            with patch.object(zimage_fallback, "_zimage_python", return_value=fake_python), patch.object(
                zimage_fallback, "_batch_script", return_value=fake_script
            ), patch.object(zimage_fallback, "_release_ollama_gpu"), patch.object(
                zimage_fallback.subprocess, "Popen", return_value=fake_process
            ) as popen:
                handle = zimage_fallback.start_zimage_warmup(root / "job")

            self.assertIsNotNone(handle)
            self.assertFalse(Path(handle["request_path"]).exists())
            command = popen.call_args.args[0]
            self.assertIn("--wait-for-request", command)
            self.assertIn("--ready", command)

    def test_cancel_warmup_terminates_live_worker(self):
        process = _WarmProcess()
        zimage_fallback.cancel_zimage_warmup({"process": process})
        self.assertTrue(process.terminated)


if __name__ == "__main__":
    unittest.main()
