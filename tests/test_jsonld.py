import unittest

from eventos.config import SourceConfig
from eventos.extractors import extract_jsonld


class JsonLdTests(unittest.TestCase):
    def test_extracts_schema_event_with_source_defaults(self):
        html = """
        <script type="application/ld+json">
        {
          "@context": "https://schema.org",
          "@type": "Event",
          "name": "Obra de prueba",
          "eventType": ["Teatro", "Cultura", "Teatro"],
          "startDate": "2099-09-01T20:00:00-04:00",
          "organizer": {"@type": "Organization", "name": "Fundación Ejemplo"},
          "location": {"@type": "Place", "name": "Teatro"}
        }
        </script>
        """
        source = SourceConfig.from_dict(
            {
                "name": "Cartelera",
                "url": "https://example.cl",
                "region": "Región de Valparaíso",
                "commune": "Viña del Mar",
            }
        )
        events = extract_jsonld(html, source, source.url)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].title, "Obra de prueba")
        self.assertEqual(events[0].region, "Región de Valparaíso")
        self.assertEqual(events[0].commune, "Viña del Mar")
        self.assertEqual(events[0].categories, ["Teatro", "Cultura"])
        self.assertEqual(events[0].organizer, "Fundación Ejemplo")


if __name__ == "__main__":
    unittest.main()
