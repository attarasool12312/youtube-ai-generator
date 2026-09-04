import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.media_relevance import build_media_topic_context
from app.media_selector import select_media


class LicensedFallbackPathTests(unittest.TestCase):
    def test_licensed_fallback_does_not_delete_its_own_winning_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            stock_path = folder / "scene-012.jpg"
            stock_path.write_bytes(b"stock")
            stock = {
                "provider": "pixabay",
                "source": "stock-source",
                "media_file": str(stock_path),
                "media_type": "image",
                "relevance_score": 35,
                "relevance_confidence": "weak",
            }

            def licensed_provider(query, target_folder, number, orientation,
                                  used_sources, history_sources, topic_context):
                path = Path(target_folder) / f"scene-{number:03d}.jpg"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"licensed")
                return {
                    "provider": "wikimedia",
                    "source": "licensed-source",
                    "media_file": str(path),
                    "media_type": "image",
                    "relevance_score": 90,
                    "relevance_confidence": "strong",
                }

            context = build_media_topic_context(
                "How Deep Sea Cables Carry the Internet",
                "historical_documentary",
            )
            with patch(
                "app.media_selector._stock_with_history_preference",
                return_value=stock,
            ), patch(
                "app.media_selector._try_licensed_historical_sources",
                side_effect=licensed_provider,
            ):
                result = select_media(
                    "submarine cable manufacturing process",
                    folder,
                    12,
                    "image",
                    "landscape",
                    topic_context=context,
                    prefer_wikimedia=True,
                )

            final_path = folder / "scene-012.jpg"
            self.assertEqual(result["provider"], "wikimedia")
            self.assertEqual(Path(result["media_file"]), final_path)
            self.assertTrue(final_path.is_file())
            self.assertEqual(final_path.read_bytes(), b"licensed")


if __name__ == "__main__":
    unittest.main()
