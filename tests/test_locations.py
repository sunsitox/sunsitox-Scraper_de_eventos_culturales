import os
import unittest
from unittest.mock import Mock, patch

from eventos.config import SourceConfig
from eventos.locations import LocationNormalizer, needs_ai_location
from eventos.models import Event
from eventos.services.nvidia import (
    NvidiaExtractor,
    NvidiaLocationEnricher,
    NvidiaRateLimiter,
    parse_nvidia_locations,
)


class NoOpEnricher:
    def enrich(self, events):
        return 0


class LocationNormalizerTests(unittest.TestCase):
    def test_source_defaults_produce_a_transparent_venue_location(self):
        source = SourceConfig.from_dict(
            {
                "name": "Teatro",
                "url": "https://example.cl",
                "commune": "Viña del Mar",
                "city": "Viña del Mar",
                "region": "Región de Valparaíso",
                "location_defaults": {"venue": "Teatro Municipal"},
            }
        )
        event = Event(title="Obra", source_name="Teatro")
        normalizer = LocationNormalizer(NoOpEnricher())
        normalizer.apply_source_defaults([event], [source])
        normalizer.finalize([event])
        self.assertEqual(event.venue, "Teatro Municipal")
        self.assertEqual(event.location_precision, "venue")
        self.assertEqual(event.location_source, "source-default")
        self.assertIn("Viña del Mar", event.location)

    def test_source_defaults_keep_validated_coordinates(self):
        source = SourceConfig.from_dict(
            {
                "name": "Recinto fijo",
                "url": "https://example.cl",
                "region": "Región del Maule",
                "location_defaults": {
                    "venue": "Centro Cultural",
                    "latitude": "-35.4264",
                    "longitude": "-71.6554",
                    "postal_code": "3460000",
                },
            }
        )
        event = Event(title="Muestra", source_name="Recinto fijo")
        normalizer = LocationNormalizer(NoOpEnricher())
        normalizer.apply_source_defaults([event], [source])
        self.assertEqual(event.latitude, "-35.4264")
        self.assertEqual(event.longitude, "-71.6554")
        self.assertEqual(event.postal_code, "3460000")

    def test_known_venue_metadata_completes_exact_address(self):
        source = SourceConfig.from_dict(
            {
                "name": "Agenda regional",
                "url": "https://example.cl",
                "location_metadata": {
                    "Teatro Regional": {
                        "address": "Calle Cultura 123",
                        "commune": "Rancagua",
                        "region": "Región del Libertador Bernardo O'Higgins",
                        "latitude": "-34.1708",
                        "longitude": "-70.7444",
                    }
                },
            }
        )
        event = Event(
            title="Concierto en Teatro Regional",
            venue="Teatro Regional",
            source_name="Agenda regional",
        )
        normalizer = LocationNormalizer(NoOpEnricher())
        normalizer.apply_source_defaults([event], [source])
        normalizer.finalize([event])
        self.assertEqual(event.address, "Calle Cultura 123")
        self.assertEqual(event.location_precision, "exact")
        self.assertEqual(event.location_source, "deterministic-inference")

    def test_online_event_has_a_location_without_inventing_an_address(self):
        event = Event(title="Taller online por Zoom", source_name="Agenda")
        normalizer = LocationNormalizer(NoOpEnricher())
        normalizer.apply_source_defaults([event], [])
        normalizer.finalize([event])
        self.assertEqual(event.location, "Online")
        self.assertEqual(event.location_precision, "online")
        self.assertFalse(event.address)

    def test_hybrid_title_does_not_hide_a_published_physical_venue(self):
        event = Event(title="Concierto con transmisión online", venue="Teatro Regional")
        LocationNormalizer.finalize([event])
        self.assertEqual(event.location, "Teatro Regional")
        self.assertEqual(event.location_precision, "venue")

    def test_region_is_last_resort_and_is_marked_as_fallback(self):
        event = Event(title="Actividad", region="Región del Maule")
        LocationNormalizer.finalize([event])
        self.assertEqual(event.location, "Región del Maule")
        self.assertEqual(event.location_precision, "region")
        self.assertEqual(event.location_source, "geographic-fallback")

    def test_only_incomplete_locations_are_ai_candidates(self):
        self.assertTrue(needs_ai_location(Event(title="Actividad", commune="Talca")))
        self.assertTrue(needs_ai_location(Event(title="Actividad", venue="Viña X")))
        self.assertFalse(
            needs_ai_location(Event(title="Actividad", venue="Teatro", commune="Santiago"))
        )


class NvidiaLocationTests(unittest.TestCase):
    def tearDown(self):
        NvidiaExtractor.unavailable_reason = ""

    def test_location_enrichment_uses_one_batched_request(self):
        response = Mock(status_code=200)
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"locations": ['
                            '{"index": 0, "commune": "Casablanca", '
                            '"city": "Casablanca", "region": "Región de Valparaíso"}'
                            "]}"
                        )
                    }
                }
            ]
        }
        events = [
            Event(
                title="Vendimia",
                venue="Viña de Casablanca",
                source_name="Agenda vinícola",
            )
        ]
        env = {
            "NVIDIA_API_KEY": "test-key",
            "NVIDIA_LOCATION_ENRICHMENT": "true",
            "NVIDIA_LOCATION_BATCH_SIZE": "20",
            "NVIDIA_LOCATION_MAX_BATCHES": "5",
        }
        with patch.dict(os.environ, env, clear=False), patch(
            "eventos.services.nvidia.post_nvidia", return_value=response
        ) as request:
            changed = NvidiaLocationEnricher().enrich(events)
        self.assertEqual(changed, 1)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(events[0].commune, "Casablanca")
        self.assertEqual(events[0].location_source, "nvidia-nim")

    def test_location_parser_accepts_provider_preamble(self):
        records = parse_nvidia_locations(
            'Resultado:\n{"locations": [{"index": 0, "commune": "Talca"}]}'
        )
        self.assertEqual(records[0]["commune"], "Talca")

    def test_rate_limiter_spaces_requests_below_forty_rpm(self):
        current = [0.0]
        waits = []

        def clock():
            return current[0]

        def sleeper(seconds):
            waits.append(seconds)
            current[0] += seconds

        limiter = NvidiaRateLimiter(40, clock=clock, sleeper=sleeper)
        limiter.acquire()
        limiter.acquire()
        self.assertEqual(limiter.requests, 2)
        self.assertEqual(len(waits), 1)
        self.assertGreaterEqual(waits[0], 1.55)


if __name__ == "__main__":
    unittest.main()
