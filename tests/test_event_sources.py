import unittest
from datetime import datetime
from unittest.mock import Mock, patch

from eventos.config import SourceConfig
from eventos.connectors.eventon import event_from_microdata
from eventos.connectors.fisa import expo_categories, fisa_date_range, location_fields
from eventos.connectors.generic import candidate_links
from eventos.connectors.ticketplus import TicketplusChileConnector, ticketplus_regional_sources
from eventos.connectors.teatro_biobio import TeatroBiobioConnector, event_datetime
from eventos.connectors.html_cards import (
    HtmlCardsConnector,
    spanish_abbreviated_range,
    spanish_flexible_range,
    spanish_same_month_range,
)
from eventos.locations import LocationNormalizer
from eventos.models import Event


class FisaTests(unittest.TestCase):
    def test_parses_single_month_range(self):
        self.assertEqual(
            fisa_date_range("20 al 22 octubre 2026"),
            ("2026-10-20", "2026-10-22"),
        )

    def test_parses_cross_month_range(self):
        self.assertEqual(
            fisa_date_range("Sep 29, 30, 01 Oct al 2026"),
            ("2026-09-29", "2026-10-01"),
        )

    def test_classifies_expo_and_location(self):
        categories = expo_categories("Expo Salud 2026", ["Exposición", "Feria"])
        location = location_fields("Espacio Riesco, Avenida El Salto 5000, Huechuraba")
        self.assertEqual(categories, ["Exposición", "Feria", "Salud"])
        self.assertEqual(location[2:], ("Huechuraba", "Región Metropolitana de Santiago"))


class TeatroBiobioTests(unittest.TestCase):
    def test_extracts_visible_numeric_date_and_time(self):
        content = "<h2>28/09/2099</h2><p>Fecha y hora: Lunes 28 de septiembre, 19:30 h.</p>"
        self.assertEqual(event_datetime(content), "2099-09-28T19:30:00")

    def test_reads_only_cartelera_posts_without_nvidia(self):
        class Response:
            headers = {"X-WP-TotalPages": "1"}

            def json(self):
                return [{
                    "title": {"rendered": "<em>Obra de prueba</em>"},
                    "content": {"rendered": "<p>Fecha y hora: 28 de septiembre de 2099, 19:30 h.</p>"},
                    "link": "https://teatrobiobio.cl/obra-de-prueba/",
                    "_embedded": {
                        "wp:term": [[{"name": "Cartelera"}, {"name": "Teatro"}]],
                        "wp:featuredmedia": [{"source_url": "https://teatrobiobio.cl/afiche.jpg"}],
                    },
                }]

        class Http:
            def __init__(self):
                self.calls = []

            def get_json(self, url, *, params):
                self.calls.append((url, params))
                return [{"id": 77, "slug": "cartelera"}]

            def get_response(self, url, *, params, verify):
                self.calls.append((url, params))
                return Response()

        source = SourceConfig.from_dict({
            "name": "Teatro Biobío",
            "url": "https://teatrobiobio.cl/categoria/cartelera/",
            "connector": "teatro_biobio",
            "region": "Región del Biobío",
            "commune": "Concepción",
            "venue": "Teatro Biobío",
            "default_categories": ["Artes escénicas"],
        })
        http = Http()
        events = TeatroBiobioConnector(http).collect(source)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].start_date, "2099-09-28T19:30:00")
        self.assertEqual(events[0].categories, ["Teatro"])
        self.assertEqual(events[0].venue, "Teatro Biobío")
        self.assertEqual(http.calls[1][1]["categories"], 77)


class EventOnTests(unittest.TestCase):
    def test_uses_event_title_instead_of_nested_location_name(self):
        html = """
        <div itemscope itemtype="http://schema.org/Event">
          <meta itemprop="startDate" content="2099-9-1T12:00-3:00">
          <div itemprop="location" itemscope itemtype="http://schema.org/Place">
            <span itemprop="name">Viña Ejemplo</span>
            <span itemprop="address" itemscope>
              <span itemprop="streetAddress">Pirque</span>
            </span>
          </div>
          <div itemprop="organizer" itemscope>
            <meta itemprop="name" content="Organizador del vino">
          </div>
          <span class="evcal_event_title" itemprop="name">Fiesta de la Vendimia</span>
          <div itemprop="description">Celebración cultural del vino.</div>
        </div>
        """
        source = SourceConfig.from_dict(
            {
                "name": "Agenda vinícola",
                "url": "https://example.cl",
                "commune_by_location": {"Pirque": "Pirque"},
                "region_by_commune": {"Pirque": "Región Metropolitana de Santiago"},
                "default_categories": ["Vino"],
            }
        )
        event = event_from_microdata(
            html,
            source,
            source.url,
            categories=["Vino", "Fiesta de la Vendimia"],
            region="Región incorrecta",
        )
        self.assertIsNotNone(event)
        self.assertEqual(event.title, "Fiesta de la Vendimia")
        self.assertEqual(event.commune, "Pirque")
        self.assertEqual(event.region, "Región Metropolitana de Santiago")
        self.assertEqual(event.organizer, "Organizador del vino")
        self.assertEqual(event.categories, ["Vino", "Fiesta de la Vendimia"])


class GenericConnectorTests(unittest.TestCase):
    def test_ignores_webcal_subscription_links(self):
        html = """
        <a href="webcal://example.cl/?post_type=tribe_events&ical=1">Suscribirse</a>
        <a href="/evento/agenda-cultural">Ver evento</a>
        """
        self.assertEqual(
            candidate_links(html, "https://example.cl/event-directory/"),
            ["https://example.cl/evento/agenda-cultural"],
        )

    def test_can_restrict_discovered_links_to_configured_event_path(self):
        html = """
        <a href="/users/sign_in">Ingreso</a>
        <a href="/events/concierto">Concierto</a>
        <a href="/taxons/musica">Música</a>
        """
        links = candidate_links(
            html,
            "https://ticketplus.cl/states/region-metropolitana",
            path_prefixes=("/events/",),
        )
        self.assertEqual(links, ["https://ticketplus.cl/events/concierto"])


class TicketplusTests(unittest.TestCase):
    def test_discovers_canonical_regional_carteleras_from_public_selector(self):
        source = SourceConfig.from_dict({
            "name": "Ticketplus · Chile",
            "url": "https://ticketplus.cl/states/region-metropolitana",
            "connector": "ticketplus_chile",
            "incremental": True,
        })
        html = '''<select id="select-region">
          <option value="">Buscar por Región</option>
          <option value="region-metropolitana">Región Metropolitana de Santiago</option>
          <option value="region-de-la-araucania">IX Región de la Araucanía</option>
        </select>'''
        pages = ticketplus_regional_sources(source, html)
        self.assertEqual([page.url for page in pages], [
            "https://ticketplus.cl/states/region-metropolitana",
            "https://ticketplus.cl/states/region-de-la-araucania",
        ])
        self.assertEqual([page.region for page in pages], [
            "Región Metropolitana de Santiago", "Región de la Araucanía",
        ])
        self.assertTrue(all(page.get("allow_empty_listing") for page in pages))

    @patch("eventos.connectors.ticketplus.GenericConnector.collect")
    @patch("eventos.connectors.ticketplus.ticketplus_regional_sources")
    def test_continues_with_other_regions_when_one_region_fails(self, regional_sources, collect):
        source = SourceConfig.from_dict({
            "name": "Ticketplus · Chile",
            "url": "https://ticketplus.cl/",
            "connector": "ticketplus_chile",
        })
        regional_sources.return_value = [
            SourceConfig.from_dict({"name": source.name, "url": "https://ticketplus.cl/states/a", "region": "Región A"}),
            SourceConfig.from_dict({"name": source.name, "url": "https://ticketplus.cl/states/b", "region": "Región B"}),
        ]
        collect.side_effect = [RuntimeError("HTML incompatible"), ["evento-b"]]
        http = Mock()
        http.get_html.return_value = "<select id='select-region'></select>"

        connector = TicketplusChileConnector(http)
        events = connector.collect(source)

        self.assertEqual(events, ["evento-b"])
        self.assertFalse(connector.collection_complete)
        self.assertEqual(collect.call_count, 2)


class HtmlCardsTests(unittest.TestCase):
    def test_parses_spanish_fonda_date_range(self):
        start, end = spanish_same_month_range("Del 11 al 20 de septiembre")
        self.assertTrue(start.endswith("09-11T00:00:00"))
        self.assertTrue(end.endswith("09-20T00:00:00"))

    def test_keeps_single_spanish_fonda_date(self):
        start, end = spanish_same_month_range("18 de septiembre")
        self.assertTrue(start.endswith("09-18T00:00:00"))
        self.assertEqual(end, "")

    def test_parses_spanish_range_joined_with_y(self):
        start, end = spanish_same_month_range("24 y 25 de septiembre")
        self.assertTrue(start.endswith("09-24T00:00:00"))
        self.assertTrue(end.endswith("09-25T00:00:00"))

    def test_parses_abbreviated_cross_month_range(self):
        start, end = spanish_abbreviated_range("04 SEP — 16 OCT")
        self.assertTrue(start.endswith("09-04T00:00:00"))
        self.assertTrue(end.endswith("10-16T00:00:00"))

    def test_parses_abbreviated_single_date_and_time(self):
        start, end = spanish_abbreviated_range("26 SEP · 19.00 H.")
        self.assertTrue(start.endswith("09-26T19:00:00"))
        self.assertEqual(end, "")

    def test_parses_flexible_cross_month_range_with_time(self):
        start, end = spanish_flexible_range(
            "22 de Septiembre al 21 de Noviembre - 18:00",
            current=datetime(2026, 9, 21),
        )
        self.assertEqual(start, "2026-09-22T18:00:00")
        self.assertEqual(end, "2026-11-21T18:00:00")

    def test_parses_flexible_same_month_range_with_time(self):
        start, end = spanish_flexible_range(
            "24 al 26 de Septiembre - 20:00",
            current=datetime(2026, 9, 21),
        )
        self.assertEqual(start, "2026-09-24T20:00:00")
        self.assertEqual(end, "2026-09-26T20:00:00")

    def test_flexible_parser_rejects_year_without_event_date(self):
        self.assertEqual(
            spanish_flexible_range("2026", current=datetime(2026, 9, 21)),
            ("", ""),
        )

    def test_ceina_cards_are_mapped_to_canonical_event_fields(self):
        html = """
        <article class="bde-loop-item ee-post">
          <img src="/uploads/evento.jpg">
          <div>
            <h5 class="bde-heading">El color del cuerpo</h5>
            <div><a rel="tag">Danza - Performance</a></div>
            <div class="bde-text">24 al 26 de Septiembre - 20:00</div>
          </div>
          <div class="bde-text-156-135">Una obra de danza contemporánea.</div>
          <a href="https://ceina.cl/cartelera/el-color-del-cuerpo/">Quiero saber más</a>
        </article>
        """
        source = SourceConfig.from_dict(
            {
                "name": "Centro Cultural CEINA",
                "url": "https://ceina.cl/cartelera/",
                "connector": "html_cards",
                "region": "Región Metropolitana de Santiago",
                "commune": "Santiago",
                "city": "Santiago",
                "organizer": "Fundación CEINA",
                "venue": "Centro Cultural CEINA",
                "address": "Arturo Prat 33, Santiago",
                "default_categories": ["Cultura"],
                "category_rules": {"danza|performance": ["Danza", "Artes escénicas"]},
                "card_selector": "article.bde-loop-item.ee-post",
                "date_range_parser": "spanish_flexible",
                "selectors": {
                    "title": "h5.bde-heading",
                    "date_range": "h5.bde-heading ~ div.bde-text",
                    "category": "a[rel='tag']",
                    "description": ".bde-text-156-135",
                    "image_url": {"selector": "img", "attribute": "src"},
                    "source_url": {"selector": "a[href*='/cartelera/']", "attribute": "href"},
                },
            }
        )
        http = Mock()
        http.get_html.return_value = html
        with patch("eventos.connectors.html_cards.chile_now", return_value=datetime(2026, 9, 21)):
            events = HtmlCardsConnector(http).collect(source)
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event.title, "El color del cuerpo")
        self.assertEqual(event.start_date, "2026-09-24T20:00:00")
        self.assertEqual(event.end_date, "2026-09-26T20:00:00")
        self.assertEqual(event.venue, "Centro Cultural CEINA")
        self.assertEqual(event.address, "Arturo Prat 33, Santiago")
        self.assertEqual(event.commune, "Santiago")
        self.assertEqual(event.region, "Región Metropolitana de Santiago")
        self.assertEqual(event.organizer, "Fundación CEINA")
        self.assertEqual(event.categories, ["Danza - Performance", "Danza", "Artes escénicas"])
        self.assertEqual(event.source_url, "https://ceina.cl/cartelera/el-color-del-cuerpo/")
        self.assertEqual(event.official_url, event.source_url)


class RegionNormalizationTests(unittest.TestCase):
    def test_normalizes_schema_region_and_uses_territorial_source_default(self):
        sources = [
            SourceConfig.from_dict({"name": "GAM", "url": "https://example.cl/gam", "region": "Región Metropolitana de Santiago"}),
            SourceConfig.from_dict({"name": "Rancagua", "url": "https://example.cl/rancagua", "region": "Región del Libertador Bernardo O'Higgins"}),
        ]
        events = [
            Event(source_name="GAM", region="Región Metropolitana"),
            Event(source_name="Rancagua", region="Rancagua", commune="Rancagua"),
        ]
        LocationNormalizer().apply_source_defaults(events, sources)
        self.assertEqual(events[0].region, "Región Metropolitana de Santiago")
        self.assertEqual(events[1].region, "Región del Libertador Bernardo O'Higgins")

    def test_infers_fonda_region_written_beside_commune(self):
        event = Event(region="Todas", commune="Pucón, La Araucanía")
        LocationNormalizer().apply_source_defaults([event], [])
        self.assertEqual(event.region, "Región de la Araucanía")


if __name__ == "__main__":
    unittest.main()
