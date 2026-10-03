import json
from pathlib import Path
import threading
import unittest
from unittest.mock import patch
import x4d
from test_x4d import Server, AGENT_TOKEN


class GlanceHTTP(Server):
    def setUp(self):
        super().setUp()
        path=Path(self.dir.name)/'snapshot.json'
        path.write_text(json.dumps({'observed_at':100,'weather':{'available':False},
                                   'calendar':{'available':True,'observed_at':100,'events':[]}}))
        # Recreate App with glance opt-in on same test database and bind handler.
        self.app=x4d.App(dict(self.app.cfg,glance_snapshot=str(path)))
        self.httpd.RequestHandlerClass.app=self.app

    def batch(self, seq=1, button='right', press='short', card='glance.home'):
        return {'device':'x4-01','boot':1,'events':[{'seq':seq,'button':button,'press':press,
                'card':card,'etag':'e','wake':'button'}]}

    def test_agent_notes_do_not_displace_glance_home(self):
        self.publish('agent.urgent')
        s,h,b=self.call('GET','/x4/v1/frame')
        self.assertEqual((s,h['X-Card']),(200,'glance.home'))
        self.assertEqual(len(b),48011)
        self.assertEqual(h['X-Session'],'30')
        self.assertIn('confirm_long',h['X-Card-Actions'])

    def test_navigation_retry_is_idempotent_and_back_goes_home(self):
        b=self.batch()
        self.assertEqual(self.call('POST','/x4/v1/events',b)[0],200)
        self.assertEqual(self.call('GET','/x4/v1/frame')[1]['X-Card'],'glance.weather')
        self.call('POST','/x4/v1/events',b)
        self.assertEqual(self.call('GET','/x4/v1/frame')[1]['X-Card'],'glance.weather')
        self.call('POST','/x4/v1/events',self.batch(seq=2,button='back',card='glance.weather'))
        self.assertEqual(self.call('GET','/x4/v1/frame')[1]['X-Card'],'glance.home')

    def test_glance_cli_page_and_status(self):
        s,r=self.agent({'action':'glance','op':'agenda'})
        self.assertEqual(s,200)
        self.assertEqual(r['result']['page'],'agenda')
        self.assertEqual(self.call('GET','/x4/v1/frame')[1]['X-Card'],'glance.agenda')
        status=self.agent({'action':'status'})[1]['result']
        self.assertEqual(status['mode'],'glance')

    def test_short_confirm_is_pending_for_muse_long_is_local_refresh(self):
        self.call('POST','/x4/v1/events',self.batch(button='confirm'))
        event=self.app.store.events(1)[0]
        self.assertEqual((event['label'],event['forward']),('brief','pending'))
        with patch.object(self.app,'refresh_sources',return_value=True) as refresh:
            self.call('POST','/x4/v1/events',self.batch(seq=2,button='confirm',press='long'))
            refresh.assert_called_once()
        self.assertEqual(self.app.store.events(1)[0]['forward'],'local')

    def test_confirm_captures_http_frame_context_before_retry_refresh(self):
        from test_x4_dashboard import fixture
        snapshot=fixture()
        snapshot['calendar']['events']=[{'summary':'HTTP CAPTURED','start':'2099-01-01'}]
        self.app.glance.path.write_text(json.dumps(snapshot))
        _,headers,_=self.call('GET','/x4/v1/frame')
        batch=self.batch(button='confirm')
        batch['events'][0]['etag']=headers['ETag']
        self.assertEqual(self.call('POST','/x4/v1/events',batch)[0],200)
        snapshot['calendar']['events']=[{'summary':'LATER SNAPSHOT','start':'2099-01-02'}]
        self.app.glance.path.write_text(json.dumps(snapshot))
        self.call('POST','/x4/v1/events',batch)
        rows=self.app.store.pending_forwards()
        self.assertEqual(len(rows),1)
        msg=self.app.glance.forward_context(rows[0])
        self.assertIn('HTTP CAPTURED',msg)
        self.assertNotIn('LATER SNAPSHOT',msg)

    def test_glance_preview_requires_auth(self):
        url='/x4/v1/glance.png?page=home'
        self.assertEqual(self.call('GET',url)[0],401)
        s,h,b=self.call('GET',url,token=AGENT_TOKEN)
        self.assertEqual((s,h['Content-Type']),(200,'image/png'))
        self.assertTrue(b.startswith(b'\x89PNG'))

if __name__=='__main__': unittest.main()
