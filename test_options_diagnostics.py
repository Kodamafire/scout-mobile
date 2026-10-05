import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from scout_options.diagnostics import running_credentials, check


class DiagnosticsTests(unittest.TestCase):
    def test_only_service_environment_is_used_and_ambiguity_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            def process(pid,module):
                p=root/str(pid);p.mkdir()
                (p/'cmdline').write_bytes(b'python\0-m\0'+module+b'\0')
                (p/'environ').write_bytes(b'ALPACA_API_KEY=abc\0ALPACA_SECRET_KEY=xyz\0OTHER=private\0')
            process(1,b'other');process(2,b'scout_options.service')
            self.assertEqual(running_credentials(tmp),('abc','xyz'))
            process(3,b'scout_options.service')
            with self.assertRaises(RuntimeError): running_credentials(tmp)

    def test_failure_never_prints_remote_error_or_account_data(self):
        class Failure(Exception): status_code=401
        client=Mock();client.get_account.side_effect=Failure('PRIVATEKEY PRIVATEACCOUNT')
        output=io.StringIO()
        with contextlib.redirect_stdout(output): self.assertFalse(check(client))
        self.assertIn('HTTP 401',output.getvalue())
        self.assertNotIn('PRIVATE',output.getvalue())
        self.assertIn('Exchange clock: OK',output.getvalue())

    def test_status_must_be_numeric_and_no_remote_objects_print(self):
        class Failure(Exception): status_code='PRIVATESECRET'
        client=Mock();client.get_account.side_effect=Failure()
        output=io.StringIO()
        with contextlib.redirect_stdout(output): check(client)
        self.assertNotIn('PRIVATESECRET',output.getvalue())
