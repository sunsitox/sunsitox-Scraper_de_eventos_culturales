import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from eventos.analytics import SourceSummary
from eventos.config import Settings, SourceConfig
from eventos.enrichment_queue import EnrichmentQueue
from eventos.models import Event
from eventos.pipeline import EventPipeline
from eventos.services.ai_budget import StageBudget
from eventos.services.content import NvidiaContentEnricher
from eventos.services.nvidia import NvidiaExtractor, NvidiaLocationEnricher
from eventos.services.nvidia_response import chat_content, json_object
from eventos.state import StateStore


def response(content, finish_reason="stop"):
    result = requests.Response()
    result.status_code = 200
    result.headers["Content-Type"] = "application/json"
    result._content = json.dumps({"choices": [{
        "message": {"content": content}, "finish_reason": finish_reason,
    }]}).encode("utf-8")
    return result


class ResponseValidationTests(unittest.TestCase):
    def test_http_envelope_extra_data_has_safe_diagnostic(self):
        result = response("unused")
        result._content = b'{"secret":"DO_NOT_LOG"}\n{"unexpected":true}'
        with self.assertRaisesRegex(ValueError, "Envoltura HTTP") as caught:
            chat_content(result)
        diagnostic = str(caught.exception)
        self.assertIn("HTTP 200", diagnostic)
        self.assertIn("application/json", diagnostic)
        self.assertIn(f"bytes={len(result.content)}", diagnostic)
        self.assertNotIn("DO_NOT_LOG", diagnostic)

    def test_truncated_model_output_is_rejected_even_if_json_is_parseable(self):
        with self.assertRaisesRegex(ValueError, "fin=length"):
            chat_content(response('{"ocr_text":"Parcial"}', "length"))

    def test_empty_or_invalid_envelope_is_rejected(self):
        for payload in ([], {}, {"choices": []}, {"choices": [None]},
                        {"choices": [{"message": {"content": None}}]}):
            with self.subTest(payload=payload):
                result = response("unused")
                result._content = json.dumps(payload).encode()
                with self.assertRaises(ValueError):
                    chat_content(result)

    def test_text_blocks_are_supported_without_stringifying_objects(self):
        self.assertEqual(chat_content(response([
            {"type": "text", "text": '{"ocr_text":'},
            {"type": "text", "text": '"Afiche"}'},
        ])), '{"ocr_text":\n"Afiche"}')
        with self.assertRaises(ValueError):
            chat_content(response([{"type": "image_url", "image_url": "url"}]))

    def test_accepts_only_unambiguous_object_wrappers(self):
        for content in ('{"ocr_text":"Hola"}', '[{"ocr_text":"Hola"}]',
                        '```json\n{"ocr_text":"Hola"}\n```',
                        'Resultado: {"otro":1}\n{"ocr_text":"Hola"}'):
            with self.subTest(content=content):
                self.assertEqual(json_object(content, "ocr_text"), {"ocr_text": "Hola"})
        for content in ('[]', '[{"ocr_text":"Uno"},{"ocr_text":"Dos"}]',
                        '"texto sin estructura"', '{"ocr_text":', '{"otro":1}',
                        '[{"ocr_text":"Parcial"},', '{"ocr_text":"Uno"}{"ocr_text":"Dos"}'):
            with self.subTest(content=content), self.assertRaises(ValueError):
                json_object(content, "ocr_text")


class StageBudgetTests(unittest.TestCase):
    def test_deadline_reduces_request_limit_and_never_becomes_unlimited(self):
        now = [0.0]
        with patch.dict(os.environ, {"NVIDIA_OCR_STAGE_BUDGET_SECONDS": "10"}), patch(
            "eventos.services.ai_budget.time.monotonic", side_effect=lambda: now[0]
        ):
            budget = StageBudget("OCR", "OCR", 1200)
            self.assertEqual(budget.request_limit(100), 10)
            now[0] = 7
            self.assertEqual(budget.request_limit(100), 3)
            now[0] = 10
            self.assertFalse(budget.can_start(2))
            with self.assertRaises(requests.Timeout):
                budget.request_limit(100)

    def test_success_resets_consecutive_failure_count(self):
        with patch.dict(os.environ, {"NVIDIA_OCR_FAILURE_THRESHOLD": "2"}):
            budget = StageBudget("OCR", "OCR", 1200)
            budget.record(False)
            budget.record(True)
            budget.record(False)
            self.assertTrue(budget.can_start(1))
            budget.record(False)
            self.assertFalse(budget.can_start(1))


class EnrichmentResilienceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = StateStore(Path(directory.name) / "state.sqlite3")
        self.env = patch.dict(os.environ, {
            "NVIDIA_API_KEY": "test-not-real",
            "NVIDIA_CONTENT_ENRICHMENT": "true",
            "NVIDIA_OCR_ENABLED": "true",
            "NVIDIA_REWRITE_ENABLED": "false",
            "NVIDIA_LOCATION_ENRICHMENT": "false",
        }, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        NvidiaExtractor.unavailable_reason = ""
        self.addCleanup(setattr, NvidiaExtractor, "unavailable_reason", "")

    @staticmethod
    def events(count=5):
        return [Event(title=f"Obra {i}", image_url=f"https://example.cl/{i}.jpg",
                      source_url=f"https://example.cl/events/{i}",
                      description="Texto original") for i in range(count)]

    def status(self, queue, stage, event):
        return self.store.get("ai:" + stage, queue.key(stage, event), {})["status"]

    def test_ocr_circuit_keeps_unattempted_pending_and_resumes_next_run(self):
        raw = self.events()
        events = [replace(event) for event in raw]
        queue = EnrichmentQueue(self.store)
        with patch("eventos.services.content.post_nvidia", side_effect=requests.Timeout("lento")) as api:
            NvidiaContentEnricher(queue).enrich(events)
        self.assertEqual(api.call_count, 3)
        self.assertEqual([self.status(queue, "ocr", e) for e in events],
                         ["retryable_error"] * 3 + ["pending"] * 2)
        self.assertEqual(NvidiaExtractor.unavailable_reason, "")
        with patch("eventos.services.content.post_nvidia", return_value=response('{"ocr_text":"Texto"}')) as api:
            NvidiaContentEnricher(EnrichmentQueue(self.store)).enrich(raw)
        self.assertEqual(api.call_count, 5)
        first_image = api.call_args_list[0].args[0]["messages"][0]["content"][1]
        self.assertEqual(first_image["image_url"]["url"], raw[3].image_url)

    def test_ocr_deadline_finishes_without_starting_next_image(self):
        now = [0.0]
        events = self.events(3)
        queue = EnrichmentQueue(self.store)

        def delayed_success(*args, **kwargs):
            self.assertEqual(kwargs["max_elapsed"], 10)
            self.assertFalse(args[0]["stream"])
            now[0] += 10
            return response('{"ocr_text":"Completado"}')

        with patch.dict(os.environ, {"NVIDIA_OCR_STAGE_BUDGET_SECONDS": "10"}), patch(
            "eventos.services.ai_budget.time.monotonic", side_effect=lambda: now[0]
        ), patch("eventos.services.content.post_nvidia", side_effect=delayed_success) as api:
            stats = NvidiaContentEnricher(queue).enrich(events)
        self.assertEqual(stats["ocr"], 1)
        self.assertEqual(api.call_count, 1)
        self.assertEqual(self.status(queue, "ocr", events[1]), "pending")

    def test_ocr_success_resets_failure_circuit(self):
        with patch("eventos.services.content.post_nvidia", side_effect=[
            requests.Timeout("uno"), requests.Timeout("dos"),
            response('{"ocr_text":"OK"}'), requests.Timeout("uno"),
            response('{"ocr_text":"OK"}'),
        ]) as api:
            stats = NvidiaContentEnricher().enrich(self.events())
        self.assertEqual(api.call_count, 5)
        self.assertEqual(stats["ocr"], 2)

    def test_invalid_json_trips_circuit_without_overwriting_original_text(self):
        events = self.events()
        queue = EnrichmentQueue(self.store)
        with patch("eventos.services.content.post_nvidia", return_value=response('"no es objeto"')) as api:
            NvidiaContentEnricher(queue).enrich(events)
        self.assertEqual(api.call_count, 3)
        self.assertTrue(all(e.description == "Texto original" and not e.ocr_text for e in events))
        self.assertEqual(self.status(queue, "ocr", events[0]), "retryable_error")

    def test_ocr_failure_does_not_disable_rewrite_stage(self):
        with patch.dict(os.environ, {"NVIDIA_REWRITE_ENABLED": "true"}), patch(
            "eventos.services.content.post_nvidia", side_effect=[
                requests.Timeout("lento"), requests.Timeout("lento"), requests.Timeout("lento"),
                response('{"events":[{"index":0,"description":"Editorial"}]}'),
            ]
        ) as api:
            stats = NvidiaContentEnricher().enrich(self.events())
        self.assertEqual(api.call_count, 4)
        self.assertEqual(stats, {"ocr": 0, "rewritten": 1})

    def test_failed_rewrite_batches_trip_circuit_but_remain_pending(self):
        events = self.events()
        queue = EnrichmentQueue(self.store)
        with patch.dict(os.environ, {"NVIDIA_OCR_ENABLED": "false", "NVIDIA_REWRITE_ENABLED": "true",
                                     "NVIDIA_REWRITE_BATCH_SIZE": "1"}), patch(
            "eventos.services.content.post_nvidia", return_value=response('{"events":[]}')
        ) as api:
            NvidiaContentEnricher(queue).enrich(events)
        self.assertEqual(api.call_count, 3)
        self.assertEqual(self.status(queue, "rewrite", events[0]), "retryable_error")
        self.assertEqual(self.status(queue, "rewrite", events[3]), "pending")

    def test_rewrite_deadline_preserves_first_batch_and_defers_rest(self):
        events = self.events(2)
        now = [0.0]
        queue = EnrichmentQueue(self.store)

        def rewrite(*args, **kwargs):
            self.assertEqual(kwargs["max_elapsed"], 10)
            now[0] = 10
            return response('{"events":[{"index":0,"description":"Editorial"}]}')

        with patch.dict(os.environ, {"NVIDIA_OCR_ENABLED": "false", "NVIDIA_REWRITE_ENABLED": "true",
                                     "NVIDIA_REWRITE_BATCH_SIZE": "1", "NVIDIA_REWRITE_STAGE_BUDGET_SECONDS": "10"}), patch(
            "eventos.services.ai_budget.time.monotonic", side_effect=lambda: now[0]
        ), patch("eventos.services.content.post_nvidia", side_effect=rewrite) as api:
            NvidiaContentEnricher(queue).enrich(events)
        self.assertEqual(api.call_count, 1)
        self.assertTrue(events[0].is_rewritten)
        self.assertFalse(events[1].is_rewritten)
        self.assertEqual(self.status(queue, "rewrite", events[1]), "pending")

    def test_rewrite_rejects_bool_index_and_nontext_description(self):
        event = self.events(1)[0]
        with patch.dict(os.environ, {"NVIDIA_OCR_ENABLED": "false", "NVIDIA_REWRITE_ENABLED": "true"}), patch(
            "eventos.services.content.post_nvidia", return_value=response(
                '{"events":[{"index":false,"description":"Incorrecto"},{"index":0,"description":{"text":"Incorrecto"}}]}'
            )
        ):
            NvidiaContentEnricher().enrich([event])
        self.assertFalse(event.is_rewritten)
        self.assertEqual(event.description, "Texto original")

    def test_location_failures_leave_unstarted_batches_pending(self):
        events = self.events()
        queue = EnrichmentQueue(self.store)
        with patch.dict(os.environ, {"NVIDIA_LOCATION_ENRICHMENT": "true", "NVIDIA_LOCATION_BATCH_SIZE": "1"}), patch(
            "eventos.services.nvidia.post_nvidia", side_effect=requests.Timeout("lento")
        ) as api:
            self.assertEqual(NvidiaLocationEnricher(queue).enrich(events), 0)
        self.assertEqual(api.call_count, 3)
        self.assertEqual(self.status(queue, "location", events[3]), "pending")

    def test_location_deadline_caps_last_request(self):
        now = [0.0]
        events = self.events(2)

        def locate(*args, **kwargs):
            self.assertEqual(kwargs["max_elapsed"], 10)
            now[0] = 10
            return response('{"locations":[{"index":0,"commune":"Talca"}]}')

        with patch.dict(os.environ, {"NVIDIA_LOCATION_ENRICHMENT": "true", "NVIDIA_LOCATION_BATCH_SIZE": "1",
                                     "NVIDIA_LOCATION_STAGE_BUDGET_SECONDS": "10"}), patch(
            "eventos.services.ai_budget.time.monotonic", side_effect=lambda: now[0]
        ), patch("eventos.services.nvidia.post_nvidia", side_effect=locate) as api:
            self.assertEqual(NvidiaLocationEnricher().enrich(events), 1)
        self.assertEqual(api.call_count, 1)
        self.assertEqual(events[0].commune, "Talca")
        self.assertEqual(events[1].commune, "")

    def test_pipeline_still_exports_to_supabase_after_ocr_circuit_opens(self):
        source = SourceConfig.from_dict({"name": "Test", "url": "https://example.cl"})
        events = self.events(2)
        for event in events:
            event.source_name = "Test"
            event.start_date = "2099-01-01T18:00:00"
            event.venue = "Plaza"
            event.commune = "Santiago"
        remote = Mock()
        remote.prepare_for_enrichment.side_effect = lambda rows: (rows, rows)
        pipeline = EventPipeline(repository=Mock(load=Mock(return_value=[source])),
                                 exporter=Mock(), supabase_exporter=remote, state=self.store)
        summary = SourceSummary("Test", "example.cl", "succeeded", 2, 1, 1, 0)
        with patch.dict(os.environ, {"NVIDIA_OCR_FAILURE_THRESHOLD": "1"}), patch(
            "eventos.pipeline.load_dotenv"
        ), patch("eventos.pipeline.configure_nvidia_state"), patch(
            "eventos.pipeline.Settings.load", return_value=Settings(cultural_filter_enabled=False)
        ), patch("eventos.pipeline.write_source_summaries"), patch.object(
            pipeline, "_collect_sources", return_value=(events, [summary])
        ), patch("eventos.services.content.post_nvidia", side_effect=requests.Timeout("lento")) as api:
            result = pipeline.run(push_to_supabase=True)
        self.assertEqual(api.call_count, 1)
        self.assertEqual(len(result), 2)
        remote.export.assert_called_once()
        self.assertEqual(len(remote.export.call_args.args[0]), 2)
        self.assertEqual(len(self.store.get("pipeline", "published")["events"]), 2)


if __name__ == "__main__":
    unittest.main()
