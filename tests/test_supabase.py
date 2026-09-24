import os
import unittest
from unittest.mock import patch

import requests

from eventos.models import Event
from eventos.supabase_project import resolve_project_ref
from eventos.services.supabase import (
    SupabaseConfig,
    SupabaseError,
    SupabaseExporter,
    SupabaseRestClient,
    build_supabase_bundle,
    supabase_enabled,
)


class SupabaseBundleTests(unittest.TestCase):
    def sample_event(self) -> Event:
        return Event(
            title="Festival cultural",
            start_date="2099-09-01T18:00:00-03:00",
            end_date="2099-09-01T21:00:00-03:00",
            venue="Teatro Municipal",
            commune="Santiago",
            city="Santiago",
            region="Región Metropolitana de Santiago",
            organizer="Municipalidad de Santiago",
            postal_code="8320000",
            latitude="-33.4372",
            longitude="-70.6506",
            categories=["Música", "Familiar"],
            is_free="true",
            image_url="https://example.cl/evento.jpg",
            source_name="Agenda oficial",
            source_url="https://example.cl/eventos/festival",
            official_url="https://example.cl/eventos/festival",
            extracted_at="2099-08-01T12:00:00+00:00",
            extraction_method="test",
            event_id="evento-estable-1",
            source_description="Texto original de la fuente",
            ocr_text="Texto del afiche",
        )

    def test_maps_full_event_and_many_to_many_categories(self):
        bundle = build_supabase_bundle([self.sample_event()], "2099-08-02T00:00:00+00:00")
        self.assertEqual(len(bundle.events), 1)
        self.assertEqual(len(bundle.categories), 2)
        self.assertEqual(len(bundle.event_categories), 2)
        self.assertEqual(len(bundle.media_assets), 1)
        self.assertTrue(bundle.events[0]["is_free"])
        self.assertEqual(bundle.events[0]["last_seen_at"], "2099-08-02T00:00:00+00:00")
        self.assertEqual(bundle.events[0]["location_precision"], "venue")
        self.assertEqual(bundle.events[0]["location_source"], "source-data")
        self.assertIn("Teatro Municipal", bundle.events[0]["location"])
        self.assertEqual(bundle.events[0]["latitude"], -33.4372)
        self.assertEqual(bundle.events[0]["longitude"], -70.6506)
        self.assertEqual(bundle.organizers[0]["name"], "Municipalidad de Santiago")
        self.assertEqual(bundle.event_provenance[0]["ocr_text"], "Texto del afiche")
        self.assertFalse(bundle.event_provenance[0]["is_rewritten"])

    def test_deterministic_ids_are_stable_across_runs(self):
        first = build_supabase_bundle([self.sample_event()], "2099-08-02T00:00:00+00:00")
        second = build_supabase_bundle([self.sample_event()], "2099-08-03T00:00:00+00:00")
        self.assertEqual(first.events[0]["id"], second.events[0]["id"])
        self.assertEqual(
            {row["id"] for row in first.categories},
            {row["id"] for row in second.categories},
        )

    def test_unparseable_dates_are_not_published(self):
        event = self.sample_event()
        event.start_date = "Fecha por confirmar"
        event.end_date = ""
        event.extracted_at = "dato inválido"
        seen_at = "2099-08-02T00:00:00+00:00"
        self.assertEqual(build_supabase_bundle([event], seen_at).events, [])

    def test_naive_chilean_time_receives_an_explicit_offset(self):
        event = self.sample_event()
        event.start_date = "2099-09-01T18:00:00"
        row = build_supabase_bundle(
            [event], "2099-08-02T00:00:00+00:00"
        ).events[0]
        self.assertIn(row["start_at"][-6:], {"-03:00", "-04:00"})


class FakeSupabaseClient:
    def __init__(self):
        self.calls = []

    def upsert(self, table, rows, on_conflict="id"):
        self.calls.append(("upsert", table, rows, on_conflict))

    def delete_in(self, table, column, values):
        self.calls.append(("delete", table, column, values))

    def delete_expired_events(self, current_time, day_start):
        self.calls.append(("delete_expired", "events", current_time, day_start))

    def stage_catalog(self, run_id, bundle):
        self.calls.append(("stage", "catalog_staging", run_id, bundle))

    def publish_staged_catalog(self, run_id, **kwargs):
        self.calls.append(("publish", "publish_staged_catalog", run_id, kwargs))
        return {"removed_stale": 2, "removed_expired": 3}


class SupabaseExporterTests(unittest.TestCase):
    def test_records_run_and_publishes_the_staged_catalog_atomically(self):
        client = FakeSupabaseClient()
        event = SupabaseBundleTests().sample_event()
        SupabaseExporter(client).export([event])
        tables = [call[1] for call in client.calls if call[0] == "upsert"]
        self.assertEqual(tables[0], "scrape_runs")
        self.assertTrue(any(call[0] == "stage" for call in client.calls))
        self.assertTrue(any(call[0] == "publish" for call in client.calls))
        self.assertEqual(tables[-1], "scrape_runs")
        self.assertEqual(client.calls[-1][2][0]["status"], "succeeded")
        self.assertFalse(any(call[0] == "delete_expired" for call in client.calls))

    def test_bundle_never_uploads_an_expired_event(self):
        event = SupabaseBundleTests().sample_event()
        event.start_date = "2000-01-01"
        event.end_date = "2000-01-02"
        self.assertEqual(build_supabase_bundle([event]).events, [])

    def test_existing_and_blocked_events_are_removed_before_ai(self):
        class PreparationClient:
            def blocked_organizer_names(self):
                return {"organizador bloqueado"}

            def match_existing_events(self, events):
                return {
                    events[0].event_id: {
                        "stored_external_key": "clave-remota",
                        "stored_description": "Descripción editorial guardada",
                        "stored_source_description": "Texto original de la fuente",
                        "stored_ocr_text": "OCR guardado",
                        "stored_is_suppressed": False,
                        "stored_has_provenance": True,
                        "stored_is_rewritten": True,
                    },
                    events[1].event_id: {
                        "stored_external_key": events[1].event_id,
                        "stored_description": "",
                        "stored_source_description": "",
                        "stored_ocr_text": "",
                        "stored_is_suppressed": True,
                        "stored_has_provenance": True,
                    }
                }

        known = SupabaseBundleTests().sample_event()
        blocked = SupabaseBundleTests().sample_event()
        blocked.title = "Evento bloqueado"
        blocked.event_id = "bloqueado"
        blocked.organizer = "Organizador Bloqueado"
        suppressed = SupabaseBundleTests().sample_event()
        suppressed.title = "Evento excluido individualmente"
        suppressed.event_id = "suprimido"
        allowed, new_events = SupabaseExporter(PreparationClient()).prepare_for_enrichment(
            [known, blocked, suppressed]
        )
        self.assertEqual(allowed, [known])
        self.assertEqual(new_events, [])
        self.assertEqual(known.event_id, "clave-remota")
        self.assertEqual(known.description, "Descripción editorial guardada")
        self.assertEqual(known.ocr_text, "OCR guardado")

    def test_legacy_existing_event_is_enriched_once(self):
        class LegacyClient:
            def blocked_organizer_names(self):
                return set()

            def match_existing_events(self, events):
                return {
                    events[0].event_id: {
                        "stored_external_key": events[0].event_id,
                        "stored_description": events[0].description,
                        "stored_source_description": "",
                        "stored_ocr_text": "",
                        "stored_is_suppressed": False,
                        "stored_has_provenance": False,
                    }
                }

        event = SupabaseBundleTests().sample_event()
        allowed, candidates = SupabaseExporter(LegacyClient()).prepare_for_enrichment([event])
        self.assertEqual(allowed, [event])
        self.assertEqual(candidates, [event])

    def test_existing_failed_rewrite_is_retried_and_source_changes_are_updated(self):
        class PendingClient:
            def blocked_organizer_names(self):
                return set()

            def match_existing_events(self, events):
                return {
                    events[0].event_id: {
                        "stored_external_key": events[0].event_id,
                        "stored_description": "Texto anterior sin parafrasear",
                        "stored_source_description": "Texto anterior sin parafrasear",
                        "stored_ocr_text": "",
                        "stored_is_suppressed": False,
                        "stored_has_provenance": True,
                        "stored_is_rewritten": False,
                    }
                }

        event = SupabaseBundleTests().sample_event()
        event.description = "Texto nuevo publicado por la fuente"
        event.source_description = ""
        allowed, candidates = SupabaseExporter(PendingClient()).prepare_for_enrichment([event])
        self.assertEqual(candidates, [event])
        self.assertEqual(event.description, "Texto nuevo publicado por la fuente")
        self.assertEqual(event.source_description, "Texto nuevo publicado por la fuente")
        self.assertFalse(event.is_rewritten)

    def test_reconciliation_deletes_only_older_unsuppressed_rows(self):
        class Session:
            def __init__(self):
                self.headers = {}
                self.calls = []

            def get(self, url, **kwargs):
                self.calls.append(("get", url, kwargs["params"]))
                response = requests.Response()
                response.status_code = 200
                response._content = b'[{"id":"stale-id"}]'
                return response

            def delete(self, url, **kwargs):
                self.calls.append(("delete", url, kwargs["params"]))
                response = requests.Response()
                response.status_code = 204
                return response

        session = Session()
        client = SupabaseRestClient(
            SupabaseConfig("https://example.supabase.co", "sb_secret_example"),
            session,
        )
        removed = client.delete_stale_source_events("source-id", "2099-01-02T00:00:00Z")
        self.assertEqual(removed, 1)
        self.assertEqual(session.calls[0][2]["source_id"], "eq.source-id")
        self.assertEqual(session.calls[0][2]["last_seen_at"], "lt.2099-01-02T00:00:00Z")
        self.assertEqual(session.calls[0][2]["is_suppressed"], "eq.false")
        self.assertEqual(session.calls[1][2], {"id": "in.(stale-id)"})

    def test_staging_chunks_every_entity_including_empty_relationships(self):
        client = SupabaseRestClient(
            SupabaseConfig("https://example.supabase.co", "sb_secret_example", batch_size=1)
        )
        bundle = build_supabase_bundle(
            [SupabaseBundleTests().sample_event()],
            "2099-08-02T00:00:00+00:00",
        )
        bundle.media_assets.clear()
        with patch.object(client, "delete_where") as delete, patch.object(
            client, "upsert"
        ) as upsert:
            client.stage_catalog("00000000-0000-0000-0000-000000000001", bundle)
        delete.assert_called_once()
        staged = [call.args[1][0] for call in upsert.call_args_list]
        self.assertEqual(
            {row["entity"] for row in staged},
            {
                "sources", "organizers", "communes", "categories", "events",
                "event_provenance", "event_categories", "media_assets",
            },
        )
        empty_media = [row for row in staged if row["entity"] == "media_assets"]
        self.assertEqual(empty_media[0]["payload"], [])

    def test_atomic_publish_calls_the_single_database_rpc(self):
        client = SupabaseRestClient(
            SupabaseConfig("https://example.supabase.co", "sb_secret_example")
        )
        with patch.object(
            client,
            "rpc",
            return_value=[{"removed_stale": 4, "removed_expired": 2}],
        ) as rpc:
            result = client.publish_staged_catalog(
                "00000000-0000-0000-0000-000000000001",
                reconcile_source_ids=["00000000-0000-0000-0000-000000000002"],
                seen_at="2099-01-01T00:00:00+00:00",
                delete_expired=True,
                current_time="2099-01-01T12:00:00-03:00",
                day_start="2099-01-01T00:00:00-03:00",
            )
        self.assertEqual(result, {"removed_stale": 4, "removed_expired": 2})
        self.assertEqual(rpc.call_args.args[0], "publish_staged_catalog")


class SupabaseConfigurationTests(unittest.TestCase):
    def test_select_retries_a_transient_timeout(self):
        class Session:
            def __init__(self):
                self.headers = {}
                self.calls = 0

            def get(self, url, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    raise requests.ReadTimeout("temporary")
                response = requests.Response()
                response.status_code = 200
                response._content = b'[{"name":"Organizador"}]'
                return response

        session = Session()
        client = SupabaseRestClient(
            SupabaseConfig(
                "https://example.supabase.co",
                "sb_secret_example",
                max_retries=1,
            ),
            session,
        )
        with patch("eventos.services.supabase.time.sleep") as sleep:
            rows = client.select("organizers", {"select": "name"})
        self.assertEqual(rows, [{"name": "Organizador"}])
        self.assertEqual(session.calls, 2)
        sleep.assert_called_once_with(1.0)

    def test_publish_rpc_does_not_retry_after_a_timeout(self):
        client = SupabaseRestClient(
            SupabaseConfig("https://example.supabase.co", "sb_secret_example")
        )
        with patch.object(client, "rpc", return_value=[]) as rpc:
            client.publish_staged_catalog(
                "00000000-0000-0000-0000-000000000001",
                reconcile_source_ids=[],
                seen_at="2099-01-01T00:00:00+00:00",
                delete_expired=True,
                current_time="2099-01-01T12:00:00-03:00",
                day_start="2099-01-01T00:00:00-03:00",
            )
        self.assertFalse(rpc.call_args.kwargs["retryable"])

    def test_project_ref_is_extracted_from_url_or_markdown_link(self):
        expected = "ngwkehuewmjykiiujqic"
        self.assertEqual(resolve_project_ref(expected), expected)
        self.assertEqual(
            resolve_project_ref(f"https://{expected}.supabase.co"),
            expected,
        )
        self.assertEqual(
            resolve_project_ref(
                f"[https://{expected}.supabase.co](https://{expected}.supabase.co)"
            ),
            expected,
        )

    def test_enable_alias_is_accepted(self):
        with patch.dict(os.environ, {"SUPABASE_ENABLED": "enable"}):
            self.assertTrue(supabase_enabled())

    def test_missing_environment_is_rejected(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(SupabaseError):
                SupabaseConfig.from_env()

    def test_new_secret_key_is_not_sent_as_bearer_jwt(self):
        config = SupabaseConfig(
            url="https://example.supabase.co",
            secret_key="sb_secret_example",
        )
        client = SupabaseRestClient(config)
        self.assertEqual(client.session.headers["apikey"], "sb_secret_example")
        self.assertNotIn("Authorization", client.session.headers)

    def test_legacy_service_role_key_keeps_bearer_header(self):
        config = SupabaseConfig(
            url="https://example.supabase.co",
            secret_key="legacy-jwt-service-role-key",
        )
        client = SupabaseRestClient(config)
        self.assertEqual(
            client.session.headers["Authorization"],
            "Bearer legacy-jwt-service-role-key",
        )

    def test_expired_cleanup_uses_current_time_and_day_for_missing_end(self):
        class Session:
            def __init__(self):
                self.headers = {}
                self.calls = []

            def delete(self, url, **kwargs):
                self.calls.append((url, kwargs["params"]))
                response = requests.Response()
                response.status_code = 204
                return response

        session = Session()
        client = SupabaseRestClient(
            SupabaseConfig("https://example.supabase.co", "sb_secret_example"),
            session,
        )
        client.delete_expired_events(
            "2099-01-02T12:00:00-03:00",
            "2099-01-02T00:00:00-03:00",
        )
        self.assertEqual(
            session.calls[0][1],
            {"end_at": "lt.2099-01-02T12:00:00-03:00"},
        )
        self.assertEqual(
            session.calls[1][1],
            {"end_at": "is.null", "start_at": "lt.2099-01-02T00:00:00-03:00"},
        )


if __name__ == "__main__":
    unittest.main()
