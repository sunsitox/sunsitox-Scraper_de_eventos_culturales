import unittest
from unittest.mock import Mock

from tools.reclassify_catalog import apply_plan, build_plan, read_all


class ReclassificationTests(unittest.TestCase):
    def fixture(self):
        source = [{'id':'source','name':'Chile Cultura','is_active':True}]
        events = [{'id':'event','title':'Librería Página 128','start_at':'2025-01-01','end_at':'2030-12-31',
                   'source_id':'source','status':'published','venue':'Librería Página 128','extraction_method':'jsonld'}]
        evidence = [{'event_id':'event','source_description':'Librería permanente'}]
        categories = [{'id':'label','name':'Guía de Librerías'}]
        links = [{'event_id':'event','category_id':'label'}]
        return events,evidence,source,categories,links,set()

    def test_preview_generates_archival_plan_without_writing(self):
        plan, skipped = build_plan(*self.fixture())
        self.assertEqual(skipped,{})
        self.assertEqual(plan[0]['classification']['decision'],'excluded')
        self.assertEqual(plan[0]['classification']['publication_status'],'archived')
        self.assertEqual(plan[0]['previous_categories'],['Guía de Librerías'])

    def test_manual_and_suppressed_rows_are_protected(self):
        args = self.fixture()
        for patch in ({'extraction_method':'manual'},{'is_suppressed':True},{'status':'cancelled'},{'status':'archived'}):
            current = [args[0][0] | patch]
            plan, skipped = build_plan(current,*args[1:])
            self.assertEqual(plan,[])
            self.assertEqual(sum(skipped.values()),1)

    def test_blocked_and_disabled_sources_are_protected(self):
        args = self.fixture()
        args[0][0]['organizer_id']='blocked'
        self.assertEqual(build_plan(*args[:-1],{'blocked'})[0],[])
        args[2][0]['is_active']=False
        self.assertEqual(build_plan(*args)[0],[])

    def test_apply_uses_only_restricted_classification_rpc(self):
        plan,_ = build_plan(*self.fixture())
        client = Mock()
        client.rpc.return_value=[{'updated':1,'skipped':0}]
        self.assertEqual(apply_plan(client,plan),{'updated':1,'skipped':0})
        self.assertEqual(client.rpc.call_args.args[0],'apply_catalog_classifications')
        payload = client.rpc.call_args.args[1]['p_rows'][0]
        self.assertEqual(set(payload),{'id','classification','category_ids'})
        client.upsert.assert_not_called()

    def test_pagination_continues_beyond_first_thousand(self):
        client=Mock()
        client.select.side_effect=[[{'id':str(i)} for i in range(1000)],[{'id':'last'}]]
        self.assertEqual(len(read_all(client,'events','id')),1001)
        self.assertEqual(client.select.call_args.args[1]['offset'],'1000')


if __name__ == '__main__':
    unittest.main()
