import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

from eventos.analytics import SourceSummary, write_source_summaries
from eventos.cache import ConditionalHttpCache
from eventos.config import SourceConfig
from eventos.connectors.maipu import event_from_maipu_record
from eventos.exporters import load_json_dataset
from eventos.models import Event
from eventos.pipeline import EventPipeline


class ConditionalCacheTests(unittest.TestCase):
    def test_stores_validators_and_restores_body_after_304(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = ConditionalHttpCache(Path(directory))
            original = requests.Response()
            original.status_code = 200
            original.url = "https://example.cl/events"
            original.encoding = "utf-8"
            original.headers.update({"ETag": '"v1"', "Content-Type": "application/json"})
            original._content = b'{"events": [1]}'
            cache.store(original.url, None, original)

            entry = cache.load(original.url, None)
            self.assertEqual(cache.conditional_headers(entry), {"If-None-Match": '"v1"'})
            not_modified = requests.Response()
            not_modified.status_code = 304
            not_modified.url = original.url
            restored = cache.restore(entry, not_modified)

            self.assertEqual(restored.status_code, 200)
            self.assertEqual(restored.json(), {"events": [1]})


class MaipuRecordTests(unittest.TestCase):
    def test_builds_event_from_backend_record_without_opening_detail(self):
        source = SourceConfig.from_dict(
            {
                "name": "Maipú en Común",
                "url": "https://maipuencomun.cl/",
                "connector": "maipu_browser",
                "region": "Región Metropolitana de Santiago",
                "commune": "Maipú",
                "city": "Santiago",
                "default_categories": ["Cultura"],
            }
        )
        event = event_from_maipu_record(
            {
                "id": 77,
                "type": "activity",
                "name": "Festival comunitario",
                "start_date": "2099-09-10",
                "end_date": "2099-09-10",
                "start_time": "18:00",
                "end_time": "20:00",
                "description": "<p>Actividad gratuita. Lugar: Plaza de Maipú</p>",
                "place": {"name": "DIRECCIÓN EN LA DESCRIPCIÓN", "address": ""},
                "category": {"display_name": "Cultura"},
                "thumbnail_image": "https://example.cl/event.jpg",
            },
            source,
        )

        self.assertIsNotNone(event)
        self.assertEqual(event.venue, "Plaza de Maipú")
        self.assertEqual(event.categories, ["Cultura"])
        self.assertEqual(event.is_free, "true")
        self.assertEqual(event.source_url, "https://maipuencomun.cl/actividad/77")
        self.assertEqual(event.extraction_method, "maipu-browser-api")


class OperationalFilesTests(unittest.TestCase):
    def test_source_metrics_and_json_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_source_summaries(
                [SourceSummary("Fuente", "example.cl", "succeeded", 3, 1.25, 2, 1)],
                root,
            )
            metrics = json.loads((root / "source_metrics.json").read_text(encoding="utf-8"))
            self.assertEqual(metrics[0]["events"], 3)
            self.assertEqual(metrics[0]["cache_hits"], 1)

            dataset = root / "events.json"
            dataset.write_text(
                json.dumps([{"title": "Evento", "categories": ["Música"], "unknown": 1}]),
                encoding="utf-8",
            )
            events = load_json_dataset(dataset)
            self.assertEqual(events, [Event(title="Evento", categories=["Música"])])


class ParallelPipelineTests(unittest.TestCase):
    def test_groups_same_domain_and_restores_source_order(self):
        sources = [
            SourceConfig.from_dict({"name": "Primera", "url": "https://agenda.cl/a"}),
            SourceConfig.from_dict({"name": "Segunda", "url": "https://otro.cl/b"}),
            SourceConfig.from_dict({"name": "Tercera", "url": "https://www.agenda.cl/c"}),
        ]
        observed_groups = []

        def collect(group):
            observed_groups.append([source.name for _, source in group])
            return (
                {index: [Event(title=source.name)] for index, source in group},
                [],
            )

        pipeline = EventPipeline()
        with patch.dict("os.environ", {"SOURCE_WORKERS": "4"}), patch.object(
            pipeline, "_collect_group", side_effect=collect
        ):
            events, _ = pipeline._collect_sources(sources)

        self.assertIn(["Primera", "Tercera"], observed_groups)
        self.assertIn(["Segunda"], observed_groups)
        self.assertEqual([event.title for event in events], ["Primera", "Segunda", "Tercera"])

    def test_unexpected_failure_in_one_source_does_not_stop_the_next(self):
        class FakeHttp:
            request_count = 0
            cache_hits = 0

        class BrokenConnector:
            def collect(self, source):
                raise RuntimeError("estructura inesperada")

        class WorkingConnector:
            def collect(self, source):
                return [Event(title="Evento válido", start_date="2099-01-01")]

        sources = [
            SourceConfig.from_dict({"name": "Fallida", "url": "https://fallida.cl"}),
            SourceConfig.from_dict({"name": "Activa", "url": "https://activa.cl"}),
        ]
        pipeline = EventPipeline(http=FakeHttp())
        with patch("eventos.pipeline.ConnectorFactory.create") as create:
            create.side_effect = [BrokenConnector(), WorkingConnector()]
            results, summaries = pipeline._collect_group(list(enumerate(sources)))
        self.assertEqual(results[0], [])
        self.assertEqual(results[1][0].title, "Evento válido")
        self.assertEqual([item.status for item in summaries], ["failed", "succeeded"])


if __name__ == "__main__":
    unittest.main()
