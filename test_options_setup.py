import contextlib
import io
import unittest
from unittest.mock import Mock, patch
from scout_options.credentials import load_credentials, credential_status, main, connect
from scout_options.alpaca_observer import stop_stream


class SetupTests(unittest.TestCase):
    def test_formats_and_safe_diagnostics(self):
        cases=[('', 'EMPTY'),(' secret','CONTAINS WHITESPACE'),('abc\r','CONTAINS WHITESPACE'),
               ('abc\x1b[200~','CONTAINS UNEXPECTED CHARACTERS'),('abcé','CONTAINS UNEXPECTED CHARACTERS'),
               ('abc123','FORMAT CHECK PASSED')]
        for value, expected in cases:
            self.assertEqual(credential_status(value),expected)
        with patch.dict('os.environ',{'ALPACA_API_KEY':' PRIVATEKEY','ALPACA_SECRET_KEY':'PRIVATESECRET'},clear=True):
            output=io.StringIO()
            with contextlib.redirect_stdout(output): main()
            self.assertNotIn('PRIVATEKEY',output.getvalue())
            self.assertNotIn('PRIVATESECRET',output.getvalue())
            with self.assertRaises(ValueError) as exc: load_credentials()
            self.assertNotIn('PRIVATEKEY',str(exc.exception))
            self.assertNotIn('PRIVATESECRET',str(exc.exception))
        self.assertEqual(load_credentials({'ALPACA_API_KEY':'abc','ALPACA_SECRET_KEY':'xyz'}),('abc','xyz'))

    def test_private_connect_normalizes_paste_and_forces_read_only_feed(self):
        with patch('getpass.getpass',side_effect=['\x1b[200~abc123\x1b[201~',' xyz789\r']), patch('os.execve') as execute, contextlib.redirect_stdout(io.StringIO()) as output:
            connect()
        executable, command, environ = execute.call_args.args
        self.assertEqual(environ['ALPACA_API_KEY'],'abc123')
        self.assertEqual(environ['ALPACA_SECRET_KEY'],'xyz789')
        self.assertEqual(environ['SCOUT_OPTIONS_FEED'],'indicative')
        self.assertEqual(command[2],'scout_options.service')
        self.assertNotIn('abc123',output.getvalue())
        self.assertNotIn('xyz789',output.getvalue())

    def test_invalid_input_reprompts_without_leaking_value(self):
        with patch('getpass.getpass',side_effect=['BAD!VALUE','abc','xyz']), patch('os.execve') as execute, contextlib.redirect_stdout(io.StringIO()) as output:
            connect()
        self.assertNotIn('BAD!VALUE',output.getvalue())
        self.assertEqual(execute.call_args.args[2]['ALPACA_API_KEY'],'abc')

    def test_shutdown_unstarted_and_already_closed_stream(self):
        stream=Mock()
        stop_stream(stream,None)
        stream.stop.assert_not_called()
        stream.stop.side_effect=AttributeError('loop not initialized')
        thread=Mock()
        stop_stream(stream,thread)
        thread.join.assert_called_once_with(timeout=3)
