"""Catálogos nacionales JSON: fechas estrictas, paginación, caché y protección de cuota."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from eventos.config import SourceRepository, SourceConfig
from eventos.connectors import ConnectorFactory
from eventos.connectors.passline import passline_event, passline_timestamp
from eventos.connectors.puntoticket import puntoticket_dates
from eventos.cultural_filter import assess_cultural_relevance
from eventos.http import HttpClient
from eventos.paths import USER_AGENT
from eventos.state import StateStore


def passline_row(identity='one', **changes):
    return dict(id=identity, nombre='Concierto de jazz', url='https://www.passline.com/eventos/'+identity,
        fecha_inicio='2099-11-01', hora_inicio='19:30:00', fecha_termino='2099-11-01', hora_termino='21:00:00',
        lugar='Sala regional', nombre_communa='Concepción', region='8', category_name='Jazz',
        upper_category_name='Música', productor='123456', precio_min='0.00', **changes)


class LargeJSONTests(unittest.TestCase):
    def setUp(self):
        sources=SourceRepository().load()
        self.passline=next(s for s in sources if s.connector=='passline_json')
        self.punto=next(s for s in sources if s.connector=='puntoticket_json')

    def http(self):
        http=Mock()
        http.pipeline_state=None
        return http

    def test_new_sources_use_json_without_llm_or_embedded_credentials(self):
        for source in (self.passline,self.punto):
            self.assertTrue(source.enabled)
            self.assertFalse(source['use_nvidia'])
            self.assertEqual(source['max_results'],0)
            self.assertTrue(source['api_url'].startswith('https://'))
            self.assertNotIn('api_key',dict(source))

    def test_passline_maps_both_dates_and_published_geography_without_fake_organizer(self):
        event=passline_event(passline_row(),self.passline)
        self.assertEqual(event.start_date,'2099-11-01T19:30:00')
        self.assertEqual(event.end_date,'2099-11-01T21:00:00')
        self.assertEqual(event.region,'Región del Biobío')
        self.assertEqual(event.commune,'Concepción')
        self.assertEqual(event.is_free,'true')
        self.assertEqual(event.organizer,'')
        self.assertEqual(event.address,'')
        self.assertEqual(event.official_url,'')
        self.assertIn('Jazz',event.categories)

    def test_passline_dates_never_fill_missing_year_and_invalid_end_is_not_imported(self):
        self.assertEqual(passline_timestamp('11-01','19:30:00'),'')
        for changes in ({'fecha_inicio':'0000-00-00'},{'fecha_termino':'2099-01-01'},
                        {'url':'https://example.com/event'},{'fecha_termino':'2099-02-30'}):
            self.assertIsNone(passline_event(passline_row() | changes,self.passline))

    def test_passline_unknown_region_is_not_guessed(self):
        event=passline_event(passline_row() | {'region':'0','precio_min':'Consultar'},self.passline)
        self.assertEqual(event.region,'')
        self.assertEqual(event.is_free,'')

    def test_passline_reads_all_offsets_and_deduplicates_overlapping_rows(self):
        source=SourceConfig.from_dict(dict(self.passline,page_size=2))
        http=self.http()
        http.post_json.side_effect=[[passline_row('a'),passline_row('b')],
                                   [passline_row('b'),passline_row('c')],[]]
        connector=ConnectorFactory(http).create(source)
        self.assertEqual(len(connector.collect(source)),3)
        self.assertTrue(connector.collection_complete)
        self.assertEqual([c.kwargs['json_body']['limit'] for c in http.post_json.call_args_list],['0,2','2,2','4,2'])
        self.assertTrue(all(c.kwargs['json_body']['country']=='chile' for c in http.post_json.call_args_list))

    def test_passline_failure_or_repeated_page_keeps_results_but_marks_partial(self):
        source=SourceConfig.from_dict(dict(self.passline,page_size=1))
        for second in ([passline_row()], requests.Timeout('slow'), {'error':'changed'}):
            http=self.http()
            http.post_json.side_effect=[[passline_row()],second]
            connector=ConnectorFactory(http).create(source)
            self.assertEqual(len(connector.collect(source)),1)
            self.assertFalse(connector.collection_complete)

    def test_passline_unexpected_empty_or_changed_payload_cannot_erase_source(self):
        for value in ([],{'error':'changed'}):
            http=self.http()
            http.post_json.return_value=value
            with self.assertRaises(ValueError):
                ConnectorFactory(http).create(self.passline).collect(self.passline)

    def test_provider_sports_and_gym_memberships_are_not_published_to_reach_target(self):
        event=passline_event(passline_row() | {'nombre':'Socios Gimnasio','category_name':'Club','upper_category_name':'Fiestas'},self.passline)
        self.assertEqual(assess_cultural_relevance(event).decision,'excluded')
        event=passline_event(passline_row() | {'nombre':'Desafío del sur','category_name':'Outdoor','upper_category_name':'Deportes'},self.passline)
        self.assertEqual(assess_cultural_relevance(event).decision,'excluded')
        event=passline_event(passline_row() | {'nombre':'Taller de cueca','category_name':'Danza','upper_category_name':'Deportes'},self.passline)
        self.assertEqual(assess_cultural_relevance(event).decision,'accepted')
        event=passline_event(passline_row() | {'nombre':'Illapu 55 años, Gimnasio Regional de Talca','category_name':'Música'},self.passline)
        self.assertEqual(assess_cultural_relevance(event).decision,'accepted')

    def test_punto_reads_explicit_dates_ranges_clocks_and_never_city_lists(self):
        for value,start,end in (
            ('28 de octubre 2099 - 01 de noviembre 2099','2099-10-28','2099-11-01'),
            ('Selecciones 28/10/99 19:30 hrs','2099-10-28T19:30',''),
            ('29-10-2099 al 29-11-2099 19:30 Hrs.','2099-10-29T19:30','2099-11-29'),
            ('14 y 15 de Nov 2099','2099-11-14','2099-11-15'),
        ):
            first,last=puntoticket_dates(value)
            self.assertTrue(first.startswith(start),value)
            self.assertTrue(last.startswith(end) if end else not last,value)
        for value in ('Arica - Antofagasta - Viña del Mar','Temporada 2099','27 DE MARZO','Sábado 14 de noviembre, 21:00 hrs','32/11/2099'):
            self.assertEqual(puntoticket_dates(value),('',''),value)

    def test_punto_cache_reuses_detail_but_catalog_revision_refreshes_it(self):
        row={'title':'Comienzo','date':'29-10-2099 al 29-11-2099','location':'Teatro Zoco - Lo Barnechea','permalink':'/comienzo'}
        node={'@type':'Event','name':'Comienzo en Teatro Zoco','startDate':'2099-11-29T18:00:00',
              'location':{'name':'Teatro Zoco','address':{'addressLocality':'Lo Barnechea','streetAddress':'Dirección publicada'}}}
        detail='<script type="application/ld+json">'+json.dumps(node)+'</script><div class="detail-wrap"><div class="slider-lines"><div class="slider-content">Obra de teatro.</div></div></div>'
        with tempfile.TemporaryDirectory() as directory:
            http=self.http()
            http.pipeline_state=StateStore(Path(directory)/'state.sqlite3')
            http.get_json.return_value=[row]
            http.get_html.side_effect=lambda url,**kw: '<p>Portada</p>' if url==self.punto.url else detail
            first=ConnectorFactory(http).create(self.punto).collect(self.punto)
            second=ConnectorFactory(http).create(self.punto).collect(self.punto)
            detail_calls=[call for call in http.get_html.call_args_list if call.args[0]!=self.punto.url]
            self.assertEqual(len(detail_calls),1)
            self.assertEqual(first[0].start_date,'2099-10-29T00:00:00')
            self.assertEqual(first[0].end_date,'2099-11-29T00:00:00')
            self.assertEqual(second[0].description,'Obra de teatro.')
            self.assertEqual(second[0].address,'Dirección publicada')
            http.get_json.return_value=[row | {'date':'30-10-2099 al 29-11-2099'}]
            ConnectorFactory(http).create(self.punto).collect(self.punto)
            self.assertEqual(len([call for call in http.get_html.call_args_list if call.args[0]!=self.punto.url]),2)

    def test_punto_stops_on_block_instead_of_rotating_identity(self):
        http=self.http()
        http.get_json.return_value=[{'title':'Concierto','date':'01 de noviembre 2099','permalink':'/one'},
                                    {'title':'Otro','date':'02 de noviembre 2099','permalink':'/two'}]
        error=requests.HTTPError('blocked',response=Mock(status_code=403))
        http.get_html.side_effect=['<p>Portada</p>',error]
        connector=ConnectorFactory(http).create(self.punto)
        self.assertEqual(connector.collect(self.punto),[])
        self.assertFalse(connector.collection_complete)
        self.assertEqual(http.get_html.call_count,2)

    def test_punto_missing_permalink_never_becomes_homepage_event(self):
        http=self.http()
        http.get_json.return_value=[{'title':'Concierto','date':'01 de noviembre 2099'}]
        http.get_html.return_value='<p>Portada</p>'
        connector=ConnectorFactory(http).create(self.punto)
        self.assertEqual(connector.collect(self.punto),[])
        self.assertFalse(connector.collection_complete)
        self.assertEqual(http.get_html.call_count,1)


class JsonPostClientTests(unittest.TestCase):
    def test_post_is_json_read_query_counted_and_does_not_use_get_cache(self):
        client=HttpClient(delay=0,cache=Mock())
        client.session=Mock()
        client.session.post.return_value.json.return_value=[{'id':1}]
        self.assertEqual(client.post_json('https://example.cl/search',json_body={'country':'chile'}),[{'id':1}])
        self.assertEqual(client.request_count,1)
        client.cache.load.assert_not_called()
        self.assertEqual(client.session.post.call_args.kwargs['json'],{'country':'chile'})

    def test_post_keeps_domain_pause_after_error_and_user_agent_is_ascii(self):
        client=HttpClient(delay=1,cache=Mock())
        client.session=Mock()
        client.session.post.side_effect=requests.Timeout('slow')
        with patch('eventos.http.time.sleep') as delay,self.assertRaises(requests.Timeout):
            client.post_json('https://example.cl/search',json_body={})
        delay.assert_called_once_with(1)
        self.assertTrue(USER_AGENT.isascii())


if __name__=='__main__': unittest.main()
