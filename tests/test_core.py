import csv
import json
import tempfile
import unittest
from pathlib import Path

from eventos.config import Settings, SourceConfig, SourceRepository
from eventos.connectors import ConnectorFactory
from eventos.connectors.wordpress import get_path
from eventos.consolidation import deduplicate, deduplicate_resolved, filter_regions
from eventos.models import Event
from eventos.pipeline import EventPipeline
from eventos.text import normal_date, text_from_html
from eventos.exporters import write_dataset


class ConfigurationTests(unittest.TestCase):
    def test_all_chilean_regions_are_enabled(self):
        settings = Settings.load()
        self.assertEqual(len(settings.target_regions), 16)
        self.assertIn("Región de Arica-Parinacota", settings.target_regions)
        self.assertIn("Región de Magallanes y la Antártica Chilena", settings.target_regions)

    def test_at_least_ten_sources_are_enabled(self):
        sources = SourceRepository().load()
        enabled = [source for source in sources if source.enabled]
        self.assertGreaterEqual(len(enabled), 10)

    def test_every_enabled_source_has_a_registered_connector(self):
        available = set(ConnectorFactory.available())
        for source in SourceRepository().load():
            if source.enabled:
                self.assertIn(source.connector, available, source.name)

    def test_wine_and_expo_sources_are_enabled(self):
        enabled = {source.name for source in SourceRepository().load() if source.enabled}
        self.assertTrue(
            {
                "Enoturismo Chile · Agenda",
                "FISA · Ferias y Exposiciones",
                "Espacio Riesco · Calendario",
            }.issubset(enabled)
        )

    def test_ceina_source_is_enabled(self):
        enabled = {source.name for source in SourceRepository().load() if source.enabled}
        self.assertIn("Centro Cultural CEINA", enabled)

    def test_teatro_biobio_replaces_rancagua_as_an_active_source(self):
        enabled = {source.name for source in SourceRepository().load() if source.enabled}
        self.assertIn("Teatro Biobío", enabled)
        self.assertNotIn("Rancagua Cultura", enabled)
        self.assertIn("Rancagua Cultura", Settings.load().retired_source_names)

    def test_source_keeps_connector_specific_options(self):
        source = SourceConfig.from_dict(
            {"name": "Ejemplo", "url": "https://example.cl", "field_map": {"title": "name"}}
        )
        self.assertEqual(source.get("field_map"), {"title": "name"})

    def test_seasonal_source_only_runs_in_configured_month(self):
        source = SourceConfig.from_dict(
            {
                "name": "Fondas de septiembre",
                "url": "https://example.cl",
                "active_months": [9],
            }
        )
        self.assertTrue(EventPipeline.is_source_active_in_month(source, 9))
        self.assertFalse(EventPipeline.is_source_active_in_month(source, 8))


class WordPressMappingTests(unittest.TestCase):
    def test_get_path_handles_nested_lists_and_colon_keys(self):
        payload = {"_embedded": {"wp:term": [[], [{"name": "Rancagua"}]]}}
        self.assertEqual(get_path(payload, "_embedded.wp:term.1.0.name"), "Rancagua")
        self.assertEqual(get_path(payload, "missing.path"), "")

    def test_spanish_month_is_normalized(self):
        self.assertEqual(normal_date("Septiembre 06, 2099 13:00")[:16], "2099-09-06T13:00")

    def test_year_first_eventon_date_does_not_swap_month_and_day(self):
        self.assertEqual(normal_date("2026-9-1T12:00-3:00")[:16], "2026-09-01T12:00")

    def test_compact_wordpress_acf_date_is_not_swapped(self):
        self.assertEqual(normal_date("20260806")[:10], "2026-08-06")

    def test_visual_composer_markup_is_removed_but_visible_text_is_kept(self):
        value = (
            '[vc_section css=".vc_custom_1{padding-top:3rem !important;}"]'
            '[vc_row][vc_column_text]Descripción visible del evento.'
            '[/vc_column_text][/vc_row][/vc_section]'
        )
        self.assertEqual(text_from_html(value), "Descripción visible del evento.")

    def test_visual_composer_only_description_becomes_empty(self):
        value = '[vc_section el_class="container" css=".vc_custom_1{color:#fff;}"][/vc_section]'
        self.assertEqual(text_from_html(value), "")


class ConsolidationTests(unittest.TestCase):
    def test_deduplication_prefers_record_with_official_url(self):
        first = Event(title="Concierto", start_date="2099-01-01", commune="Talca")
        second = Event(
            title="Concierto",
            start_date="2099-01-01",
            commune="Talca",
            official_url="https://example.cl/evento",
        )
        result = deduplicate([first, second])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].official_url, "https://example.cl/evento")

    def test_same_title_and_time_without_location_remain_distinct_by_origin(self):
        first = Event(
            title="Taller",
            start_date="2099-01-01T10:00:00-03:00",
            source_name="Fuente A",
            source_url="https://a.example/evento/1",
        )
        second = Event(
            title="Taller",
            start_date="2099-01-01T10:00:00-03:00",
            source_name="Fuente B",
            source_url="https://b.example/evento/2",
        )
        self.assertEqual(len(deduplicate([first, second])), 2)

    def test_region_filter_normalizes_accents_and_case(self):
        settings = Settings(target_regions=("Región del Maule",))
        event = Event(title="Feria", region="REGION DEL MAULE")
        self.assertEqual(filter_regions([event], settings), [event])

    def test_resolved_remote_ids_are_collapsed_without_losing_precise_time(self):
        date_only = Event(
            title="Muestra",
            start_date="2099-01-01T00:00:00",
            event_id="remote-key",
        )
        timed = Event(
            title="Muestra",
            start_date="2099-01-01T18:30:00",
            event_id="remote-key",
        )
        self.assertEqual(deduplicate_resolved([date_only, timed]), [timed])


class ExportTests(unittest.TestCase):
    def test_categories_are_json_array_in_json_and_csv(self):
        event = Event(title="Festival", categories=["Música", "Teatro"])
        with tempfile.TemporaryDirectory() as directory:
            json_path = Path(directory) / "events.json"
            csv_path = Path(directory) / "events.csv"
            write_dataset([event], json_path, csv_path)
            json_row = json.loads(json_path.read_text(encoding="utf-8"))[0]
            with csv_path.open(encoding="utf-8-sig", newline="") as file:
                csv_row = next(csv.DictReader(file))
        self.assertEqual(json_row["categories"], ["Música", "Teatro"])
        self.assertEqual(json.loads(csv_row["categories"]), ["Música", "Teatro"])


if __name__ == "__main__":
    unittest.main()
