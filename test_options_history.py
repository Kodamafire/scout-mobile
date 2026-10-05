import contextlib
import io
import json
import tempfile
import unittest
from datetime import date,datetime,timedelta,timezone
from pathlib import Path
from types import SimpleNamespace as Obj
from unittest.mock import Mock,patch
from scout_options.history import HistoryStore,collect,six_year_start,main
from scout_options.replay import evaluate_session,evaluate_session_horizons,replay,comparison,nonoverlapping,time_bucket,summary_text

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

    def test_cli_failed_download_returns_failure_without_remote_payload(self):
        output=io.StringIO()
        with patch('sys.argv',['history','--collect','--database',str(self.path)]), patch('scout_options.local_runner.private_load',side_effect=RuntimeError('SECRET REMOTE BODY')), contextlib.redirect_stdout(output):
            with self.assertRaises(SystemExit) as exc:main()
        self.assertEqual(exc.exception.code,1)
        self.assertNotIn('SECRET REMOTE BODY',output.getvalue())

    def test_leap_day_start(self):
        self.assertEqual(six_year_start(date(2024,2,29)),date(2018,2,28))

    def test_cli_report_saves_coverage_and_replay_without_touching_bars(self):
        self.save({'SPY':[bar(OPEN)]})
        report=Path(self.tmp.name)/'reports'/'comparison.json'
        output=io.StringIO()
        with patch('sys.argv',['history','--replay','--start',DAY.isoformat(),
                               '--end',(DAY+timedelta(days=1)).isoformat(),
                               '--database',str(self.path),'--report',str(report)]), contextlib.redirect_stdout(output):
            main()
        saved=json.loads(report.read_text())
        self.assertEqual(saved['replay']['sessions_available'],1)
        self.assertEqual(saved['coverage']['symbols']['SPY']['bars_saved'],1)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM bars').fetchone()[0],1)
        self.assertIn('Comparison report saved:',output.getvalue())

    def test_report_cannot_replace_database(self):
        with patch('sys.argv',['history','--database',str(self.path),'--report',str(self.path)]), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as exc:main()
        self.assertEqual(exc.exception.code,2)

    def test_cli_compares_exits_on_saved_data_without_credentials(self):
        for i in range(2):
            opened=OPEN+timedelta(days=i)
            self.store.save_session(opened.date(),opened,opened+timedelta(minutes=45),
                {'SPY':[bar(opened+timedelta(minutes=j),100+j,200 if j==20 else 100) for j in range(45)]},
                opened+timedelta(hours=8))
        report=Path(self.tmp.name)/'exits.json'
        output=io.StringIO()
        with patch('sys.argv',['history','--compare-exits','--start',DAY.isoformat(),
                               '--end',(DAY+timedelta(days=2)).isoformat(),
                               '--database',str(self.path),'--report',str(report)]), \
             patch('scout_options.local_runner.private_load',side_effect=AssertionError('Must not read credentials')), \
             contextlib.redirect_stdout(output):
            main()
        result=json.loads(report.read_text())['replay']
        self.assertEqual(result['selection_spacing_minutes'],15)
        self.assertEqual(list(result['horizons']),['1','5','15'])
        self.assertIn('First hour only:',output.getvalue())


class ReplayTests(unittest.TestCase):
    def sample(self,direction='PUT',move=10,entry=OPEN,underlying='SPY'):
        return dict(direction=direction,stock_move_bps=move,
                    signed_move_bps=move if direction=='CALL' else -move,
                    session=entry.date().isoformat(),underlying=underlying,
                    entry_at=entry.isoformat(),exit_at=(entry+timedelta(minutes=15)).isoformat())

    def test_baselines_use_same_windows_and_down_is_not_option_profit(self):
        stats=comparison([self.sample('CALL',20),self.sample('PUT',10)])
        self.assertEqual(stats['bot']['average_signed_stock_move_bps'],5)
        self.assertEqual(stats['always_up']['average_signed_stock_move_bps'],15)
        self.assertEqual(stats['always_down']['average_signed_stock_move_bps'],-15)
        self.assertEqual(stats['equal_weight_session_means']['bot_minus_always_up_bps'],-10)
        self.assertIsNone(comparison([])['bot']['average_signed_stock_move_bps'])

    def test_nonoverlap_keeps_first_and_allows_exit_time_and_other_symbols(self):
        rows=[self.sample(entry=OPEN+timedelta(minutes=15)),
              self.sample(entry=OPEN+timedelta(minutes=1)),self.sample(),
              self.sample(entry=OPEN+timedelta(minutes=1),underlying='QQQ')]
        kept=nonoverlapping(rows)
        self.assertEqual(len(kept),3)
        self.assertEqual([s['underlying'] for s in kept],['SPY','QQQ','SPY'])
        self.assertEqual(rows[1]['entry_at'],(OPEN+timedelta(minutes=1)).isoformat())

    def test_session_weighting_does_not_overweight_busy_day(self):
        rows=[self.sample('CALL',10) for _ in range(10)]
        rows.append(self.sample('CALL',-10,OPEN+timedelta(days=1)))
        stats=comparison(rows)
        self.assertGreater(stats['bot']['average_signed_stock_move_bps'],0)
        self.assertEqual(stats['equal_weight_session_means']['bot_bps'],0)

    def test_time_buckets_use_exchange_timezone_in_winter_and_summer(self):
        winter=datetime(2026,1,5,15,29,tzinfo=UTC)
        self.assertEqual(time_bucket(self.sample(entry=winter)),'09:30-10:30 ET')
        self.assertEqual(time_bucket(self.sample(entry=winter+timedelta(minutes=1))),'10:30-14:00 ET')
        self.assertEqual(time_bucket(self.sample(entry=OPEN+timedelta(hours=4,minutes=30))),'14:00-16:00 ET')

    def test_invalid_horizon_rejected(self):
        with self.assertRaises(ValueError):evaluate_session('SPY',[],0)

    def test_exit_comparison_uses_identical_entries_and_known_future_opens(self):
        rows=self.bars()
        results=evaluate_session_horizons('SPY',rows)
        self.assertEqual({h:len(v) for h,v in results.items()},{1:1,5:1,15:1})
        for horizon,expected in ((1,122),(5,126),(15,136)):
            row=results[horizon][0]
            self.assertAlmostEqual(row['signed_move_bps'],(expected/121-1)*10000)
            self.assertEqual(row['entry_at'],results[15][0]['entry_at'])
            self.assertEqual(row['selection_exit_at'],results[15][0]['exit_at'])
        rows[36]=(rows[36][0],80,80,100)
        changed=evaluate_session_horizons('SPY',rows)
        self.assertEqual(results[1],changed[1])
        self.assertEqual(results[5],changed[5])
        self.assertLess(changed[15][0]['signed_move_bps'],0)

    def test_gap_after_short_exit_excludes_entry_for_every_horizon(self):
        rows=self.bars();del rows[30]
        self.assertEqual(evaluate_session_horizons('SPY',rows),{1:[],5:[],15:[]})

    def test_short_exit_cannot_change_matched_nonoverlap_entries(self):
        rows=self.bars()
        rows=[(at,o,c,200) for at,o,c,v in rows]
        # Ascending prices and rising final volume create two nearby signals.
        rows[20]=(rows[20][0],rows[20][1],rows[20][2],500)
        rows[22]=(rows[22][0],rows[22][1],rows[22][2],500)
        results=evaluate_session_horizons('SPY',rows)
        self.assertEqual(len(results[1]),2)
        entries=[]
        for horizon,samples in results.items():
            selected=nonoverlapping([dict(s,underlying='SPY',session=DAY.isoformat()) for s in samples])
            entries.append([s['entry_at'] for s in selected])
        self.assertTrue(entries[0])
        self.assertEqual(len(entries[0]),1)
        self.assertEqual(entries[0],entries[1]);self.assertEqual(entries[1],entries[2])

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
                progress=Mock()
                result=replay(store.db,DAY,DAY+timedelta(days=3),progress=progress)
                self.assertEqual(progress.call_args_list[0].args,(1,3,'2026-10-02'))
                self.assertEqual(progress.call_args_list[-1].args,(3,3,'2026-10-04'))
                self.assertEqual(result['held_out_start'],'2026-10-04')
                self.assertEqual(result['results']['earlier']['overall']['observations'],2)
                self.assertEqual(result['results']['held_out']['overall']['observations'],1)
                self.assertIn('no option P&L',result['status'])
                self.assertEqual(result['results']['held_out']['nonoverlapping']['overall']['bot']['observations'],1)
                self.assertIn('not option profits',summary_text(result))
                exits=replay(store.db,DAY,DAY+timedelta(days=3),horizons=(1,5,15))
                self.assertEqual(list(exits['horizons']),['1','5','15'])
                self.assertEqual(exits['held_out_start'],result['held_out_start'])
                for report in exits['horizons'].values():
                    later=report['results']['held_out']['nonoverlapping']
                    self.assertEqual(later['overall']['bot']['observations'],1)
                    self.assertEqual(len(later['by_entry_time']['09:30-10:30 ET']['daily']),1)
                self.assertIn('same entries and spacing',summary_text(exits))
            finally:store.close()


if __name__=='__main__':unittest.main()
