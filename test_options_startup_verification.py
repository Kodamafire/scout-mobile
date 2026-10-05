import unittest
from datetime import datetime, timedelta, timezone
from scout_options.local_runner import verified_status, shortcut_script

NOW=datetime.now(timezone.utc)


class StartupVerificationTests(unittest.TestCase):
    def state(self,**changes):
        return dict(dict(pid=123,at=NOW.isoformat(),worker_ready=True,source='windows_signin',boot_id='boot1',restarts=0),**changes)

    def test_only_current_fresh_ready_signin_launch_is_verified(self):
        self.assertTrue(verified_status(self.state(),NOW,'boot1')['startup_verified'])
        for changes in [dict(source='manual'),dict(source='setup'),dict(source=None),dict(worker_ready=False),
                        dict(at=(NOW-timedelta(seconds=6)).isoformat()),dict(pid=None),dict(boot_id='old')]:
            state=self.state();state.update(changes)
            self.assertFalse(verified_status(state,NOW,'boot1')['startup_verified'])

    def test_stale_or_future_state_is_not_reported_ready(self):
        for at in [NOW-timedelta(seconds=6),NOW+timedelta(seconds=1)]:
            state=self.state();state['at']=at.isoformat()
            result=verified_status(state,NOW,'boot1')
            self.assertFalse(result['supervisor_fresh']);self.assertFalse(result['worker_ready'])

    def test_invalid_state_fails_closed(self):
        self.assertFalse(verified_status({},NOW,'boot1')['startup_verified'])
        state=self.state();state['at']='not-a-date'
        self.assertFalse(verified_status(state,NOW,'boot1')['startup_verified'])

    def test_shortcut_marks_signin_origin_without_credentials(self):
        text=shortcut_script('Ubuntu','kodamafire','/home/kodamafire/scout-options','/home/kodamafire/scout-options/.venv/bin/python')
        self.assertIn('--source windows_signin',text)
        self.assertNotIn('ALPACA',text)
