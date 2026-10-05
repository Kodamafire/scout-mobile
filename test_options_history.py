import tempfile
import unittest
from datetime import date,datetime,timedelta,timezone
from pathlib import Path
from types import SimpleNamespace as Obj
from unittest.mock import Mock
from scout_options.history import HistoryStore,collect,six_year_start
from scout_options.replay import evaluate_session,replay

UTC=timezone.utc
DAY=date(2026,10,2)
OPEN=datetime(2026,10,2,13,30,tzinfo=UTC)


def bar(at,price=100,volume=100):
    return Obj(timestamp=at,open=price,high=price+1,low=price-1,close=price+.5,volume=volume)


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/'history.sqlite'
        self.store=HistoryStore(self.path)

    def tearDown(self):
        self.store.close();self.tmp.cleanup()

    def save(self,data):
        self.store.save_session(DAY,OPEN,OPEN+timedelta(minutes=3),data,OPEN+timedelta(hours=8))

    def test_missing_data_and_bad_bars_are_counted_not_filled(self):
        invalid=bar(OPEN+timedelta(minutes=1));invalid.close=float('nan')
        self.save({'SPY':[bar(OPEN),invalid,bar(OPEN)]})
        summary=self.store.summary(DAY,DAY+timedelta(days=1))['symbols']
        self.assertEqual(summary['SPY']['bars_saved'],1)
        self.assertEqual(summary['SPY']['missing_minutes'],2)
        self.assertEqual(summary['SPY']['rejected_bars'],2)
        self.assertEqual(summary['QQQ']['missing_minutes'],3)
        self.assertTrue(self.store.done(DAY))
        self.assertFalse(self.store.done(DAY,retry_incomplete=True))

    def test_reopen_resumes_and_retry_replaces_incomplete_session(self):
        self.save({});self.store.close();self.store=HistoryStore(self.path)
        self.assertTrue(self.store.done(DAY))
        self.save({s:[bar(OPEN+timedelta(minutes=i)) for i in range(3)] for s in ('SPY','QQQ','IWM')})
        self.assertTrue(self.store.done(DAY,True))
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM bars').fetchone()[0],9)

    def test_open_session_cannot_checkpoint_and_transaction_rolls_back(self):
        with self.assertRaises(ValueError):
            self.store.save_session(DAY,OPEN,OPEN+timedelta(hours=6),{},OPEN)
        self.assertFalse(self.store.done(DAY))
        self.store.db.execute("CREATE TRIGGER fail_write BEFORE INSERT ON coverage BEGIN SELECT RAISE(ABORT,'fixture'); END")
        with self.assertRaises(Exception):self.save({'SPY':[bar(OPEN)]})
        self.assertFalse(self.store.done(DAY))
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM bars').fetchone()[0],0)

    def test_free_feed_calendar_early_close_and_resume(self):
        trading=Mock();stocks=Mock()
        trading.get_calendar.return_value=[Obj(date=DAY,open=datetime(2026,10,2,9,30),close=datetime(2026,10,2,13,0))]
        stocks.get_stock_bars.return_value=Obj(data={s:[bar(OPEN)] for s in ('SPY','QQQ','IWM')})
        now=OPEN+timedelta(hours=8)
        self.assertEqual(collect(self.store,stocks,trading,DAY,DAY+timedelta(days=1),now),1)
        request=stocks.get_stock_bars.call_args.args[0]
        self.assertEqual(request.feed.value,'iex');self.assertEqual(request.adjustment.value,'raw')
        self.assertIsNone(request.limit)
        self.assertEqual(self.store.summary(DAY,DAY+timedelta(days=1))['symbols']['SPY']['missing_minutes'],209)
        self.assertEqual(collect(self.store,stocks,trading,DAY,DAY+timedelta(days=1),now),0)
        self.assertEqual(stocks.get_stock_bars.call_count,1)

    def test_network_failure_is_not_checkpointed(self):
        trading=Mock();stocks=Mock()
        trading.get_calendar.return_value=[Obj(date=DAY,open=OPEN,close=OPEN+timedelta(minutes=3))]
        stocks.get_stock_bars.side_effect=RuntimeError('fixture')
        with self.assertRaises(RuntimeError):collect(self.store,stocks,trading,DAY,DAY+timedelta(days=1),OPEN+timedelta(hours=8))
        self.assertFalse(self.store.done(DAY))

    def test_leap_day_start(self):
        self.assertEqual(six_year_start(date(2024,2,29)),date(2018,2,28))


class ReplayTests(unittest.TestCase):
    def bars(self):
        return [(OPEN+timedelta(minutes=i),100+i,100+i+.5,200 if i==20 else 100) for i in range(45)]

    def test_signal_is_known_before_entry_and_future_is_not_used(self):
        rows=self.bars();a=evaluate_session('SPY',rows,15)
        self.assertEqual(len(a),1);self.assertEqual(a[0]['direction'],'CALL')
        self.assertEqual(a[0]['at'],a[0]['entry_at'])
        self.assertAlmostEqual(a[0]['signed_move_bps'],(136/121-1)*10000)
        rows[36]=(rows[36][0],80,80,100)
        b=evaluate_session('SPY',rows,15)
        self.assertEqual(a[0]['direction'],b[0]['direction'])
        self.assertLess(b[0]['signed_move_bps'],0)

    def test_gap_disallows_hypothetical_fill(self):
        rows=self.bars();del rows[25]
        self.assertEqual(evaluate_session('SPY',rows,15),[])

    def test_holdout_is_later_and_report_never_calls_stock_moves_option_profit(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=HistoryStore(Path(tmp)/'history.sqlite')
            try:
                for i in range(3):
                    opened=OPEN+timedelta(days=i);day=opened.date()
                    data={'SPY':[bar(opened+timedelta(minutes=j),100+j,200 if j==20 else 100) for j in range(45)]}
                    store.save_session(day,opened,opened+timedelta(minutes=45),data,opened+timedelta(hours=8))
                result=replay(store.db,DAY,DAY+timedelta(days=3))
                self.assertEqual(result['held_out_start'],'2026-10-04')
                self.assertEqual(result['results']['earlier']['overall']['observations'],2)
                self.assertEqual(result['results']['held_out']['overall']['observations'],1)
                self.assertIn('no option P&L',result['status'])
            finally:store.close()


if __name__=='__main__':unittest.main()
