import unittest
from datetime import timedelta

from eventos.config import SourceConfig
from eventos.connectors.json_api import JsonApiConnector
from eventos.models import Event
from eventos.sanitization import EventSanitizer
from eventos.text import chile_now, is_upcoming, text_from_html


class HtmlSanitizationTests(unittest.TestCase):
    def test_removes_encoded_wordpress_builder_and_css(self):
        contaminated = (
            '<div class="wpb-content-wrapper">'
            '[vc_section css=&#8221;.vc_custom_1{padding-top:3rem !important;}&#8221;]'
            '[vc_column_text]Descripción visible del evento.[/vc_column_text]'
            '.otra-clase{background-color:#fff !important;}'
            '</div>'
        )
        result = text_from_html(contaminated)
        self.assertEqual(result, "Descripción visible del evento.")

    def test_global_sanitizer_filters_expired_and_compacts_same_list(self):
        events = [
            Event(
                title="<strong>Evento vigente</strong>",
                start_date="2099-01-01",
                end_date="2099-01-02",
                description="<style>.x{color:red}</style><p>Contenido</p>",
                categories=["<b>Música</b>"],
            ),
            Event(title="Evento vencido", start_date="2000-01-01", end_date="2000-01-01"),
        ]
        original_id = id(events)
        result = EventSanitizer().apply(events)
        self.assertEqual(id(result), original_id)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].title, "Evento vigente")
        self.assertEqual(result[0].description, "Contenido")
        self.assertEqual(result[0].categories, ["Música"])

    def test_global_sanitizer_discards_missing_and_invalid_start_dates(self):
        events = [
            Event(title="Sin fecha"),
            Event(title="Fecha textual", start_date="Fecha por confirmar"),
            Event(title="Válido", start_date="2099-09-01 20:00"),
        ]
        EventSanitizer().apply(events)
        self.assertEqual([event.title for event in events], ["Válido"])
        self.assertIn(events[0].start_date[-6:], {"-03:00", "-04:00"})

    def test_vigency_uses_end_time_but_keeps_date_only_through_the_day(self):
        current = chile_now()
        finished = Event(end_date=(current - timedelta(minutes=1)).isoformat())
        all_day = Event(end_date=current.date().isoformat())
        self.assertFalse(is_upcoming(finished, current))
        self.assertTrue(is_upcoming(all_day, current))


class FakeJsonHttp:
    def get_json(self, url, params=None, verify=True):
        return {
            "results": [
                {
                    "name": "Feria cultural",
                    "dates": {"start": "2099-10-01", "end": "2099-10-02"},
                    "url": "https://example.cl/feria",
                }
            ],
            "page_count": 1,
        }


class JsonApiConnectorTests(unittest.TestCase):
    def test_maps_a_new_api_using_only_source_configuration(self):
        source = SourceConfig.from_dict(
            {
                "name": "API de ejemplo",
                "url": "https://example.cl/agenda",
                "api_url": "https://example.cl/api/events",
                "connector": "json_api",
                "results_path": "results",
                "total_pages_path": "page_count",
                "field_map": {
                    "title": "name",
                    "start_date": "dates.start",
                    "end_date": "dates.end",
                    "source_url": "url",
                },
            }
        )
        events = JsonApiConnector(FakeJsonHttp()).collect(source)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].title, "Feria cultural")
        self.assertEqual(events[0].start_date[:10], "2099-10-01")


if __name__ == "__main__":
    unittest.main()
