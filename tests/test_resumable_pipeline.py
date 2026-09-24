import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from eventos.config import SourceConfig, Settings
from eventos.models import Event
from eventos.consolidation import deduplicate
from eventos.state import StateStore, DetailCache
from eventos.enrichment_queue import EnrichmentQueue
from eventos.services.content import NvidiaContentEnricher
from eventos.services.nvidia import NvidiaRateLimiter, NvidiaExtractor, post_nvidia, retry_after_seconds
from eventos.connectors.generic import GenericConnector, candidate_links
from eventos.connectors.eventon import EventOnWordPressConnector
from eventos.pipeline import EventPipeline
from eventos.analytics import SourceSummary
from tools.checkpoint_state import snapshot


def response(content):
    result = Mock(status_code=200, headers={})
    result.json.return_value = {"choices": [{"message": {"content": content}}]}
    return result


class ResumableTests(unittest.TestCase):
    def test_calendar_downloads_are_not_html_candidates(self):
        html = '''<a href="/event-directory/mes/?ical=1">Eventos</a>
        <a href="/event-directory/mes/?outlook-ical=1">Outlook</a>
        <a href="/eventos.ics">Calendario de eventos</a>
        <a href="webcal://example.cl/eventos">Agenda</a>
        <a href="/event-directory/obra/">Obra</a>'''
        self.assertEqual(candidate_links(html, "https://example.cl"),
                         ["https://example.cl/event-directory/obra/"])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.now = [1000000.0]
        self.store = StateStore(Path(self.directory.name) / "pipeline.sqlite3", clock=lambda: self.now[0])
        self.env = patch.dict(os.environ, {
            "NVIDIA_API_KEY": "test", "NVIDIA_CONTENT_ENRICHMENT": "true",
            "NVIDIA_REWRITE_ENABLED": "false", "NVIDIA_OCR_ENABLED": "true",
            "NVIDIA_OCR_MAX_IMAGES": "100", "NVIDIA_LOCATION_ENRICHMENT": "false",
        }, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        NvidiaExtractor.unavailable_reason = ""

    def test_cycle_resumes_until_finished_then_starts_fresh(self):
        cycle = self.store.begin_cycle()
        self.assertEqual(self.store.begin_cycle(), cycle)
        self.store.finish_cycle()
        self.assertNotEqual(self.store.begin_cycle(), cycle)

    def test_expired_cycle_starts_new_collection(self):
        cycle = self.store.begin_cycle()
        self.now[0] += 86401
        self.assertNotEqual(self.store.begin_cycle(), cycle)

    def test_atomic_backup_restores_queue_and_cycle(self):
        cycle = self.store.begin_cycle()
        self.store.put("ai:ocr", "item", {"status": "retryable_error"})
        path = Path(self.directory.name) / "backup/state.sqlite3"
        snapshot(self.store.path, path)
        restored = StateStore(path, clock=lambda: self.now[0])
        self.assertEqual(restored.begin_cycle(), cycle)
        self.assertEqual(restored.get("ai:ocr", "item")["status"], "retryable_error")

    def test_timeout_leaves_retryable_item_and_next_image_completes(self):
        raw = [Event(title="Uno", image_url="https://example.cl/1"), Event(title="Dos", image_url="https://example.cl/2")]
        queue = EnrichmentQueue(self.store)
        with patch("eventos.services.content.post_nvidia", side_effect=[requests.Timeout("timeout"), response('{"ocr_text":"Texto dos"}')]) as api:
            NvidiaContentEnricher(queue).enrich([replace(e) for e in raw])
        self.assertEqual(api.call_count, 2)
        self.assertEqual(self.store.get("ai:ocr", queue.key("ocr", raw[0]))["status"], "retryable_error")
        resumed = [replace(e) for e in raw]
        with patch("eventos.services.content.post_nvidia", return_value=response('{"ocr_text":"Texto uno"}')) as api:
            NvidiaContentEnricher(EnrichmentQueue(self.store)).enrich(resumed)
        self.assertEqual(api.call_count, 1)
        self.assertEqual([e.ocr_text for e in resumed], ["Texto uno", "Texto dos"])

    def test_interrupted_processing_can_be_claimed_in_next_execution(self):
        event = Event(title="Uno", image_url="https://example.cl/1")
        queue = EnrichmentQueue(self.store)
        self.assertTrue(queue.ready("ocr", event))
        queue.start("ocr", event)
        self.assertFalse(queue.ready("ocr", event))
        self.assertTrue(EnrichmentQueue(self.store).ready("ocr", event))

    def test_empty_valid_ocr_is_completed_and_not_repeated(self):
        event = Event(title="Sin texto", image_url="https://example.cl/1")
        with patch("eventos.services.content.post_nvidia", return_value=response('{"ocr_text":""}')) as api:
            NvidiaContentEnricher(EnrichmentQueue(self.store)).enrich([replace(event)])
            NvidiaContentEnricher(EnrichmentQueue(self.store)).enrich([replace(event)])
        self.assertEqual(api.call_count, 1)

    def test_cached_editorial_survives_missing_api_key(self):
        event = Event(title="Obra", source_description="Original", description="Original")
        queue = EnrichmentQueue(self.store)
        queue.ready("rewrite", event)
        queue.finish("rewrite", queue.start("rewrite", event), {"description": "Editorial", "is_rewritten": True})
        with patch.dict(os.environ, {"NVIDIA_API_KEY": ""}), patch("eventos.services.content.post_nvidia") as api:
            NvidiaContentEnricher(EnrichmentQueue(self.store)).enrich([event])
        api.assert_not_called()
        self.assertEqual(event.description, "Editorial")

    def test_image_limit_leaves_remaining_items_pending(self):
        events = [Event(title=str(i), image_url=f"https://example.cl/{i}") for i in range(3)]
        queue = EnrichmentQueue(self.store)
        with patch.dict(os.environ, {"NVIDIA_OCR_MAX_IMAGES": "1"}), patch("eventos.services.content.post_nvidia", return_value=response('{"ocr_text":"Texto"}')):
            NvidiaContentEnricher(queue).enrich(events)
        self.assertEqual(self.store.get("ai:ocr", queue.key("ocr", events[2]))["status"], "pending")

    def test_failed_ocr_refresh_does_not_reimport_stale_remote_text(self):
        event = Event(title="Afiche", image_url="https://example.cl/1")
        queue = EnrichmentQueue(self.store)
        queue.ready("ocr", event)
        queue.finish("ocr", queue.start("ocr", event), {"ocr_text": "Texto antiguo"})
        self.now[0] += 31 * 86400
        with patch("eventos.services.content.post_nvidia", side_effect=requests.Timeout("lento")):
            NvidiaContentEnricher(EnrichmentQueue(self.store)).enrich([replace(event, ocr_text="Texto antiguo")])
        fresh = replace(event, ocr_text="Texto antiguo")
        with patch("eventos.services.content.post_nvidia", return_value=response('{"ocr_text":"Texto nuevo"}')) as api:
            NvidiaContentEnricher(EnrichmentQueue(self.store)).enrich([fresh])
        self.assertEqual(api.call_count, 1)
        self.assertEqual(fresh.ocr_text, "Texto nuevo")

    def test_failed_images_do_not_starve_unattempted_images(self):
        raw = [Event(title="Falla", image_url="https://example.cl/1"), Event(title="Nueva", image_url="https://example.cl/2")]
        with patch.dict(os.environ, {"NVIDIA_OCR_MAX_IMAGES": "1"}):
            with patch("eventos.services.content.post_nvidia", side_effect=requests.Timeout("lento")):
                NvidiaContentEnricher(EnrichmentQueue(self.store)).enrich([replace(e) for e in raw])
            with patch("eventos.services.content.post_nvidia", return_value=response('{"ocr_text":"Texto"}')) as api:
                NvidiaContentEnricher(EnrichmentQueue(self.store)).enrich([replace(e) for e in raw])
            image = api.call_args.args[0]["messages"][0]["content"][1]["image_url"]["url"]
            self.assertEqual(image, raw[1].image_url)

    def test_shared_listing_url_does_not_merge_different_events(self):
        a = Event(title="Música", start_date="2099-01-01", source_url="https://example.cl/agenda")
        b = replace(a, title="Teatro")
        self.assertEqual(len(deduplicate([a, b, replace(a)])), 2)

    def test_shared_image_is_requested_once(self):
        events = [Event(title="Uno", image_url="https://example.cl/1"), Event(title="Dos", image_url="https://example.cl/1")]
        with patch("eventos.services.content.post_nvidia", return_value=response('{"ocr_text":"Texto"}')) as api:
            NvidiaContentEnricher(EnrichmentQueue(self.store)).enrich(events)
        self.assertEqual(api.call_count, 1)
        self.assertEqual(events[1].ocr_text, "Texto")

    def test_changed_facts_invalidate_remote_rewrite_across_restarts(self):
        raw = Event(event_id="stable", title="Obra", start_date="2099-01-01", source_description="Original", image_url="https://example.cl/old")
        queue = EnrichmentQueue(self.store)
        queue.reconcile_input(replace(raw), raw)
        new = replace(raw, start_date="2099-02-01", image_url="https://example.cl/new")
        for _ in range(2):
            from_remote = replace(new, description="Editorial antigua", is_rewritten=True, ocr_text="Afiche antiguo")
            EnrichmentQueue(self.store).reconcile_input(from_remote, new)
            self.assertFalse(from_remote.is_rewritten)
            self.assertEqual(from_remote.description, "Original")
            self.assertEqual(from_remote.ocr_text, "")

    def test_rewrite_batch_checkpoint_skips_completed_inputs_but_not_changes(self):
        raw = Event(title="Obra", source_description="Original", description="Original")
        with patch.dict(os.environ, {"NVIDIA_OCR_ENABLED": "false", "NVIDIA_REWRITE_ENABLED": "true"}), patch("eventos.services.content.post_nvidia", return_value=response('{"events":[{"index":0,"description":"Editorial"}]}')) as api:
            NvidiaContentEnricher(EnrichmentQueue(self.store)).enrich([replace(raw)])
            resumed = replace(raw)
            NvidiaContentEnricher(EnrichmentQueue(self.store)).enrich([resumed])
            self.assertEqual(api.call_count, 1)
            self.assertEqual(resumed.description, "Editorial")
            NvidiaContentEnricher(EnrichmentQueue(self.store)).enrich([replace(raw, source_description="Nueva descripción")])
            self.assertEqual(api.call_count, 2)

    def test_completed_source_resumes_but_failed_source_is_retried(self):
        sources = [SourceConfig.from_dict({"name": name, "url": f"https://{name}.cl"}) for name in ("buena", "mala")]
        http = Mock(request_count=0, cache_hits=0)
        pipeline = EventPipeline(http=http, state=self.store)
        pipeline.cycle = self.store.begin_cycle()
        good = Mock()
        good.collect.return_value = [Event(title="Evento", start_date="2099-01-01")]
        bad = Mock()
        bad.collect.side_effect = ValueError("Fallo")
        with patch("eventos.pipeline.ConnectorFactory.create", side_effect=[good, bad]), patch("eventos.pipeline.EventAvailabilityValidator.apply"):
            _, summaries = pipeline._collect_group(list(enumerate(sources)))
        self.assertEqual([x.status for x in summaries], ["succeeded", "failed"])
        with patch("eventos.pipeline.ConnectorFactory.create", return_value=good) as factory, patch("eventos.pipeline.EventAvailabilityValidator.apply"):
            _, summaries = pipeline._collect_group(list(enumerate(sources)))
        self.assertEqual(factory.call_count, 1)
        self.assertEqual(factory.call_args.args[0].name, "mala")

    def test_successful_empty_source_is_marked_complete_empty(self):
        source = SourceConfig.from_dict({"name": "Sin cartelera", "url": "https://example.cl"})
        http = Mock(request_count=0, cache_hits=0)
        connector = Mock(collection_complete=True)
        connector.collect.return_value = []
        pipeline = EventPipeline(http=http, state=self.store)
        pipeline.cycle = self.store.begin_cycle()
        with patch("eventos.pipeline.ConnectorFactory.create", return_value=connector), patch(
            "eventos.pipeline.EventAvailabilityValidator.apply"
        ):
            _, summaries = pipeline._collect_group([(0, source)])
        self.assertEqual(summaries[0].status, "complete_empty")

    def test_phase_extract_never_constructs_supabase_and_process_loads_snapshot(self):
        source = SourceConfig.from_dict({"name": "Test", "url": "https://example.cl"})
        repo = Mock()
        repo.load.return_value = [source]
        events = [Event(title="Obra", start_date="2099-01-01", source_name="Test")]
        summary = SourceSummary("Test", "example.cl", "succeeded", 1, 1, 1, 0)
        settings = Settings(cultural_filter_enabled=False)
        with patch("eventos.pipeline.load_dotenv"), patch("eventos.pipeline.configure_nvidia_state"), patch("eventos.pipeline.Settings.load", return_value=settings), patch("eventos.pipeline.write_source_summaries"), patch("eventos.pipeline.SupabaseExporter.from_env") as remote:
            pipeline = EventPipeline(repository=repo, state=self.store, exporter=Mock())
            with patch.object(pipeline, "_collect_sources", return_value=(events, [summary])):
                pipeline.run(push_to_supabase=True, phase="extract")
            remote.assert_not_called()
            with patch.object(pipeline, "_collect_sources", side_effect=AssertionError("No reextraer")), patch.dict(os.environ, {"NVIDIA_API_KEY": ""}):
                result = pipeline.run(push_to_supabase=False, phase="process")
            self.assertEqual(len(result), 1)

    def test_pipeline_sends_only_remote_candidates_to_ai(self):
        source = SourceConfig.from_dict({"name": "Test", "url": "https://example.cl"})
        repo = Mock()
        repo.load.return_value = [source]
        known = Event(
            title="Conocido",
            start_date="2099-01-01T18:00:00",
            source_name="Test",
            source_url="https://example.cl/known",
        )
        pending = Event(
            title="Pendiente",
            start_date="2099-01-02T18:00:00",
            source_name="Test",
            source_url="https://example.cl/pending",
        )
        summary = SourceSummary("Test", "example.cl", "succeeded", 2, 1, 1, 0)
        remote = Mock()
        remote.prepare_for_enrichment.side_effect = lambda events: (events, [events[1]])
        content = Mock()
        content.enrich.return_value = {"ocr": 0, "rewritten": 0}
        location = Mock()
        location.finalize.return_value = {}
        pipeline = EventPipeline(
            repository=repo,
            exporter=Mock(),
            supabase_exporter=remote,
            location_normalizer=location,
            content_enricher=content,
            state=self.store,
        )
        with patch("eventos.pipeline.load_dotenv"), patch(
            "eventos.pipeline.configure_nvidia_state"
        ), patch(
            "eventos.pipeline.Settings.load",
            return_value=Settings(cultural_filter_enabled=False),
        ), patch("eventos.pipeline.write_source_summaries"), patch.object(
            pipeline, "_collect_sources", return_value=([known, pending], [summary])
        ):
            pipeline.run(push_to_supabase=True)
        candidates = content.enrich.call_args.args[0]
        self.assertEqual([event.title for event in candidates], ["Pendiente"])


class IncrementalTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.clock = [1000000.0]
        self.store = StateStore(Path(self.directory.name) / "state.sqlite3", clock=lambda: self.clock[0])

    def test_detail_cache_revision_ttl_and_config_invalidation(self):
        source = SourceConfig.from_dict({"name": "Test", "url": "https://example.cl"})
        cache = DetailCache(self.store, source)
        cache.save("url", [Event(title="A")], "v1")
        self.assertIsNone(cache.load("url", "v2"))
        self.assertEqual(cache.load("url", "v1")[0].title, "A")
        self.clock[0] += 7 * 86400
        self.assertIsNone(cache.load("url", "v1"))
        cache.save("url", [], "")
        self.assertEqual(cache.load("url"), [])
        self.clock[0] += 86400
        self.assertIsNone(cache.load("url"))

    def test_ticketplus_discovers_new_and_omits_removed_urls(self):
        source = SourceConfig.from_dict({"name": "Ticket", "url": "https://example.cl", "incremental": True, "link_path_prefixes": ["/events/"], "max_pages": 0, "use_nvidia": False})
        http = Mock(pipeline_state=self.store)
        def detail(title):
            return '<script type="application/ld+json">' + json.dumps({"@type": "Event", "name": title, "startDate": "2099-01-01"}) + '</script>'
        pages = {source.url: '<a href="/events/a">A</a>', "https://example.cl/events/a": detail("A"), "https://example.cl/events/b": detail("B")}
        http.get_html.side_effect = lambda url, **kw: pages[url]
        self.assertEqual(len(GenericConnector(http).collect(source)), 1)
        http.get_html.reset_mock()
        pages[source.url] += '<a href="/events/b">B</a>'
        events = GenericConnector(http).collect(source)
        self.assertEqual([e.title for e in events], ["A", "B"])
        self.assertEqual(http.get_html.call_count, 2)  # cartelera + solo ficha nueva
        pages[source.url] = '<a href="/events/b">B</a>'
        self.assertEqual([e.title for e in GenericConnector(http).collect(source)], ["B"])

    def test_santiago_skips_unchanged_detail_and_reloads_modified(self):
        source = SourceConfig.from_dict({"name": "Santiago", "url": "https://example.cl", "incremental": True})
        http = Mock(pipeline_state=self.store)
        row = {"id": 1, "link": "https://example.cl/a", "title": {"rendered": "A"}, "modified_gmt": "2026-01-01T00:00:00"}
        http.get_response.return_value = Mock(headers={"X-WP-TotalPages": "1"})
        http.get_response.return_value.json.return_value = [row]
        http.get_html.return_value = '<div itemtype="https://schema.org/Event"><meta itemprop="startDate" content="2099-01-01"></div>'
        EventOnWordPressConnector(http).collect(source)
        EventOnWordPressConnector(http).collect(source)
        self.assertEqual(http.get_html.call_count, 1)
        row["modified_gmt"] = "2026-01-02T00:00:00"
        EventOnWordPressConnector(http).collect(source)
        self.assertEqual(http.get_html.call_count, 2)

    def test_interrupted_santiago_keeps_details_already_downloaded(self):
        source = SourceConfig.from_dict({"name": "Santiago", "url": "https://example.cl", "incremental": True})
        http = Mock(pipeline_state=self.store)
        http.get_response.return_value = Mock(headers={"X-WP-TotalPages": "1"})
        http.get_response.return_value.json.return_value = [
            {"link": f"https://example.cl/{i}", "title": {"rendered": str(i)}, "modified_gmt": "v1"}
            for i in range(2)
        ]
        html = '<div itemtype="https://schema.org/Event"><meta itemprop="startDate" content="2099-01-01"></div>'
        http.get_html.side_effect = [html, KeyboardInterrupt()]
        with self.assertRaises(KeyboardInterrupt):
            EventOnWordPressConnector(http).collect(source)
        http.get_html.reset_mock()
        http.get_html.side_effect = [html]
        self.assertEqual(len(EventOnWordPressConnector(http).collect(source)), 2)
        self.assertEqual(http.get_html.call_count, 1)

    def test_missing_structure_is_partial_and_never_cached_as_empty_success(self):
        source = SourceConfig.from_dict({"name": "Ticket", "url": "https://example.cl", "incremental": True, "link_path_prefixes": ["/events/"], "use_nvidia": False})
        http = Mock(pipeline_state=self.store)
        http.get_html.side_effect = ['<a href="/events/a">A</a>', '<h1>Reintente</h1>']
        connector = GenericConnector(http)
        self.assertEqual(connector.collect(source), [])
        self.assertFalse(connector.collection_complete)
        self.assertIsNone(DetailCache(self.store, source).load("https://example.cl/events/a"))

    def test_empty_detail_preserves_other_events_and_marks_partial(self):
        source = SourceConfig.from_dict({"name": "Agenda", "url": "https://example.cl", "use_nvidia": False})
        http = Mock(pipeline_state=self.store)
        http.get_html.side_effect = [
            '<a href="/event/a">A</a><a href="/event/b">B</a>', None,
            '<script type="application/ld+json">{"@type":"Event","name":"B","startDate":"2099-01-01"}</script>',
        ]
        connector = GenericConnector(http)
        events = connector.collect(source)
        self.assertEqual([event.title for event in events], ["B"])
        self.assertFalse(connector.collection_complete)


class NvidiaPauseTests(unittest.TestCase):
    def limiter(self):
        now = [0.0]
        def sleep(seconds):
            now[0] += seconds
        return NvidiaRateLimiter(clock=lambda: now[0], sleeper=sleep)

    def test_defer_applies_even_before_first_request(self):
        limiter = self.limiter()
        limiter.defer(60)
        limiter.acquire()
        self.assertEqual(limiter.waited_seconds, 60)

    def test_retry_after_not_truncated_and_persisted_after_last_retry(self):
        limiter = self.limiter()
        result = Mock(status_code=429, headers={"Retry-After": "180"})
        with patch("eventos.services.nvidia.nvidia_rate_limiter", return_value=limiter), patch("eventos.services.nvidia.requests.post", return_value=result) as api:
            self.assertIs(post_nvidia({}, "test", retries=0, max_wait=1), result)
            self.assertEqual(limiter.remaining_wait(), 180)
            with self.assertRaises(requests.Timeout):
                post_nvidia({}, "test", max_elapsed=1)
            self.assertEqual(api.call_count, 1)

    def test_pause_only_after_three_consecutive_timeouts(self):
        limiter = self.limiter()
        with patch.dict(os.environ, {"NVIDIA_TIMEOUT_THRESHOLD": "3", "NVIDIA_TIMEOUT_COOLDOWN_SECONDS": "60"}), patch("eventos.services.nvidia.nvidia_rate_limiter", return_value=limiter), patch("eventos.services.nvidia.requests.post", side_effect=requests.Timeout("lento")):
            for _ in range(2):
                with self.assertRaises(requests.Timeout):
                    post_nvidia({}, "test", retries=0)
                self.assertEqual(limiter.remaining_wait(), 0)
            with self.assertRaises(requests.Timeout):
                post_nvidia({}, "test", retries=0)
            self.assertEqual(limiter.remaining_wait(), 60)

    def test_retry_after_http_date(self):
        self.assertEqual(retry_after_seconds("120"), 120)
        self.assertEqual(retry_after_seconds("Wed, 21 Oct 2015 07:28:00 GMT"), 0)
        self.assertIsNone(retry_after_seconds("incorrecto"))
