import os
import unittest
from unittest.mock import patch

from app.video_renderer import (
    _build_transition_filter_graph,
    _build_transition_plan,
    _image_motion_filter,
    _transition_spec,
)


class VideoMotionTransitionTests(unittest.TestCase):
    def _scenes(self):
        return [
            {
                "scene_number": 1,
                "duration_seconds": 5.0,
                "media_file": "/tmp/one.png",
            },
            {
                "scene_number": 2,
                "duration_seconds": 6.0,
                "media_file": "/tmp/two.mp4",
            },
            {
                "scene_number": 3,
                "duration_seconds": 4.0,
                "media_file": "/tmp/three.png",
            },
        ]

    def test_image_motion_is_smooth_subtle_and_1080p(self):
        value = _image_motion_filter(1920, 1080, 5.0, 7)
        self.assertIn("scale=3840:2160", value)
        self.assertIn("flags=lanczos", value)
        self.assertIn("zoompan=", value)
        self.assertIn("cos(PI*min(on\\,", value)
        self.assertIn("s=1920x1080:fps=30", value)
        self.assertIn("format=yuv420p", value)
        self.assertNotIn("zoom+", value)

    def test_image_motion_is_deterministic_but_varied(self):
        first = _image_motion_filter(1920, 1080, 5.0, 1)
        again = _image_motion_filter(1920, 1080, 5.0, 1)
        other = _image_motion_filter(1920, 1080, 5.0, 2)
        self.assertEqual(first, again)
        self.assertNotEqual(first, other)

    def test_transition_is_restrained_and_can_be_disabled(self):
        scenes = self._scenes()
        spec = _transition_spec(scenes[0], scenes[1])
        self.assertIn(
            spec["name"],
            {"fade", "smoothleft", "smoothright", "wipeleft", "wiperight"},
        )
        self.assertGreaterEqual(spec["duration"], 0.12)
        self.assertLessEqual(spec["duration"], 0.38)

        with patch.dict(os.environ, {"VIDEO_TRANSITIONS": "0"}):
            self.assertIsNone(_transition_spec(scenes[0], scenes[1]))
            self.assertEqual(_build_transition_plan(scenes), {})

    def test_transition_graph_preserves_original_scene_boundaries(self):
        scenes = self._scenes()
        plan = {
            1: {"name": "fade", "duration": 0.30},
            2: {"name": "smoothleft", "duration": 0.25},
        }
        graph, label = _build_transition_filter_graph(scenes, plan)
        self.assertIn(
            "[0:v]tpad=stop_mode=clone:stop_duration=0.300[pad0]",
            graph,
        )
        self.assertIn(
            "[1:v]tpad=stop_mode=clone:stop_duration=0.250[pad1]",
            graph,
        )
        self.assertIn(
            "transition=fade:duration=0.300:offset=5.000",
            graph,
        )
        self.assertIn(
            "transition=smoothleft:duration=0.250:offset=11.000",
            graph,
        )
        self.assertEqual(label, "[xf2]")

    def test_transition_graph_places_captions_after_transitions(self):
        scenes = self._scenes()[:2]
        plan = {1: {"name": "fade", "duration": 0.30}}
        graph, label = _build_transition_filter_graph(
            scenes,
            plan,
            captions_ass="/tmp/captions.ass",
        )
        self.assertIn("xfade=transition=fade", graph)
        self.assertIn("subtitles=filename=", graph)
        self.assertTrue(graph.endswith("[vout]"))
        self.assertEqual(label, "[vout]")


if __name__ == "__main__":
    unittest.main()
