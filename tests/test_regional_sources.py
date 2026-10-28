"""Muestras mínimas de las estructuras públicas comprobadas el 27-10-2026."""
import json
import unittest
from datetime import datetime
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from eventos.config import SourceConfig, SourceRepository
from eventos.connectors import ConnectorFactory
from eventos.connectors.html_cards import HtmlCardsConnector
from eventos.connectors.html_support import selector_value
from eventos.locations import LocationNormalizer
from eventos.sanitization import EventSanitizer


NOW = datetime(2026, 10, 27, 12, tzinfo=ZoneInfo("America/Santiago"))


class NewRegionalSourcesTests(unittest.TestCase):
    def setUp(self):
        self.sources = {source.name: source for source in SourceRepository().load()}
        for target in ("eventos.text.chile_now", "eventos.connectors.html_cards.chile_now"):
            current = patch(target, return_value=NOW)
            current.start()
            self.addCleanup(current.stop)

    def collect(self, name, pages):
        source = self.sources[name]
        http = Mock()
        http.get_html.side_effect = lambda url, **kwargs: pages[url]
        connector = ConnectorFactory(http).create(source)
        events = connector.collect(source)
        EventSanitizer().apply(events, source)
        normalizer = LocationNormalizer()
        normalizer.apply_source_defaults(events, [source])
        normalizer.finalize(events)
        return connector, events, http

    def test_five_new_sources_are_enabled_without_llm_extraction(self):
        names = {"Teatro del Lago", "Teatro Municipal de Chillán", "Teatro Regional Cervantes",
                 "Corporación Cultural de Puerto Montt", "Teatro Oriente"}
        self.assertGreaterEqual(len(self.sources), 27)
        for name in names:
            source = self.sources[name]
            self.assertTrue(source.enabled)
            self.assertFalse(source["use_nvidia"])
            self.assertTrue(source.commune and source.region)

    def test_chillan_reads_explicit_year_in_detail_not_abbreviated_listing(self):
        source = self.sources["Teatro Municipal de Chillán"]
        detail = "https://teatrochillan.cl/evento/manuel/"
        listing = f'''<div class="events-group-proximos"><article class="event-card">
        <h3 class="event-title">Manuel García</h3><div class="event-date-badge">10 ABR</div>
        <span class="event-category-badge">CONCIERTO</span><a class="event-link" href="{detail}">Ficha</a>
        </article></div><div class="events-group-pasados"><article class="event-card">Archivo</article></div>'''
        page = '''<div class="info-content"><span class="info-label">FECHA</span>
        <span class="info-value">10 de abril de 2027</span></div>
        <div class="info-content"><span class="info-label">HORA</span><span class="info-value">19:30 hrs</span></div>
        <div class="evento-ficha-accordions"><div class="entry-content"><p>Concierto acústico.</p></div></div>'''
        connector, events, http = self.collect(source.name, {source.url: listing, detail: page})
        self.assertTrue(connector.collection_complete)
        self.assertEqual(http.get_html.call_count, 2)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].start_date, "2027-04-10T19:30:00-04:00")
        self.assertEqual(events[0].description, "Concierto acústico.")
        self.assertEqual(events[0].source_url, detail)
        self.assertIn("18 de Septiembre 590", events[0].location)

    def test_cervantes_keeps_full_numeric_date_and_hour_without_minutes(self):
        source = self.sources["Teatro Regional Cervantes"]
        page = '''<div class="e-loop-item type-calendario"><h2><a href="/calendario/obra/">Pareidolia</a></h2>
        <div class="elementor-widget-icon-list"><ul><li class="elementor-icon-list-item">
        <span class="elementor-icon-list-text">21/11/2026</span></li><li class="elementor-icon-list-item">
        <span class="elementor-icon-list-text">19 hrs.</span></li></ul></div>
        <a class="elementor-post-info__terms-list-item">Teatro</a><img src="/afiche.jpg"></div>'''
        _, events, _ = self.collect(source.name, {source.url: page})
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].start_date, "2026-11-21T19:00:00-03:00")
        self.assertEqual(events[0].categories, ["Teatro"])
        self.assertEqual(events[0].commune, "Valdivia")

    def test_puerto_montt_rolling_calendar_crosses_new_year_and_keeps_organizer(self):
        source = self.sources["Corporación Cultural de Puerto Montt"]
        detail = "https://culturapuertomontt.cl/eventos/concierto/"
        listing = f'''<div class="e-loop-item type-evento">
        <div class="ccpm-tarjeta__titulo"><h3><a href="{detail}">Concierto de verano</a></h3></div>
        <div class="ccpm-tarjeta__fecha">02 ENE</div><div class="ccpm-tarjeta__area">Música</div>
        <div class="ccpm-tarjeta__meta"><span>Sáb 02 Ene · 19:30 hrs</span><span>Sala Diego Rivera</span></div>
        <span class="ccpm-tarjeta__valor">Entrada liberada</span></div>'''
        page = '''<meta property="og:image" content="https://culturapuertomontt.cl/afiche.jpg">
        <div class="ccpm-prosa"><p>Concierto gratuito.</p><p>Organiza: Agrupación Cultural Local</p></div>'''
        december = datetime(2026, 12, 30, 12, tzinfo=NOW.tzinfo)
        with patch("eventos.text.chile_now", return_value=december), patch(
            "eventos.connectors.html_cards.chile_now", return_value=december
        ):
            _, events, _ = self.collect(source.name, {source.url: listing, detail: page})
        self.assertEqual(events[0].start_date, "2027-01-02T19:30:00-03:00")
        self.assertEqual(events[0].organizer, "Agrupación Cultural Local")
        self.assertEqual(events[0].address, "Quillota 116, Puerto Montt")
        self.assertEqual(events[0].is_free, "true")

    def test_oriente_uses_iso_dates_from_jsonld_and_excludes_cancelled_cards(self):
        source = self.sources["Teatro Oriente"]
        detail = "https://teatrooriente.cl/evento/temporada/"
        listing = f'''<div class="upcoming-event-wrap"><div class="type-tribe_events">
        <h2 class="tribe-events-list-event-title"><a href="{detail}">Temporada lírica</a></h2></div>
        <div class="type-tribe_events"><h2 class="tribe-events-list-event-title"><a href="/cancelada/">Obra CANCELADO</a></h2></div>
        <div class="type-tribe_events"><h2 class="tribe-events-list-event-title"><a href="/aviso/">Teatro declarado Monumento nacional</a></h2></div></div>'''
        node = {"@type": "Event", "name": "Temporada lírica", "url": detail,
                "startDate": "2026-12-02T19:00:00-03:00", "endDate": "2027-07-06T21:00:00-04:00"}
        page = '<script type="application/ld+json">' + json.dumps([node]) + '</script>'
        _, events, http = self.collect(source.name, {source.url: listing, detail: page})
        self.assertEqual(http.get_html.call_count, 2)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].start_date, node["startDate"])
        self.assertEqual(events[0].end_date, node["endDate"])

    def test_teatro_del_lago_reads_all_api_pages_and_sanitizes_content(self):
        source = self.sources["Teatro del Lago"]
        http = Mock()
        row = {"title": "Música &amp; danza", "start_date": "2026-11-07 19:00:00",
               "end_date": "2026-11-07 21:00:00", "url": "https://teatrodellago.cl/evento/uno/",
               "description": "<style>.oculto{color:red}</style><p>Concierto.</p>",
               "venue": {"venue": "Espacio Tronador"}, "categories": [{"name": "Danza"}]}
        http.get_json.side_effect = [{"events": [row], "total_pages": 2},
                                     {"events": [dict(row, title="Exposición", url="https://teatrodellago.cl/evento/dos/")], "total_pages": 2}]
        events = ConnectorFactory(http).create(source).collect(source)
        EventSanitizer().apply(events, source)
        self.assertEqual(len(events), 2)
        self.assertEqual(http.get_json.call_count, 2)
        self.assertEqual(http.get_json.call_args.kwargs["params"]["page"], 2)
        self.assertNotIn("color:red", events[0].description)


class HtmlCompatibilityTests(unittest.TestCase):
    def source(self, **options):
        return SourceConfig.from_dict({"name": "Prueba", "url": "https://example.cl/agenda/",
            "connector": "html_cards", "card_selector": "article", "require_start_date": True,
            "selectors": {"title": "h2", "start_date": "time", "source_url": {"selector": "a", "attribute": "href"}},
            **options})

    def test_selector_pattern_and_clock_format_validate_values(self):
        card = BeautifulSoup('<span>19 HRS.</span>', "html.parser")
        self.assertEqual(selector_value(card, {"selector": "span", "format": "time"}), "19:00")
        card.span.string = "Vie 30 Oct · 19:30 hrs"
        self.assertEqual(selector_value(card, {"selector": "span", "pattern": r"(\d{1,2}:\d{2})", "format": "time"}), "19:30")
        card.span.string = "25:90 hrs"
        self.assertEqual(selector_value(card, {"selector": "span", "format": "time"}), "")

    def test_end_clock_does_not_duplicate_start_clock_in_date(self):
        source = self.source(selectors={"title": "h2", "start_date": "time", "start_time": ".start", "end_time": ".end"})
        http = Mock()
        http.get_html.return_value = '<article><h2>Obra</h2><time>2099-11-01</time><span class="start">19:00</span><span class="end">21:00</span></article>'
        event = HtmlCardsConnector(http).collect(source)[0]
        self.assertEqual(event.start_date, "2099-11-01T19:00:00")
        self.assertEqual(event.end_date, "2099-11-01T21:00:00")

    def test_pagination_deduplicates_and_repeated_page_is_partial(self):
        source = self.source(next_page_selector="a.next")
        card = '<article><h2>Obra</h2><time>2099-11-01</time><a href="/obra/">Ficha</a></article>'
        http = Mock()
        http.get_html.side_effect = [card + '<a class="next" href="?page=2">Siguiente</a>',
                                     card + '<a class="next" href="?page=2">Repetida</a>']
        connector = HtmlCardsConnector(http)
        self.assertEqual(len(connector.collect(source)), 1)
        self.assertEqual(http.get_html.call_count, 2)
        self.assertFalse(connector.collection_complete)

    def test_missing_detail_field_keeps_source_partial_and_continues(self):
        source = self.source(detail_selectors={"start_date": ".date"}, detail_required_fields=["start_date"])
        card = '<article><h2>Obra</h2><a href="/obra/">Ficha</a></article>'
        http = Mock()
        http.get_html.side_effect = [card, '<p>Cambió la estructura</p>']
        connector = HtmlCardsConnector(http)
        self.assertEqual(connector.collect(source), [])
        self.assertFalse(connector.collection_complete)

    def test_external_detail_is_not_fetched(self):
        source = self.source(detail_selectors={"start_date": ".date"})
        http = Mock()
        http.get_html.return_value = '<article><h2>Obra</h2><a href="https://other.cl/obra/">Ficha</a></article>'
        connector = HtmlCardsConnector(http)
        self.assertEqual(connector.collect(source), [])
        self.assertEqual(http.get_html.call_count, 1)
        self.assertFalse(connector.collection_complete)

    def test_expired_event_is_not_reintroduced(self):
        source = self.source()
        http = Mock()
        http.get_html.return_value = '<article><h2>Archivo</h2><time>2020-01-01</time></article>'
        self.assertEqual(HtmlCardsConnector(http).collect(source), [])

    def test_missing_title_does_not_authorize_empty_source_reconciliation(self):
        http = Mock()
        http.get_html.return_value = '<article><time>2099-01-01</time></article>'
        connector = HtmlCardsConnector(http)
        self.assertEqual(connector.collect(self.source()), [])
        self.assertFalse(connector.collection_complete)


if __name__ == "__main__":
    unittest.main()
