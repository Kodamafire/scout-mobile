import json
import os
import tempfile
import unittest
from unittest.mock import Mock, patch
from datetime import datetime, timedelta, timezone
from pathlib import Path
from scout_options.local_runner import private_save, private_load, shortcut_script, supervise, worker_ready


class LocalSetupTests(unittest.TestCase):
    def test_private_storage_and_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'private'/'keys.json'
            values=dict(ALPACA_API_KEY='abc',ALPACA_SECRET_KEY='xyz')
            private_save(path,values)
            self.assertEqual(path.stat().st_mode & 0o777,0o600)
            self.assertEqual(path.parent.stat().st_mode & 0o777,0o700)
            self.assertEqual(private_load(path),('abc','xyz'))
            path.chmod(0o644)
            with self.assertRaises(ValueError): private_load(path)

    def test_symlinks_are_not_written_or_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/'target';target.write_text('UNCHANGED')
            link=Path(tmp)/'link';link.symlink_to(target)
            with self.assertRaises(ValueError): private_save(link,{})
            with self.assertRaises(OSError): private_load(link)
            self.assertEqual(target.read_text(),'UNCHANGED')

    def test_shortcut_has_no_credentials_and_pins_ubuntu_user_and_root(self):
        script=shortcut_script('Ubuntu','kodamafire',Path('/home/kodamafire/scout-options'),
                               '/home/kodamafire/scout-options/.venv/bin/python')
        self.assertIn('ScoutOptionsResearch.lnk',script)
        self.assertIn('-d Ubuntu -u kodamafire --cd /home/kodamafire/scout-options',script)
        self.assertIn('-m scout_options.local_runner --run',script)
        self.assertNotIn('ALPACA',script)

class SupervisorTests(unittest.TestCase):
    def test_dead_worker_restarts_and_pins_read_only_service(self):
        with tempfile.TemporaryDirectory() as tmp:
            handlers={};loops=[0]
            worker1=Mock(pid=101);worker1.poll.return_value=1
            dashboard=Mock(pid=102);dashboard.poll.return_value=None
            worker2=Mock(pid=103);worker2.poll.return_value=None
            def on_sleep(_):
                loops[0]+=1
                if loops[0]==3: handlers[15](15,None)
            with patch('scout_options.local_runner.DATA',Path(tmp)), patch('scout_options.local_runner.private_load',return_value=('abc','xyz')), patch('scout_options.local_runner.signal.signal',side_effect=lambda n,h:handlers.update({n:h})), patch('scout_options.local_runner.time.monotonic',side_effect=[0,1,12]), patch('scout_options.local_runner.time.sleep',side_effect=on_sleep), patch('scout_options.local_runner.worker_ready',return_value=False), patch('scout_options.local_runner.stop_child') as stop, patch('scout_options.local_runner.subprocess.Popen',side_effect=[worker1,dashboard,worker2]) as spawn:
                supervise()
            self.assertEqual(spawn.call_count,3)
            self.assertIn('scout_options.service',spawn.call_args_list[0].args[0])
            self.assertEqual(spawn.call_args_list[0].kwargs['env']['SCOUT_OPTIONS_FEED'],'indicative')
            self.assertNotIn('abc',spawn.call_args_list[0].args[0])
            self.assertEqual(stop.call_count,2)
            self.assertIsNone(json.loads((Path(tmp)/'supervisor-status.json').read_text())['pid'])

    def test_old_heartbeat_cannot_confirm_new_worker_ready(self):
        with tempfile.TemporaryDirectory() as tmp, patch('scout_options.local_runner.DATA',Path(tmp)):
            now=datetime.now(timezone.utc)
            path=Path(tmp)/'options-status.json'
            path.write_text(json.dumps({'heartbeat':(now-timedelta(seconds=1)).isoformat()}))
            self.assertFalse(worker_ready(now))
            path.write_text(json.dumps({'heartbeat':now.isoformat()}))
            self.assertTrue(worker_ready(now-timedelta(seconds=1)))
