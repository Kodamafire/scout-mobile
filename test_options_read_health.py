import json
import unittest
from datetime import datetime,timedelta,timezone
from scout_options.read_health import ReadHealth
from scout_options.alpaca_observer import Observer

NOW=datetime(2026,10,5,22,tzinfo=timezone.utc)


class ReadHealthTests(unittest.TestCase):
    def test_unchecked_is_not_healthy_and_success_expires(self):
        reads=ReadHealth()
        self.assertFalse(reads.snapshot(NOW)['account']['fresh'])
        reads.success('account',NOW,'Market closed')
        self.assertTrue(reads.snapshot(NOW+timedelta(seconds=15))['account']['fresh'])
        self.assertFalse(reads.snapshot(NOW+timedelta(seconds=16))['account']['fresh'])
        self.assertFalse(reads.snapshot(NOW-timedelta(seconds=1))['account']['fresh'])

    def test_failed_latest_read_is_unavailable_even_after_recent_success(self):
        reads=ReadHealth();reads.success('account',NOW)
        exc=RuntimeError('SECRET BODY');exc.status_code=401
        reads.failure('account',exc,NOW+timedelta(seconds=1))
        state=reads.snapshot(NOW+timedelta(seconds=2))['account']
        self.assertFalse(state['fresh']);self.assertEqual(state['last_success'],NOW.isoformat())
        self.assertEqual(state['http'],401)
        self.assertNotIn('SECRET',json.dumps(state))

    def test_recovery_clears_current_problem(self):
        reads=ReadHealth();reads.failure('account',RuntimeError('old'),NOW)
        reads.success('account',NOW+timedelta(seconds=5),'Market open')
        state=reads.snapshot(NOW+timedelta(seconds=6))['account']
        self.assertTrue(state['fresh']);self.assertIsNone(state['error']);self.assertIsNone(state['http'])

    def observer(self):
        observer=Observer.__new__(Observer);observer.reads=ReadHealth()
        observer.context=(False,NOW,None)
        return observer

    def test_current_closed_market_is_waiting_not_scanner_failure(self):
        observer=self.observer();observer.reads.success('account',NOW,'Market closed')
        state=observer.current_health(NOW)
        self.assertEqual(state['market'],'Market closed')
        self.assertEqual(state['scanner']['current_state'],'Waiting for market open')
        self.assertEqual(observer.current_health(NOW+timedelta(seconds=16))['market'],'Market state unverified')

    def test_open_market_scan_failure_is_visible_and_stream_health_independent(self):
        observer=self.observer();observer.context=(True,NOW,NOW)
        observer.reads.success('account',NOW,'Market open')
        observer.reads.failure('scanner',ValueError('body'),NOW)
        observer.reads.success('stream',NOW,'Quote update received')
        state=observer.current_health(NOW)
        self.assertEqual(state['scanner']['current_state'],'Unavailable')
        self.assertTrue(state['stream']['fresh'])
        observer.reads.failure('stream',RuntimeError('body'),NOW)
        self.assertFalse(observer.current_health(NOW)['stream']['fresh'])


if __name__=='__main__':unittest.main()
