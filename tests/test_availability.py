import unittest
from unittest.mock import Mock

import requests

from eventos.availability import EventAvailabilityValidator, is_soft_404
from eventos.config import SourceConfig
from eventos.models import Event


class AvailabilityTests(unittest.TestCase):
    def test_detects_soft_404_only_in_page_identity(self):
        self.assertTrue(is_soft_404("<title>Error 404 - Página no encontrada</title>"))
        self.assertFalse(
            is_soft_404("<title>Festival cultural</title><p>Información y entradas</p>")
        )

    def test_removes_confirmed_404_but_keeps_transient_failure(self):
        missing_response = requests.Response()
        missing_response.status_code = 404
        missing = requests.HTTPError("404", response=missing_response)
        http = Mock()
        available = Mock(status_code=200, text="<title>Evento vigente</title>")
        available.headers = {"content-type": "text/html"}
        http.get_response.side_effect = [missing, requests.Timeout("lento"), available]
        source = SourceConfig.from_dict(
            {
                "name": "Agenda",
                "url": "https://example.cl/agenda",
                "validate_event_urls": True,
            }
        )
        events = [
            Event(title="Retirado", source_name="Agenda", source_url="https://example.cl/1"),
            Event(title="Incierto", source_name="Agenda", source_url="https://example.cl/2"),
            Event(title="Vigente", source_name="Agenda", source_url="https://example.cl/3"),
        ]

        removed = EventAvailabilityValidator(http).apply(events, [source])

        self.assertEqual(removed, 1)
        self.assertEqual([event.title for event in events], ["Incierto", "Vigente"])


if __name__ == "__main__":
    unittest.main()
