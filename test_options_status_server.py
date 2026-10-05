import tempfile
import threading
import unittest
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen
from scout_options.status_server import StatusHandler


class StatusServerTests(unittest.TestCase):
    def test_serves_dashboard_and_status_but_never_ledger_or_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name, value in [('options.html','dashboard'),('options-status.json','{}'),('ledger.sqlite','PRIVATE')]:
                (Path(tmp)/name).write_text(value)
            server=ThreadingHTTPServer(('127.0.0.1',0),partial(StatusHandler,directory=tmp))
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            base='http://127.0.0.1:'+str(server.server_address[1])
            try:
                with urlopen(base+'/') as response:
                    self.assertEqual(response.read(),b'dashboard')
                    self.assertEqual(response.headers['Cache-Control'],'no-store')
                with urlopen(base+'/options-status.json') as response:
                    self.assertEqual(response.read(),b'{}')
                for path in ['/ledger.sqlite','/ledger.sqlite-wal','/../ledger.sqlite','/%2e%2e/ledger.sqlite','/other/']:
                    with self.assertRaises(HTTPError) as caught: urlopen(base+path)
                    self.assertEqual(caught.exception.code,404)
            finally:
                server.shutdown();server.server_close();thread.join()
