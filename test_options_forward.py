import fcntl
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace as Obj
from unittest.mock import Mock, patch
from scout_options.forward_test import load_or_freeze, cycle, summarize, rules
from scout_options.history import HistoryStore

UTC=timezone.utc
FREEZE=datetime(2026,10,5,21,tzinfo=UTC)
OPEN=datetime(2026,10,6,13,30,tzinfo=UTC)


def bars(opened=OPEN):
    return [Obj(timestamp=opened+timedelta(minutes=i),open=100+i,close=100+i+.5,
                high=101+i,low=99+i,volume=200 if i==20 else 100) for i in range(45)]


class ForwardTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.stocks=Mock();self.trading=Mock()
        self.trading.get_calendar.return_value=[Obj(date=OPEN.date(),open=OPEN,close=OPEN+timedelta(minutes=45))]
        self.stocks.get_stock_bars.return_value=Obj(data={'SPY':bars()})

    def tearDown(self):self.tmp.cleanup()

    def test_freeze_starts_next_exchange_local_date_and_restart_keeps_date(self):
        plan=load_or_freeze(self.root,FREEZE)
        self.assertEqual(plan['start_session'],'2026-10-06')
        self.assertEqual(load_or_freeze(self.root,FREEZE+timedelta(days=5)),plan)
        late_utc=FREEZE.replace(hour=1)+timedelta(days=1)
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(load_or_freeze(folder,late_utc)['start_session'],'2026-10-06')

    def test_changed_rules_and_invalid_start_refuse_reuse(self):
        plan=load_or_freeze(self.root,FREEZE)
        changed=dict(rules(),version=99)
        with patch('scout_options.forward_test.rules',return_value=changed):
            with self.assertRaises(ValueError):load_or_freeze(self.root,FREEZE)
        plan['start_session']='2026-10-05'
        (self.root/'forward-plan.json').write_text(json.dumps(plan))
        with self.assertRaises(ValueError):load_or_freeze(self.root,FREEZE)

    def test_before_first_session_no_network_or_fake_results(self):
        result=cycle(self.root,self.stocks,self.trading,FREEZE)
        self.stocks.get_stock_bars.assert_not_called();self.trading.get_calendar.assert_not_called()
        self.assertEqual(result['sessions_available'],0)
        self.assertIsNone(result['candidate_bot_bps'])

    def test_open_session_is_not_downloaded_or_checkpointed(self):
        load_or_freeze(self.root,FREEZE)
        result=cycle(self.root,self.stocks,self.trading,OPEN+timedelta(minutes=5))
        self.stocks.get_stock_bars.assert_not_called()
        self.assertEqual(result['sessions_available'],0)
        with HistoryStoreContext(self.root/'forward-history.sqlite') as store:
            self.assertFalse(store.done(OPEN.date()))

    def test_one_completed_future_day_is_reported_and_restart_resumes(self):
        load_or_freeze(self.root,FREEZE)
        now=OPEN+timedelta(hours=8)
        first=cycle(self.root,self.stocks,self.trading,now)
        self.assertEqual(first['sessions_available'],1)
        self.assertEqual(first['candidate_windows'],1)
        self.assertAlmostEqual(first['candidate_bot_bps'],(126/121-1)*10000)
        self.assertAlmostEqual(first['opening_15_bps'],(136/121-1)*10000)
        request=self.trading.get_calendar.call_args.args[0]
        self.assertEqual(request.start,OPEN.date())
        second=cycle(self.root,self.stocks,self.trading,now+timedelta(minutes=5))
        self.assertEqual(second['candidate_windows'],1)
        self.assertEqual(self.stocks.get_stock_bars.call_count,1)
        result=json.loads((self.root/'forward-results.json').read_text())
        self.assertEqual(len(result['candidate']['daily']),1)
        self.assertGreater(result['coverage']['symbols']['SPY']['missing_minutes'], -1)

    def test_old_history_is_excluded_even_if_present(self):
        plan=load_or_freeze(self.root,FREEZE)
        with HistoryStoreContext(self.root/'forward-history.sqlite') as store:
            opened=OPEN-timedelta(days=1)
            store.save_session(opened.date(),opened,opened+timedelta(minutes=45),
                               {'SPY':bars(opened)},opened+timedelta(hours=8))
            result=summarize(store.db,plan,OPEN.date()+timedelta(days=1))
            self.assertEqual(result['sessions_available'],0)
            self.assertEqual(result['candidate']['bot']['observations'],0)

    def test_failed_download_has_no_checkpoint_or_fabricated_day(self):
        load_or_freeze(self.root,FREEZE)
        self.stocks.get_stock_bars.side_effect=RuntimeError('network fixture')
        with self.assertRaises(RuntimeError):cycle(self.root,self.stocks,self.trading,OPEN+timedelta(hours=8))
        with HistoryStoreContext(self.root/'forward-history.sqlite') as store:
            self.assertFalse(store.done(OPEN.date()))
        self.assertFalse((self.root/'forward-results.json').exists())

    def test_missing_future_window_cannot_generate_candidate(self):
        load_or_freeze(self.root,FREEZE)
        data=bars();del data[30]
        self.stocks.get_stock_bars.return_value=Obj(data={'SPY':data})
        result=cycle(self.root,self.stocks,self.trading,OPEN+timedelta(hours=8))
        self.assertEqual(result['sessions_available'],1)
        self.assertEqual(result['candidate_windows'],0)
        self.assertIsNone(result['candidate_bot_bps'])

    def test_second_writer_cannot_touch_plan_or_database(self):
        with (self.root/'forward-study.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):cycle(self.root,self.stocks,self.trading,FREEZE)
        self.assertFalse((self.root/'forward-plan.json').exists())

    def test_edited_freeze_date_cannot_mix_existing_dataset(self):
        cycle(self.root,self.stocks,self.trading,FREEZE)
        path=self.root/'forward-plan.json';plan=json.loads(path.read_text())
        plan['start_session']='2026-10-07';path.write_text(json.dumps(plan))
        with self.assertRaises(ValueError):cycle(self.root,self.stocks,self.trading,FREEZE)


class HistoryStoreContext:
    def __init__(self,path):self.store=HistoryStore(path)
    def __enter__(self):return self.store
    def __exit__(self,*args):self.store.close()


if __name__=='__main__':unittest.main()
