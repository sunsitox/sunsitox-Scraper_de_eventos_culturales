"""Regresiones de reemplazos públicos y contenido original antes de clasificar."""
import json
import unittest
from datetime import datetime
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from eventos.classification import classify_event, input_signature
from eventos.config import SourceConfig, SourceRepository, Settings
from eventos.connectors import ConnectorFactory
from eventos.connectors.generic import GenericConnector, augment_detail
from eventos.connectors.html_cards import spanish_flexible_range
from eventos.cultural_filter import assess_cultural_relevance
from eventos.mapping import get_path, map_fields
from eventos.models import Event
from eventos.sanitization import EventSanitizer


NOW = datetime(2026, 10, 28, 12, tzinfo=ZoneInfo('America/Santiago'))


class ReplacementSourceTests(unittest.TestCase):
    def setUp(self):
        self.sources = {s.name: s for s in SourceRepository().load()}

    def test_403_sources_are_retired_not_left_as_active_configs(self):
        for name in ('Teatro del Lago', 'Teatro Oriente'):
            self.assertNotIn(name, self.sources)
            self.assertIn(name, Settings.load().retired_source_names)
        self.assertEqual(len(self.sources), 27)

    def test_maule_excludes_historical_archive_and_other_communes(self):
        source = self.sources['Teatro Regional del Maule']
        listing = '''<div class="entry-content"><p>
            <a href="/archivo/1"><img src="/one.jpg"></a>
            <a href="/archivo/1"><img src="/duplicate.jpg"></a>
            <a href="/archivo/2"><img src="/extension.jpg"></a></p>
            <p>EVENTOS REALIZADOS <a>Ver más</a></p>
            <p><a href="/archivo/3"><img src="/old.jpg"></a></p></div>'''
        def detail(venue):
            return f'''<h1 class="entry-title">Humedales fantásticos</h1><div class="entry-content">
            <table><tr><td>FECHA</td><td>icon</td><td>MARTES 10 DE NOVIEMBRE 2026</td></tr>
            <tr><td>HORA</td><td>icon</td><td>11:00 HRS.</td></tr>
            <tr><td>LUGAR</td><td>icon</td><td>{venue}</td></tr>
            <tr><td>ENTRADAS</td><td>icon</td><td>GRATUITO PREVIA INSCRIPCIÓN</td></tr></table>
            <p>Obra de teatro familiar.</p></div>'''
        pages = {source.url: listing, 'https://www.teatroregional.cl/archivo/1': detail('SALA TRM'),
                 'https://www.teatroregional.cl/archivo/2': detail('Teatro Municipal de Linares')}
        http = Mock()
        http.get_html.side_effect = lambda url, **kwargs: pages[url]
        connector = ConnectorFactory(http).create(source)
        with patch('eventos.text.chile_now', return_value=NOW), patch('eventos.connectors.html_cards.chile_now', return_value=NOW):
            events = connector.collect(source)
        EventSanitizer().apply(events, source)
        self.assertEqual(len(events), 1)
        self.assertEqual(http.get_html.call_count, 3)
        self.assertTrue(connector.collection_complete)
        self.assertEqual(events[0].start_date, '2026-11-10T11:00:00-03:00')
        self.assertEqual(events[0].commune, 'Talca')
        self.assertEqual(events[0].is_free, 'true')

    def test_maule_missing_archive_boundary_does_not_authorize_cleanup(self):
        source = self.sources['Teatro Regional del Maule']
        http = Mock(get_html=Mock(return_value='<div class="entry-content">Cambió el HTML</div>'))
        with self.assertRaisesRegex(ValueError, 'límite'):
            ConnectorFactory(http).create(source).collect(source)

    def test_museum_pagination_iso_dates_and_full_content(self):
        source = self.sources['Museo de la Memoria y los Derechos Humanos']
        def row(slug, date):
            return {'attributes': {'titulo': 'Presentación del libro', 'slug': slug,
                'fecha_y_hora_de_inicio': date, 'fecha_y_hora_de_termino': None,
                'lugar': 'Auditorio MMDH', 'precio': 'Entrada liberada',
                'contenido': [{'campo': '<p>Primera parte.</p>'}, {'__component': 'imagen'}, {'campo': '<p>Segunda parte.</p>'}],
                'tipo_de_evento': {'data': {'attributes': {'tipo_de_evento': 'Lanzamiento'}}},
                'poster': {'data': {'attributes': {'url': '/uploads/poster.png'}}}}}
        http = Mock()
        http.get_json.side_effect = [
            {'data': [row('libro', '2099-11-03T21:30:00.000Z')], 'meta': {'pagination': {'pageCount': 2}}},
            {'data': [row('archivo', '2020-01-01T12:00:00.000Z')], 'meta': {'pagination': {'pageCount': 2}}}]
        events = ConnectorFactory(http).create(source).collect(source)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].source_url, 'https://mmdh.cl/cartelera/libro')
        self.assertEqual(events[0].official_url, events[0].source_url)
        self.assertEqual(events[0].image_url, 'https://manager.mmdh.cl/uploads/poster.png')
        self.assertIn('Segunda parte.', events[0].description)
        self.assertEqual(events[0].start_date, '2099-11-03T21:30:00+00:00')
        self.assertEqual(http.get_json.call_args.kwargs['params']['pagination[page]'], 2)

    def test_explicit_year_is_never_replaced_by_current_year(self):
        for text, start, end in (
            ('10 de noviembre de 2025', '2025-11-10', ''),
            ('Del 10 al 20 de noviembre 2025', '2025-11-10', '2025-11-20'),
            ('28 de diciembre de 2026 al 3 de enero de 2027', '2026-12-28', '2027-01-03'),
            ('28 de diciembre al 3 de enero de 2027', '2026-12-28', '2027-01-03'),
        ):
            first, last = spanish_flexible_range(text, current=NOW)
            self.assertTrue(first.startswith(start), text)
            self.assertTrue(last.startswith(end) if end else not last, text)

    def test_wildcard_mapping_keeps_legacy_paths_and_all_text_blocks(self):
        row = {'body': [{'text': 'uno'}, {'image': 'ignored'}, {'text': 'dos'}]}
        self.assertEqual(get_path(row, 'body.0.text'), 'uno')
        self.assertEqual(map_fields(row, {'description': {'path': 'body.*.text', 'join': '\n'}}), {'description': 'uno\ndos'})


class TicketplusEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.source = next(s for s in SourceRepository().load() if s.connector == 'ticketplus_chile')

    def test_detail_original_text_and_disciplines_are_extracted_without_extra_http(self):
        url = 'https://ticketplus.cl/events/wachun'
        node = {'@type': 'MusicEvent', 'name': 'Wachun', 'startDate': '2099-01-01', 'description': 'Sólo un resumen'}
        page = '<script type="application/ld+json">' + json.dumps(node) + '</script>' + '''
            <div class="event__detalle"><div class="description-content">
            <p><span>Disciplina: </span><strong>Música</strong><br>Género: Música Infantil</p>
            <p>Concierto musical participativo para familias.</p><style>.css{color:red}</style></div></div>
            <aside><h2>Relacionados</h2><p>Disciplina: Danza</p></aside>'''
        http = Mock()
        http.pipeline_state = None
        http.get_html.side_effect = [f'<a href="{url}">Evento</a>', page]
        source = SourceConfig.from_dict(dict(self.source, url='https://ticketplus.cl/states/test'))
        events = GenericConnector(http).collect(source)
        self.assertEqual(http.get_html.call_count, 2)
        self.assertEqual(len(events), 1)
        self.assertIn('Concierto musical', events[0].source_description)
        self.assertNotIn('Relacionados', events[0].description)
        self.assertNotIn('color:red', events[0].description)
        self.assertNotIn('Danza', events[0].categories)
        self.assertEqual(assess_cultural_relevance(events[0]).decision, 'accepted')

    def test_missing_detail_block_keeps_structured_evidence(self):
        event = Event(title='Día de Spa', description='Sin texto adicional')
        augment_detail([event], '<aside>Concierto de jazz</aside>', self.source)
        self.assertEqual(event.description, 'Sin texto adicional')
        self.assertEqual(assess_cultural_relevance(event).decision, 'excluded')

    def test_multiple_events_never_share_one_description(self):
        events = [Event(title='Uno'), Event(title='Dos')]
        augment_detail(events, '<div class="event__detalle"><div class="description-content">Concierto</div></div>', self.source)
        self.assertTrue(all(not event.source_description for event in events))

    def test_clear_art_disciplines_are_no_longer_quarantined(self):
        for title, category in [('Canto libre', 'Música'), ('Autorretrato y collage', 'Artes visuales'), ('Instalación sonora multiespecie', 'Artes mediales')]:
            event = Event(title=title)
            self.assertEqual(assess_cultural_relevance(event).decision, 'accepted')
            self.assertIn(category, event.categories)

    def test_editorial_description_never_overrides_original_evidence(self):
        event = Event(title='Actividad desconocida', source_description='No se indica la disciplina', description='Concierto de jazz')
        classify_event(event)
        signature = input_signature(event)
        self.assertEqual(event.categories, [])
        event.description = 'Una obra de teatro'
        self.assertEqual(input_signature(event), signature)


if __name__ == '__main__':
    unittest.main()
