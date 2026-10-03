import unittest
import x4_protocol
import x4ctl

class GlanceProtocol(unittest.TestCase):
    def test_glance_requests_bounded(self):
        for op in ('status','refresh','home','weather','agenda'):
            self.assertEqual(x4_protocol.validate({'action':'glance','op':op}),
                             {'action':'glance','op':op,'device':'x4-01'})
        for req in ({'action':'glance','op':'bad'}, {'action':'glance','op':'home','device':'BAD'},
                    {'action':'glance','op':'home','url':'http://evil'}):
            with self.assertRaises(ValueError): x4_protocol.validate(req)

    def test_cli_convenience_glance_commands(self):
        for cmd,op in [('home','home'),('weather','weather'),('agenda','agenda'),('refresh','refresh'),('glance','status')]:
            args=x4ctl._parser().parse_args([cmd])
            self.assertEqual(x4ctl._request(args),{'action':'glance','op':op,'device':'x4-01'})

if __name__=='__main__': unittest.main()
