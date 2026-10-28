import json
import os
import tempfile
import unittest
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from eventos.classification import CLASSIFICATION_VERSION, CANONICAL_CATEGORIES, classify_event
from eventos.cultural_filter import assess_cultural_relevance, filter_cultural_events
from eventos.enrichment_queue import EnrichmentQueue
from eventos.exporters import load_json_dataset, write_dataset
from eventos.models import Event
from eventos.services.category_classifier import NvidiaCategoryClassifier
from eventos.services.nvidia import NvidiaExtractor
from eventos.services.supabase import build_supabase_bundle
from eventos.state import StateStore, event_rows, load_events
from eventos.pipeline import EventPipeline
from eventos.config import SourceConfig, Settings
from eventos.analytics import SourceSummary


class ClassificationTests(unittest.TestCase):
    def test_directory_guides_are_excluded_not_renamed_as_literature(self):
        for label in ('Guía de Librerías', 'Guía de ferias y festivales del libro'):
            event = Event(title='Librería L', categories=[label], start_date='2025-01-01', end_date='2030-12-31')
            decision = assess_cultural_relevance(event)
            self.assertEqual(decision.decision, 'excluded')
            self.assertEqual(event.classification['content_kind'], 'directory')
            self.assertEqual(event.classification['raw_categories'], [label])

    def test_museum_directory_is_not_an_exhibition(self):
        event = Event(title='Museo de Quillota', venue='Museo de Quillota', categories=['Museo'], start_date='2019-01-01', end_date='2030-12-31')
        self.assertEqual(assess_cultural_relevance(event).decision, 'excluded')

    def test_long_duration_alone_never_excludes_real_program(self):
        event = Event(title='Exposición de pintura contemporánea', categories=['Artes visuales'], start_date='2025-01-01', end_date='2028-12-31')
        self.assertEqual(assess_cultural_relevance(event).decision, 'accepted')
        self.assertEqual(event.classification['content_kind'], 'event')

    def test_cultural_event_at_library_is_preserved(self):
        event = Event(title='Presentación del libro Vientos', venue='Librería L', categories=['Literatura'])
        self.assertEqual(assess_cultural_relevance(event).decision, 'accepted')

    def test_discipline_format_audience_and_program_are_separate(self):
        event = Event(title='Concierto de jazz', categories=['CONCIERTO', 'Música Popular', 'Familiar', 'PAOCC', 'FTB'])
        classify_event(event)
        self.assertEqual(event.categories, ['Música'])
        self.assertEqual(event.classification['formats'], ['Concierto'])
        self.assertEqual(event.classification['audiences'], ['Familiar'])
        self.assertEqual(event.classification['programs'], ['PAOCC', 'FTB'])

    def test_environment_spellings_are_not_spurious_discipline(self):
        event = Event(title='Encuentro ciudadano', categories=['Medioambiente', 'Medio ambiente'])
        classify_event(event)
        self.assertEqual(event.categories, [])
        self.assertEqual(len(event.classification['raw_categories']), 2)

    def test_multiple_disciplines_are_kept(self):
        event = Event(title='Feria de artesanía y gastronomía', categories=['Música', 'Danza'])
        classify_event(event)
        self.assertTrue({'Música', 'Danza', 'Artesanía', 'Gastronomía'}.issubset(event.categories))

    def test_ticketplus_without_genre_uses_explicit_content(self):
        event = Event(title='Jazz club: concierto de jazz', source_name='Ticketplus · Chile')
        classify_event(event)
        self.assertEqual(event.categories, ['Música'])
        self.assertEqual(assess_cultural_relevance(event).decision, 'accepted')

    def test_culture_source_organizer_or_venue_cannot_accept_unknown_event(self):
        event = Event(title='Encuentro ciudadano', source_name='Chile Cultura', organizer='Ministerio de las Culturas', venue='Teatro Municipal', categories=['Cultura'])
        self.assertEqual(assess_cultural_relevance(event).decision, 'review')
        self.assertEqual(event.categories, [])

    def test_non_cultural_sports_services_and_academic_activities_are_excluded(self):
        for title in ('Básquetbol formativo', 'Entrenamiento funcional', 'Día de Spa', 'Taller ORCID', 'Servicio de acompañamiento telefónico'):
            event = Event(title=title, source_name='Chile Cultura', categories=['Cultura'])
            self.assertEqual(assess_cultural_relevance(event).decision, 'excluded', title)

    def test_folkloric_dance_is_not_excluded_as_exercise(self):
        event = Event(title='Taller de cueca y danza tradicional', categories=['Deportes'])
        self.assertEqual(assess_cultural_relevance(event).decision, 'accepted')

    def test_background_music_does_not_turn_fitness_into_concert(self):
        event = Event(title='ZUMBA 2026', description='Ejercicio al ritmo de la música', categories=['Deportes'])
        self.assertEqual(assess_cultural_relevance(event).decision, 'excluded')

    def test_martial_arts_need_context_not_automatic_philosophy_category(self):
        event = Event(title='Kung-fu 2026', description='Técnica física y filosofía', categories=['Deportes'])
        self.assertEqual(assess_cultural_relevance(event).decision, 'review')

    def test_review_is_a_draft_not_published_as_other(self):
        event = Event(title='Encuentro ciudadano', start_date='2099-01-01', source_url='https://example.cl/e')
        events, _ = filter_cultural_events([event])
        row = build_supabase_bundle(events).events[0]
        self.assertEqual(row['status'], 'draft')
        self.assertEqual(events[0].categories, [])
        self.assertEqual(build_supabase_bundle(events).event_provenance[0]['classification']['decision'], 'review')

    def test_classification_is_idempotent_and_survives_old_checkpoint(self):
        event = Event(title='Concierto', categories=['CONCIERTO', 'Familiar'])
        classify_event(event)
        first = asdict(event)
        classify_event(event)
        self.assertEqual(asdict(event), first)
        self.assertEqual(load_events(event_rows([event]))[0], event)
        self.assertEqual(load_events([{'title': 'Antiguo'}])[0].classification, {})

    def test_json_and_csv_keep_structured_classification(self):
        event = Event(title='Teatro', categories=['FTB', 'Teatro'])
        classify_event(event)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            write_dataset([event], path/'events.json', path/'events.csv')
            self.assertEqual(load_json_dataset(path/'events.json')[0].classification, event.classification)
            self.assertIn('classification', (path/'events.csv').read_text(encoding='utf-8-sig'))

    def test_taxonomy_change_does_not_invalidate_unchanged_rewrite(self):
        with tempfile.TemporaryDirectory() as directory:
            queue = EnrichmentQueue(StateStore(Path(directory)/'state.sqlite3'))
            raw = Event(event_id='same', title='Concierto', categories=['CONCIERTO'], source_description='Original')
            queue.reconcile_input(raw, raw)
            normalized_event = replace(raw, is_rewritten=True)
            classify_event(normalized_event)
            queue.reconcile_input(normalized_event, normalized_event)
            self.assertTrue(normalized_event.is_rewritten)


class CategoryAItests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.queue = EnrichmentQueue(StateStore(Path(self.directory.name)/'state.sqlite3'))
        self.env = patch.dict(os.environ, {'NVIDIA_API_KEY':'fake-test-only', 'NVIDIA_CLASSIFICATION_ENABLED':'true'}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        NvidiaExtractor.unavailable_reason = ''
        self.addCleanup(setattr, NvidiaExtractor, 'unavailable_reason', '')

    @staticmethod
    def response(rows):
        response = Mock(status_code=200, headers={})
        response.json.return_value = {'choices':[{'message':{'content':json.dumps({'events':rows})}}]}
        return response

    def test_known_categories_and_directories_never_call_llm(self):
        events = [Event(title='Concierto de jazz'), Event(title='Librería', categories=['Guía de Librerías'])]
        with patch('eventos.services.category_classifier.post_nvidia') as api:
            self.assertEqual(NvidiaCategoryClassifier(self.queue).enrich(events), 0)
        api.assert_not_called()

    def test_failed_remote_verification_disables_new_ai_requests(self):
        with patch('eventos.services.category_classifier.post_nvidia') as api:
            NvidiaCategoryClassifier(self.queue).enrich([Event(title='Actividad desconocida')],allow_requests=False)
        api.assert_not_called()

    def test_valid_evidence_is_cached_and_reused(self):
        raw = Event(title='La voz del sur', description='Una presentación con instrumentos y canciones latinoamericanas.')
        rows = [{'index':0,'evidence':[{'category':'Música','quote':'instrumentos y canciones latinoamericanas'}]}]
        with patch('eventos.services.category_classifier.post_nvidia', return_value=self.response(rows)) as api:
            self.assertEqual(NvidiaCategoryClassifier(self.queue).enrich([replace(raw)]), 1)
            resumed = replace(raw)
            NvidiaCategoryClassifier(EnrichmentQueue(self.queue.store)).enrich([resumed])
        self.assertEqual(api.call_count, 1)
        self.assertEqual(resumed.categories, ['Música'])
        self.assertFalse(api.call_args.args[0]['stream'])

    def test_invalid_category_or_invented_quote_stays_pending(self):
        event = Event(title='Evento ambiguo', description='Sin información adicional disponible')
        for evidence in ([{'category':'Deportes','quote':'información adicional'}], [{'category':'Música','quote':'concierto de jazz inexistente'}]):
            with patch('eventos.services.category_classifier.post_nvidia', return_value=self.response([{'index':0,'evidence':evidence}])):
                NvidiaCategoryClassifier(EnrichmentQueue(self.queue.store)).enrich([event])
            self.assertEqual(event.categories, [])
            self.assertEqual(self.queue.store.get('ai:classify', self.queue.key('classify', event))['status'], 'retryable_error')

    def test_empty_evidence_is_completed_without_inventing_category(self):
        event = Event(title='Actividad sin detalles')
        with patch('eventos.services.category_classifier.post_nvidia', return_value=self.response([{'index':0,'evidence':[]}])) as api:
            classifier = NvidiaCategoryClassifier(self.queue)
            classifier.enrich([event])
            NvidiaCategoryClassifier(EnrichmentQueue(self.queue.store)).enrich([event])
        self.assertEqual(api.call_count, 1)
        self.assertEqual(event.categories, [])

    def test_timeouts_preserve_pending_and_open_circuit(self):
        events = [Event(title=f'Actividad desconocida {i}') for i in range(5)]
        with patch.dict(os.environ, {'NVIDIA_CLASSIFICATION_BATCH_SIZE':'1'}), patch('eventos.services.category_classifier.post_nvidia', side_effect=requests.Timeout('lento')) as api:
            NvidiaCategoryClassifier(self.queue).enrich(events)
        self.assertEqual(api.call_count, 3)
        self.assertEqual(self.queue.store.get('ai:classify', self.queue.key('classify', events[3]))['status'], 'pending')

    def test_changed_input_invalidates_ai_evidence(self):
        event = Event(title='Actividad', description='Instrumentos y canciones latinoamericanas')
        classify_event(event)
        event.classification['ai_evidence']=[{'category':'Música','quote':'Instrumentos y canciones latinoamericanas'}]
        classify_event(event)
        self.assertEqual(event.categories, ['Música'])
        event.description = 'No se dispone de información adicional'
        classify_event(event)
        self.assertEqual(event.categories, [])

    def test_remote_evidence_survives_lost_local_cache(self):
        from eventos.services.supabase import SupabaseExporter
        event = Event(title='La voz del sur', description='Instrumentos y canciones latinoamericanas', event_id='stable')
        classify_event(event)
        metadata = dict(event.classification, ai_checked=True, ai_evidence=[{'category':'Música','quote':'Instrumentos y canciones latinoamericanas'}])
        client = Mock()
        def select(table, params):
            return [{'event_id':params['event_id'][4:-1], 'classification':metadata}]
        client.select.side_effect = select
        SupabaseExporter(client).restore_classification_evidence([event])
        with patch('eventos.services.category_classifier.post_nvidia') as api:
            NvidiaCategoryClassifier(self.queue).enrich([event])
        api.assert_not_called()
        self.assertEqual(event.categories,['Música'])


class ClassificationPipelineTests(unittest.TestCase):
    def test_existing_rows_are_reclassified_and_directories_never_reach_editorial_ai(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(Path(directory)/'state.sqlite3')
            source = SourceConfig.from_dict({'name':'Test','url':'https://example.cl'})
            events = [
                Event(title='Concierto de jazz',start_date='2099-01-01',source_name='Test'),
                Event(title='Librería',categories=['Guía de Librerías'],start_date='2099-01-02',source_name='Test'),
                Event(title='Encuentro ciudadano',start_date='2099-01-03',source_name='Test'),
            ]
            remote = Mock()
            remote.prepare_for_enrichment.side_effect=lambda rows:(rows,[])
            content = Mock()
            content.enrich.return_value={'ocr':0,'rewritten':0}
            pipeline=EventPipeline(repository=Mock(load=Mock(return_value=[source])),
                                   exporter=Mock(),supabase_exporter=remote,content_enricher=content,state=store)
            with patch('eventos.pipeline.load_dotenv'),patch('eventos.pipeline.configure_nvidia_state'),patch(
                'eventos.pipeline.Settings.load',return_value=Settings()),patch(
                'eventos.pipeline.write_source_summaries'),patch('eventos.pipeline.write_cultural_audit'),patch(
                'eventos.pipeline.NvidiaCategoryClassifier.enrich',return_value=0),patch.object(
                pipeline,'_collect_sources',return_value=(events,[SourceSummary('Test','example.cl','succeeded',3,0,0,0)])):
                output=pipeline.run(push_to_supabase=True)
            content.enrich.assert_called_once_with([])
            self.assertEqual(len(output),2)
            self.assertEqual(output[0].categories,['Música'])
            self.assertEqual(output[1].classification['publication_status'],'draft')
            self.assertEqual(remote.export.call_args.kwargs['classification_rejections'][0].title,'Librería')
            remote.require_classification_schema.assert_called_once()


if __name__ == '__main__':
    unittest.main()
