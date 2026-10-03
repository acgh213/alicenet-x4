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

    def test_brief_matches_displayed_page_and_survives_snapshot_refresh(self):
        from test_x4_dashboard import fixture, NOW, TZ
        snap=fixture()
        snap['calendar']['events']=[{'summary':f'Shown {i:02}', 'start':f'2026-10-{4+i:02}'}
                                    for i in reversed(range(12))]
        snap['calendar']['events'].append({'summary':'ENDED','start':'2026-10-02','end':'2026-10-03'})
        self.path.write_text(json.dumps(snap))
        self.glance.select('x4-01','agenda')
        self.glance.navigate('x4-01','down','short')
        frame=self.glance.frame('x4-01',NOW,TZ,120)
        batch={'device':'x4-01','boot':1,'events':[{'seq':1,'card':frame['card'],'etag':frame['etag'],
               'button':'confirm','press':'short','wake':'button'}]}
        contexts=self.glance.event_context(batch)
        self.store.record_events(batch,now=NOW,action_labels=self.glance.action_labels(),contexts=contexts)
        # Changes after event acceptance cannot rewrite queued brief contents.
        snap['calendar']['events']=[{'summary':'NEW SNAPSHOT','start':'2026-10-04'}]
        self.path.write_text(json.dumps(snap))
        restarted=x4_glance.Glance(self.store,str(self.path))
        message=restarted.forward_context(self.store.pending_forwards()[0])
        for i in range(5,10): self.assertIn(f'Shown {i:02}',message)
        for text in ('Shown 00','Shown 04','Shown 10','ENDED','NEW SNAPSHOT'):
            self.assertNotIn(text,message)

    def test_queued_brief_obeys_current_calendar_consent(self):
        from test_x4_dashboard import fixture, NOW, TZ
        snap=fixture()
        snap['calendar'].update(source_key='same-source',approved_fields=['summary','start'])
        snap['calendar']['events']=[{'summary':'REVOKED','start':'2026-10-04',
                                    'description':'PRIVATE_DESCRIPTION','location':'PRIVATE_LOCATION'}]
        self.path.write_text(json.dumps(snap))
        frame=self.glance.frame('x4-01',NOW,TZ,120)
        batch={'device':'x4-01','boot':1,'events':[{'seq':1,'card':frame['card'],'etag':frame['etag'],
               'button':'confirm','press':'short','wake':'button'}]}
        self.store.record_events(batch,now=NOW,action_labels=self.glance.action_labels(),
                                 contexts=self.glance.event_context(batch))
        ev=self.store.pending_forwards()[0]
        self.assertNotIn('PRIVATE_',self.glance.forward_context(ev))
        snap['calendar']['approved_fields']=['start']
        self.path.write_text(json.dumps(snap))
        message=self.glance.forward_context(ev)
        self.assertNotIn('REVOKED',message)
        self.assertIn('2026-10-04',message)
        snap['calendar']['source_key']='new-source'
        self.path.write_text(json.dumps(snap))
        self.assertNotIn('2026-10-04',self.glance.forward_context(ev))

    def test_unknown_display_fingerprint_does_not_brief_latest_snapshot(self):
        ev={'device':'x4-01','card':'glance.agenda','etag':'unknown'}
        msg=self.glance.forward_context(ev)
        self.assertIn('displayed context unavailable',msg)
        self.assertNotIn('"summary"',msg)

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

    def test_agenda_navigation_uses_same_unexpired_items_as_renderer(self):
        from test_x4_dashboard import fixture, NOW, TZ
        snap=fixture()
        snap['calendar']['events']=([{'summary':'Ended','start':'2026-10-01','end':'2026-10-02'}]*10+
                                    [{'summary':f'Upcoming {i}','start':'2026-10-04'} for i in range(6)])
        self.path.write_text(json.dumps(snap))
        self.glance.select('x4-01','agenda')
        for _ in range(3): self.glance.navigate('x4-01','down','short',now=NOW,tz=TZ)
        self.assertEqual(self.glance.state('x4-01')['offset'],5)

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
