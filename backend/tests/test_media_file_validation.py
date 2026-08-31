"""Downloaded video files must be decodable and sane before entering the render."""
import subprocess
import tempfile
import unittest
from pathlib import Path

from app.stock_providers import _validate_video_file


def _make_video(path, seconds, fps=25):
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", f"color=c=blue:s=64x64:r={fps}:d={seconds}",
         "-pix_fmt", "yuv420p", str(path)],
        check=True,
    )


class VideoValidationTests(unittest.TestCase):
    def test_normal_clip_passes(self):
        with tempfile.TemporaryDirectory() as folder:
            clip = Path(folder) / "ok.mp4"
            _make_video(clip, 3)
            valid, reason = _validate_video_file(clip)
            self.assertTrue(valid, reason)

    def test_audio_heavy_container_with_tiny_video_stream_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            video = Path(folder) / "v.mp4"
            _make_video(video, 2)
            bad = Path(folder) / "bad.mp4"
            subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                 "-i", str(video),
                 "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", "120",
                 "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac",
                 str(bad)],
                check=True,
            )
            valid, reason = _validate_video_file(bad)
            self.assertFalse(valid)
            self.assertIn("container-video-mismatch", reason)

    def test_non_video_bytes_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            junk = Path(folder) / "junk.mp4"
            junk.write_bytes(b"ID3 not a movie" * 100)
            valid, reason = _validate_video_file(junk)
            self.assertFalse(valid, reason)
