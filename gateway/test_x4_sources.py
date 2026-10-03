import datetime as dt
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
sys.path.insert(0, '/home/cassie/services/clock-control')
import x4_sources

NOW = dt.datetime(2026, 10, 3, 20, tzinfo=dt.timezone.utc).timestamp()
CONFIG = {'owner':'shared', 'timeout_s':5, 'ha': {'origin':'http://ha.test:8123', 'token_env':'TEST_HA',
          'weather_entity':'weather.forecast_home','calendar_entity':'calendar.allowed'},
          'sources':[{'id':'weather','kind':'weather','stale_after_s':1800},
          {'id':'upcoming','kind':'upcoming','calendar':True,'display_consent':True,
          'fields':['summary','start'],'lookahead_s':604800,'stale_after_s':1800}]}

class Sources(unittest.TestCase):
    def call(self, responses, config=None):
        self.requests=[]
        def get(url, headers, timeout, cap):
            self.requests.append(url)
            key='calendar' if '/calendars/' in url else 'weather'
            value=responses[key]
            if isinstance(value, Exception): raise value
            return value
        with patch.dict(os.environ, {'TEST_HA':'private-token'}):
            return x4_sources.collect(config or CONFIG, now=NOW, http_get=get)

    def test_raw_weather_and_all_calendar_events_not_card_strings(self):
        s=self.call({'weather':{'state':'cloudy','last_updated':'2026-10-03T19:55:00Z','attributes':
                     {'temperature':68,'temperature_unit':'°F','humidity':75,'wind_speed':7,
                      'wind_speed_unit':'mph','secret':'never'}},
                     'calendar':[{'summary':'Dentist','start':{'dateTime':'2026-10-04T10:00:00-04:00'},
                                  'end':{'dateTime':'2026-10-04T11:00:00-04:00'},
                                  'description':'private description','location':'private location'},
                                 {'summary':'Day off','start':{'date':'2026-10-05'},'end':{'date':'2026-10-06'}}]})
        self.assertEqual(s['weather']['temperature'],68)
        self.assertEqual(s['weather']['observed_at'],NOW-300)
        self.assertEqual(s['weather']['checked_at'],NOW)
        self.assertEqual(len(s['calendar']['events']),2)
        self.assertEqual(s['calendar']['events'][0]['start'],'2026-10-04T10:00:00-04:00')
        self.assertNotIn('private',json.dumps(s))
        self.assertNotIn('secret',json.dumps(s))
        self.assertIn('start=',self.requests[1]); self.assertIn('end=',self.requests[1])
        self.assertEqual(s['observed_at'], NOW)

    def test_valid_empty_calendar_is_not_unavailable(self):
        s=self.call({'weather':{'state':'unknown'},'calendar':[]})
        self.assertFalse(s['weather']['available'])
        self.assertTrue(s['calendar']['available'])
        self.assertEqual(s['calendar']['events'],[])

    def test_source_failure_preserves_old_observation_not_freshened(self):
        previous={'observed_at':NOW-4000,'weather':{'available':True,'observed_at':NOW-4000,
                  'temperature':10,'stale_after_s':1800},'calendar':{'available':True,
                  'observed_at':NOW-100,'events':[]}}
        with patch.dict(os.environ, {'TEST_HA':'secret'}):
            s=x4_sources.collect(CONFIG, now=NOW, previous=previous,
                                 http_get=lambda *a: (_ for _ in ()).throw(RuntimeError('token secret')))
        self.assertEqual(s['weather']['observed_at'],NOW-4000)
        self.assertEqual(s['weather']['temperature'],10)
        self.assertTrue(s['weather']['refresh_failed'])
        self.assertNotIn('secret',json.dumps(s))

    def test_disabled_or_unconsented_calendar_never_fetched(self):
        c=json.loads(json.dumps(CONFIG)); c['sources'][1]['enabled']=False
        s=self.call({'weather':{'state':'sunny','attributes':{}},'calendar':[]},c)
        self.assertEqual(len(self.requests),1)
        self.assertFalse(s['calendar']['available'])
        c['sources'][1]['enabled']=True; c['sources'][1]['display_consent']=False
        with self.assertRaises(ValueError): self.call({},c)

    def test_future_weather_observation_is_rejected(self):
        s=self.call({'weather':{'state':'sunny','last_updated':'2099-01-01T00:00:00Z'},'calendar':[]})
        self.assertFalse(s['weather']['available'])

    def test_atomic_private_snapshot_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'snapshot.json'
            x4_sources.write_snapshot(p, {'observed_at':NOW,'calendar':{'events':[]}})
            self.assertEqual(json.loads(p.read_text())['observed_at'],NOW)
            self.assertEqual(p.stat().st_mode & 0o777,0o600)

if __name__=='__main__': unittest.main()
