import contextlib
import io
import unittest
from unittest.mock import Mock, patch
from scout_options.credentials import load_credentials, credential_status, main
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

    def test_shutdown_unstarted_and_already_closed_stream(self):
        stream=Mock()
        stop_stream(stream,None)
        stream.stop.assert_not_called()
        stream.stop.side_effect=AttributeError('loop not initialized')
        thread=Mock()
        stop_stream(stream,thread)
        thread.join.assert_called_once_with(timeout=3)
