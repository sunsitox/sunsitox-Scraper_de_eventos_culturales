import os
import unittest
from unittest.mock import Mock, patch

import requests

from eventos.models import Event
from eventos.services.content import NvidiaContentEnricher
from eventos.services.nvidia import NvidiaExtractor


def response_with(content):
    response = Mock(status_code=200)
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "choices": [{"message": {"content": content}}]
    }
    return response


class NvidiaContentTests(unittest.TestCase):
    def tearDown(self):
        NvidiaExtractor.unavailable_reason = ""

    def test_ocr_sends_poster_and_preserves_text(self):
        event = Event(
            title="Festival",
            image_url="https://example.cl/afiche.jpg",
            description="Texto original",
        )
        env = {
            "NVIDIA_API_KEY": "test",
            "NVIDIA_CONTENT_ENRICHMENT": "true",
            "NVIDIA_OCR_ENABLED": "true",
            "NVIDIA_OCR_MODEL": "vision-test",
            "NVIDIA_REWRITE_ENABLED": "false",
        }
        with patch.dict(os.environ, env, clear=False), patch(
            "eventos.services.content.post_nvidia",
            return_value=response_with('{"ocr_text":"Lugar: Plaza Central"}'),
        ) as request:
            stats = NvidiaContentEnricher().enrich([event])
        payload = request.call_args.args[0]
        image_part = payload["messages"][0]["content"][1]
        self.assertEqual(image_part["image_url"]["url"], event.image_url)
        self.assertEqual(event.ocr_text, "Lugar: Plaza Central")
        self.assertEqual(event.source_description, "Texto original")
        self.assertEqual(stats["ocr"], 1)

    def test_rewriter_changes_public_copy_but_keeps_source_copy(self):
        event = Event(title="Obra", description="Descripción publicada por la fuente")
        env = {
            "NVIDIA_API_KEY": "test",
            "NVIDIA_CONTENT_ENRICHMENT": "true",
            "NVIDIA_OCR_ENABLED": "false",
            "NVIDIA_REWRITE_ENABLED": "true",
        }
        with patch.dict(os.environ, env, clear=False), patch(
            "eventos.services.content.post_nvidia",
            return_value=response_with(
                '{"events":[{"index":0,"description":"Versión editorial factual"}]}'
            ),
        ):
            stats = NvidiaContentEnricher().enrich([event])
        self.assertEqual(event.description, "Versión editorial factual")
        self.assertEqual(event.source_description, "Descripción publicada por la fuente")
        self.assertTrue(event.is_rewritten)
        self.assertEqual(stats["rewritten"], 1)

    def test_ocr_timeout_does_not_discard_remaining_images(self):
        events = [
            Event(title="Uno", image_url="https://example.cl/uno.jpg"),
            Event(title="Dos", image_url="https://example.cl/dos.jpg"),
        ]
        env = {
            "NVIDIA_API_KEY": "test",
            "NVIDIA_CONTENT_ENRICHMENT": "true",
            "NVIDIA_OCR_ENABLED": "true",
            "NVIDIA_REWRITE_ENABLED": "false",
        }
        with patch.dict(os.environ, env, clear=False), patch(
            "eventos.services.content.post_nvidia",
            side_effect=requests.Timeout("lento"),
        ) as request:
            stats = NvidiaContentEnricher().enrich(events)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(stats["ocr"], 0)

    def test_completed_rewrite_is_not_repeated_for_ocr_only_candidate(self):
        event = Event(
            title="Evento redactado",
            description="Versión editorial",
            source_description="Texto original",
            image_url="https://example.cl/afiche.jpg",
            is_rewritten=True,
        )
        env = {
            "NVIDIA_API_KEY": "test",
            "NVIDIA_CONTENT_ENRICHMENT": "true",
            "NVIDIA_OCR_ENABLED": "true",
            "NVIDIA_REWRITE_ENABLED": "true",
        }
        with patch.dict(os.environ, env, clear=False), patch(
            "eventos.services.content.post_nvidia",
            return_value=response_with('{"ocr_text":"Texto del afiche"}'),
        ) as request:
            stats = NvidiaContentEnricher().enrich([event])
        self.assertEqual(request.call_count, 1)
        self.assertEqual(stats, {"ocr": 1, "rewritten": 0})
        self.assertEqual(event.description, "Versión editorial")


if __name__ == "__main__":
    unittest.main()
