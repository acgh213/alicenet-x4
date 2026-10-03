import unittest
from unittest.mock import patch
import subprocess
import x4_forward


class Forward(unittest.TestCase):
    def test_message_is_stdin_not_remote_shell(self):
        text = "Cassie pressed confirm; $(touch /bad) 'quoted'\nnext"
        with patch('x4_forward.subprocess.run', return_value=subprocess.CompletedProcess([], 0, b'ok', b'')) as run:
            self.assertEqual(x4_forward.send(text), 0)
        args, kw = run.call_args
        self.assertEqual(args[0][-2:], ['cassie@192.168.18.53', '/usr/local/bin/musegadget send-user-msg -'])
        self.assertNotIn(text, args[0])
        self.assertEqual(kw['input'], text.encode())
        self.assertLessEqual(kw['timeout'], 25)
        self.assertIn('BatchMode=yes', args[0])

    def test_transport_failure_is_not_success(self):
        with patch('x4_forward.subprocess.run', return_value=subprocess.CompletedProcess([], 1, b'', b'private')):
            self.assertEqual(x4_forward.send('x'), 1)

    def test_empty_or_oversized_messages_are_rejected(self):
        for text in ('', 'x' * 8193):
            with self.subTest(text=text[:3]), patch('x4_forward.subprocess.run') as run:
                self.assertEqual(x4_forward.send(text), 1)
                run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
