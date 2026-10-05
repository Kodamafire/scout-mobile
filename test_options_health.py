import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from scout_options.health import healthy


class HealthTests(unittest.TestCase):
    def test_fresh_stale_future_naive_and_missing_heartbeat(self):
        now=datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'status.json'
            self.assertFalse(healthy(path,now))
            for at, expected in [(now,True),(now-timedelta(seconds=11),False),
                                  (now+timedelta(seconds=1),False),(now.replace(tzinfo=None),False)]:
                path.write_text(json.dumps({'heartbeat':at.isoformat()}))
                self.assertEqual(healthy(path,now),expected)
            path.write_text('{bad'); self.assertFalse(healthy(path,now))
            path.write_text('{}'); self.assertFalse(healthy(path,now))
