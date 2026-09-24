import json
import tempfile
import unittest
from pathlib import Path

from eventos.source_validation.exporter import SourceValidationExporter
from eventos.source_validation.models import FetchObservation, SourceCandidate
from eventos.source_validation.pipeline import SourceValidationPipeline
from eventos.source_validation.rules import SourceRuleEngine


class FakeFetcher:
    def __init__(self, observations):
        self.observations = observations

    def fetch(self, source):
        return self.observations[source.id]


class RuleEngineTests(unittest.TestCase):
    def test_approves_accessible_chilean_cultural_calendar(self):
        source = SourceCandidate(
            id="1", name="Teatro Chile", categories="Teatro; Música", url="https://teatro.cl/agenda", eligible=True
        )
        observation = FetchObservation(
            requested_url=source.url, final_url=source.url, status_code=200,
            content_type="text/html", visible_text="Agenda cultural. Eventos de teatro y música. Viernes 12 de septiembre.",
            structured_event_count=2, date_signal_count=1, event_link_count=5, robots_allowed=True,
        )
        result = SourceRuleEngine().evaluate(source, observation)
        self.assertEqual(result.verdict, "aprobada")
        self.assertGreaterEqual(result.score, 65)

    def test_keeps_territorial_reference_outside_digital_denominator(self):
        source = SourceCandidate(id="2", name="Casa de la Cultura", eligible=False)
        result = SourceRuleEngine().evaluate(source, FetchObservation(error="sin_url_eventos"))
        self.assertEqual(result.verdict, "no_aplica")

    def test_rejects_dead_link(self):
        source = SourceCandidate(id="3", name="Agenda antigua", url="https://example.cl/eventos", eligible=True)
        observation = FetchObservation(status_code=404, content_type="text/html", error="http_404", robots_allowed=True)
        result = SourceRuleEngine().evaluate(source, observation)
        self.assertEqual(result.verdict, "rechazada")

    def test_existing_integration_without_visible_evidence_is_sent_to_review(self):
        source = SourceCandidate(
            id="4", name="Portal dinámico", url="https://portal.cl/", eligible=True,
            previous_status="Integrada activa",
        )
        observation = FetchObservation(
            status_code=200, content_type="text/html", visible_text="Inicio",
            robots_allowed=True,
        )
        result = SourceRuleEngine().evaluate(source, observation)
        self.assertEqual(result.verdict, "revision_manual")


class PipelineTests(unittest.TestCase):
    def test_reviews_every_inventory_row_and_exports_subsets(self):
        rows = [
            {"id": "1", "name": "Agenda cultural", "url": "https://agenda.cl/eventos", "eligible": True, "categories": "Cultura; Teatro"},
            {"id": "2", "name": "Espacio sin agenda", "url": "", "eligible": False},
        ]
        observations = {
            "1": FetchObservation(
                final_url="https://agenda.cl/eventos", status_code=200, content_type="text/html",
                visible_text="Agenda de eventos culturales de teatro. Sábado 20 de mayo.",
                structured_event_count=1, date_signal_count=1, event_link_count=3, robots_allowed=True,
            ),
            "2": FetchObservation(error="sin_url_eventos"),
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "sources.json"
            input_path.write_text(json.dumps(rows), encoding="utf-8")
            pipeline = SourceValidationPipeline(
                fetcher=FakeFetcher(observations), exporter=SourceValidationExporter(root / "out"), workers=2
            )
            results, summary = pipeline.run(input_path)
            self.assertEqual(len(results), 2)
            self.assertEqual(summary["total"], 2)
            self.assertEqual(summary["approved"], 1)
            self.assertEqual(summary["not_applicable"], 1)
            self.assertTrue((root / "out" / "results.csv").exists())


if __name__ == "__main__":
    unittest.main()
