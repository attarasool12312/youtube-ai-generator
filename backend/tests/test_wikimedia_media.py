import io
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

import httpx
from PIL import Image

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.media_relevance import build_media_topic_context  # noqa: E402
from app.wikimedia_media import (  # noqa: E402
    API, MAX_API_PAGES, _allowed_license, _candidate_from_page,
    _query_candidates, download_image,
)


def response(status=200, json_data=None, content=b"", content_type=None, url=API):
    headers = {"content-type": content_type} if content_type else {}
    return httpx.Response(
        status, json=json_data if json_data is not None else None,
        content=content if json_data is None else None, headers=headers,
        request=httpx.Request("GET", url),
    )


def image_bytes(fmt="JPEG"):
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (20, 40, 60)).save(buffer, fmt)
    return buffer.getvalue()


def page(title="File:Ada Lovelace portrait.jpg", source="https://commons.test/ada",
         license_name="Public domain", author="Museum", mime="image/jpeg",
         description="Portrait of Ada Lovelace", image_url=None, categories=None):
    return {
        "title": title,
        "categories": categories or [{"title": "Category:Ada Lovelace"}],
        "imageinfo": [{
            "mime": mime,
            "thumburl": image_url or f"https://images.test/{source.rsplit('/', 1)[-1]}.jpg",
            "descriptionurl": source,
            "extmetadata": {
                "LicenseShortName": {"value": license_name},
                "LicenseUrl": {"value": "https://license.test/value"},
                "Artist": {"value": author},
                "ImageDescription": {"value": description},
            },
        }],
    }


class FakeClient:
    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.handler(url, kwargs, len(self.calls))

    def close(self):
        pass


class WikimediaMediaTests(unittest.TestCase):
    def setUp(self):
        self.context = build_media_topic_context("Who Was Ada Lovelace?", "general_explainer")
        self.output_dir = tempfile.TemporaryDirectory()
        self.output = pathlib.Path(self.output_dir.name) / "result.jpg"

    def tearDown(self):
        self.output_dir.cleanup()

    def test_query_candidates_preserve_proper_name_and_context(self):
        variants = _query_candidates(
            "Ada Lovelace analytical engine historical portrait", self.context
        )
        self.assertEqual(variants[0], "Ada Lovelace analytical engine historical portrait")
        self.assertIn("Ada Lovelace analytical engine", variants)
        self.assertIn("Ada Lovelace", variants)
        self.assertTrue(all("Ada Lovelace" in item for item in variants))
        self.assertNotIn("War of the", _query_candidates("War of the Currents archival photo", self.context))

    def test_api_continuation_is_used_when_first_page_is_weak(self):
        api_count = 0
        def handler(url, kwargs, _count):
            nonlocal api_count
            if url == API:
                api_count += 1
                if api_count == 1:
                    return response(json_data={"query": {"pages": []}, "continue": {"gsrcontinue": "next", "continue": "gsrcontinue||"}})
                return response(json_data={"query": {"pages": [page()]}})
            return response(content=image_bytes(), content_type="image/jpeg", url=url)
        client = FakeClient(handler)
        result = download_image("Ada Lovelace laboratory", self.output, topic_context=self.context, client=client)
        self.assertEqual(result["provider"], "wikimedia")
        self.assertGreaterEqual(api_count, 2)
        self.assertLessEqual(api_count, MAX_API_PAGES)
        second_params = [kwargs["params"] for url, kwargs in client.calls if url == API][1]
        self.assertEqual(second_params["gsrcontinue"], "next")

    def test_entity_in_description_can_outweigh_missing_scene_context(self):
        candidate, reason = _candidate_from_page(
            page(title="File:Portrait 1840.jpg", description="A portrait of Ada Lovelace",
                 categories=[{"title": "Category:Mathematicians"}]),
            "Ada Lovelace analytical engine notes", self.context, set(), set(),
        )
        self.assertIsNone(reason)
        self.assertTrue(candidate["entity_match"])
        self.assertEqual(candidate["context_match_count"], 0)

    def test_wrong_multiword_entity_is_rejected(self):
        wrong = page(
            title="File:Grace Hopper portrait.jpg", description="Grace Hopper at work",
            categories=[{"title": "Category:Grace Hopper"}],
        )
        candidate, reason = _candidate_from_page(
            wrong, "Ada Lovelace portrait", self.context, set(), set()
        )
        self.assertIsNone(candidate)
        self.assertEqual(reason, "relevance")

    def test_license_policy_is_not_broadened(self):
        self.assertTrue(_allowed_license("Public domain"))
        self.assertTrue(_allowed_license("CC0 1.0"))
        self.assertTrue(_allowed_license("CC BY 4.0"))
        for blocked in ("CC BY-SA 4.0", "CC BY-NC 4.0", "CC BY-ND 4.0", "unknown"):
            with self.subTest(license=blocked):
                self.assertFalse(_allowed_license(blocked))
        candidate, reason = _candidate_from_page(
            page(license_name="CC BY-SA 4.0"), "Ada Lovelace", self.context, set(), set()
        )
        self.assertIsNone(candidate)
        self.assertEqual(reason, "license")

    def test_mime_and_document_candidates_are_rejected(self):
        candidate, reason = _candidate_from_page(
            page(mime="application/pdf"), "Ada Lovelace", self.context, set(), set()
        )
        self.assertIsNone(candidate)
        self.assertEqual(reason, "MIME")
        candidate, reason = _candidate_from_page(
            page(title="File:Ada Lovelace book cover.jpg"), "Ada Lovelace", self.context, set(), set()
        )
        self.assertIsNone(candidate)
        self.assertEqual(reason, "banned/document-type")

    def test_download_failure_continues_to_next_ranked_candidate(self):
        broken = page(source="https://commons.test/broken", image_url="https://images.test/broken.jpg")
        good = page(title="File:Ada Lovelace painting.jpg", source="https://commons.test/good",
                    image_url="https://images.test/good.jpg")
        def handler(url, kwargs, _count):
            if url == API:
                return response(json_data={"query": {"pages": [broken, good]}})
            if url.endswith("broken.jpg"):
                return response(status=404, url=url)
            return response(content=image_bytes(), content_type="image/jpeg", url=url)
        result = download_image("Ada Lovelace", self.output, topic_context=self.context, client=FakeClient(handler))
        self.assertEqual(result["source"], "https://commons.test/good")
        self.assertTrue(self.output.exists())

    def test_transient_timeout_retries_but_permanent_404_does_not(self):
        attempts = 0
        def handler(url, kwargs, _count):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise httpx.ReadTimeout("temporary", request=httpx.Request("GET", url))
            return response(json_data={"query": {"pages": []}}, url=url)
        with patch("app.wikimedia_media.time.sleep"):
            with self.assertRaises(RuntimeError):
                download_image("Ada Lovelace", self.output, topic_context=self.context, client=FakeClient(handler))
        self.assertGreaterEqual(attempts, 2)

    def test_current_duplicate_rejected_but_history_is_last_resort(self):
        candidate, reason = _candidate_from_page(
            page(), "Ada Lovelace", self.context, {"https://commons.test/ada"}, set()
        )
        self.assertIsNone(candidate)
        self.assertEqual(reason, "duplicate-current-job")
        def handler(url, kwargs, _count):
            if url == API:
                return response(json_data={"query": {"pages": [page()]}})
            return response(content=image_bytes(), content_type="image/jpeg", url=url)
        result = download_image(
            "Ada Lovelace", self.output, topic_context=self.context,
            history_sources={"https://commons.test/ada"}, client=FakeClient(handler),
        )
        self.assertEqual(result["source"], "https://commons.test/ada")

    def test_fresh_candidate_is_preferred_over_global_history(self):
        old = page(source="https://commons.test/old", image_url="https://images.test/old.jpg")
        fresh = page(title="File:Ada Lovelace 1843.jpg", source="https://commons.test/fresh",
                     image_url="https://images.test/fresh.jpg")
        def handler(url, kwargs, _count):
            if url == API:
                return response(json_data={"query": {"pages": [old, fresh]}})
            return response(content=image_bytes(), content_type="image/jpeg", url=url)
        result = download_image(
            "Ada Lovelace", self.output, topic_context=self.context,
            history_sources={"https://commons.test/old"}, client=FakeClient(handler),
        )
        self.assertEqual(result["source"], "https://commons.test/fresh")

    def test_search_attempts_are_bounded(self):
        def handler(url, kwargs, count):
            self.assertEqual(url, API)
            return response(json_data={
                "query": {"pages": [page(mime="application/pdf", source=f"x{count}")]},
                "continue": {"gsrcontinue": f"next-{count}", "continue": "gsrcontinue||"},
            })
        client = FakeClient(handler)
        with self.assertRaises(RuntimeError):
            download_image(
                "Ada Lovelace historical portrait", self.output,
                topic_context=self.context, client=client,
            )
        self.assertLessEqual(len(client.calls), MAX_API_PAGES)

    def test_metadata_contract_is_preserved(self):
        def handler(url, kwargs, _count):
            if url == API:
                return response(json_data={"query": {"pages": [page(license_name="CC BY 4.0", author="Archive") ]}})
            return response(content=image_bytes(), content_type="image/jpeg", url=url)
        result = download_image("Ada Lovelace", self.output, topic_context=self.context, client=FakeClient(handler))
        for key in (
            "provider", "source", "title", "author", "license", "license_url",
            "attribution", "media_file", "media_type", "wikimedia_query",
            "wikimedia_relevance_score",
        ):
            self.assertIn(key, result)
        self.assertEqual(result["author"], "Archive")
        self.assertEqual(result["license"], "CC BY 4.0")


if __name__ == "__main__":
    unittest.main()
