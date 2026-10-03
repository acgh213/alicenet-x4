import json
import tempfile
import unittest
from pathlib import Path
import x4_glance
from x4_store import Store


class Glance(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'snapshot.json'
        self.path.write_text(json.dumps({'observed_at':100, 'weather':{'available':True,'observed_at':90},
                            'calendar':{'available':True,'events':[{'summary':str(i)} for i in range(17)]}}))
        self.store=Store(str(Path(self.tmp.name)/'db'))
        self.glance=x4_glance.Glance(self.store,str(self.path))

    def test_defaults_to_home_not_any_agent_cards(self):
        self.assertEqual(self.glance.state('x4-01'), {'page':'home','offset':0})

    def test_navigation_persists_and_is_per_device(self):
        self.glance.navigate('x4-01','right','short')
        self.assertEqual(self.glance.state('x4-01')['page'],'weather')
        self.assertEqual(x4_glance.Glance(self.store,str(self.path)).state('x4-01')['page'],'weather')
        self.assertEqual(self.glance.state('other')['page'],'home')
        self.glance.navigate('x4-01','right','short')
        self.glance.navigate('x4-01','down','short')
        self.assertEqual(self.glance.state('x4-01'),{'page':'agenda','offset':5})
        self.glance.navigate('x4-01','back','short')
        self.assertEqual(self.glance.state('x4-01'),{'page':'home','offset':0})

    def test_agenda_stops_at_last_five_item_page_not_last_single_event(self):
        self.glance.select('x4-01', 'agenda')
        for _ in range(10):
            self.glance.navigate('x4-01', 'down', 'short')
        self.assertEqual(self.glance.state('x4-01')['offset'], 15)
        self.glance.navigate('x4-01', 'up', 'short')
        self.assertEqual(self.glance.state('x4-01')['offset'], 10)

    def test_action_labels_are_only_muse_brief_not_navigation(self):
        self.assertEqual(self.glance.action_labels()['glance.home'],{'confirm':'brief'})

    def test_missing_or_bad_snapshot_is_unavailable_not_empty_calendar(self):
        self.path.write_text('bad')
        s=self.glance.snapshot()
        self.assertFalse(s['calendar']['available'])
        self.assertFalse(s['weather']['available'])

    def test_labels_for_virtual_home_event_saved_for_relay(self):
        batch={'device':'x4-01','boot':1,'events':[{'seq':1,'card':'glance.home','etag':'e',
               'button':'confirm','press':'short','wake':'button'}]}
        self.store.record_events(batch,now=200,action_labels=self.glance.action_labels())
        e=self.store.events(1)[0]
        self.assertEqual(e['label'],'brief')
        self.assertEqual(e['forward'],'pending')
        self.assertEqual(len(self.store.record_events(batch,now=201,action_labels=self.glance.action_labels())),0)

if __name__=='__main__': unittest.main()
