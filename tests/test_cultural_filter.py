import unittest

from eventos.cultural_filter import assess_cultural_relevance, filter_cultural_events
from eventos.models import Event


class CulturalFilterTests(unittest.TestCase):
    def test_accepts_broad_cultural_dimensions(self):
        for event in (
            Event(title="Feria del Libro"),
            Event(title="Vendimia", description="Patrimonio y cultura del vino"),
            Event(title="Fonda comunal", description="Cueca, gastronomía y música en vivo"),
            Event(title="Festival de anime y cosplay"),
            Event(title="Muestra gastronómica territorial"),
        ):
            self.assertEqual(assess_cultural_relevance(event).decision, "accepted")

    def test_excludes_industrial_event_without_cultural_dimension(self):
        event = Event(
            title="Expomin 2027",
            categories=["Feria", "Negocios", "Minería"],
            description="Proveedores, networking y oportunidades de negocio para la industria minera.",
        )
        decision = assess_cultural_relevance(event)
        self.assertEqual(decision.decision, "excluded")

    def test_cultural_dimension_overrides_industry_context(self):
        event = Event(
            title="Memoria minera",
            description="Exposición de patrimonio industrial y relatos comunitarios.",
        )
        self.assertEqual(assess_cultural_relevance(event).decision, "accepted")

    def test_does_not_match_culture_or_art_inside_other_words(self):
        event = Event(
            title="AquaSur",
            description="Actores que forman parte de la industria de acuicultura.",
        )
        decision = assess_cultural_relevance(event)
        self.assertNotIn("cultura", decision.cultural_signals)
        self.assertNotIn("arte", decision.cultural_signals)
        self.assertEqual(decision.decision, "excluded")

    def test_review_policy_is_configurable(self):
        event = Event(title="Encuentro ciudadano")
        kept, decisions = filter_cultural_events([event], review_action="keep")
        self.assertEqual(kept, [event])
        self.assertEqual(decisions[0].decision, "review")
        self.assertEqual(event.classification["publication_status"], "published")
        self.assertEqual(event.classification["review_action"], "keep")
        excluded, _ = filter_cultural_events([event], review_action="exclude")
        self.assertEqual(excluded, [])

    def test_keep_preserves_accepted_review_and_excluded_distinction(self):
        accepted = Event(title="Concierto de jazz")
        review = Event(title="Encuentro ciudadano")
        directory = Event(title="Librería L", categories=["Guía de Librerías"])
        sport = Event(title="Entrenamiento funcional")
        kept, decisions = filter_cultural_events([accepted, review, directory, sport], review_action="keep")
        self.assertEqual(kept, [accepted, review])
        self.assertEqual([d.decision for d in decisions], ["accepted", "review", "excluded", "excluded"])
        self.assertEqual([e.classification["publication_status"] for e in (accepted, review, directory, sport)],
                         ["published", "published", "archived", "archived"])
        self.assertEqual(review.categories, [])

    def test_repository_policy_publishes_reviews_without_disabling_filter(self):
        from eventos.config import Settings
        settings = Settings.load()
        self.assertTrue(settings.cultural_filter_enabled)
        self.assertEqual(settings.cultural_review_action, "keep")


if __name__ == "__main__":
    unittest.main()
