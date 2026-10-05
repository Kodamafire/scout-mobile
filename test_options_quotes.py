import asyncio
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime,timezone,timedelta
from pathlib import Path
from types import SimpleNamespace as Obj
from unittest.mock import Mock
from scout_options.quote_recorder import QuoteRecorder,probe
from scout_options.core import Signal
from scout_options.alpaca_observer import Observer

NOW=datetime(2026,10,6,14,tzinfo=timezone.utc)


def quote(**changes):
    values=dict(symbol='QQQ261016C00500000',timestamp=NOW,bid_price=1.,ask_price=1.05,
                bid_size=3,ask_size=4,bid_exchange='A',ask_exchange='B',conditions='R')
    values.update(changes);return Obj(**values)


class TapeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'quotes.sqlite'

    def tearDown(self):self.tmp.cleanup()

    def test_timestamp_prices_sizes_feed_and_signal_survive_close(self):
        tape=QuoteRecorder(self.path)
        tape.quote(quote(),'indicative',NOW+timedelta(seconds=.25),{'underlying':'QQQ','kind':'CALL'})
        tape.signal(Signal('QQQ','CALL',NOW,'test'),'indicative',NOW)
        tape.close()
        with sqlite3.connect(self.path) as db:
            rows=db.execute('SELECT kind,feed,market_at,received_at,bid,ask,bid_size,ask_size,metadata FROM events ORDER BY id').fetchall()
            self.assertEqual(len(rows),2);self.assertEqual(rows[0][:3],('quote','indicative',NOW.isoformat()))
            self.assertEqual(rows[0][4:8],(1.,1.05,3.,4.))
            self.assertEqual(json.loads(rows[0][-1])['arrival_delay_seconds'],.25)
            self.assertEqual(json.loads(rows[1][-1])['direction'],'CALL')
            self.assertIsNotNone(db.execute('SELECT ended_at FROM runs').fetchone()[0])
        self.assertEqual(tape.snapshot()['recorded_quotes'],1)

    def test_restart_appends_and_preserves_separate_runs(self):
        for _ in range(2):
            tape=QuoteRecorder(self.path);tape.quote(quote(),'opra',NOW);tape.close()
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM events').fetchone()[0],2)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM runs').fetchone()[0],2)

    def test_invalid_timestamp_nonfinite_and_unknown_feed_are_counted_as_gaps(self):
        tape=QuoteRecorder(self.path)
        tape.quote(quote(timestamp=NOW.replace(tzinfo=None)),'opra',NOW)
        tape.quote(quote(ask_price=float('nan')),'opra',NOW)
        tape.quote(quote(),'synthetic',NOW);tape.close()
        self.assertEqual(tape.snapshot()['dropped_events'],3)
        self.assertEqual(tape.snapshot()['recorded_events'],0)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT dropped FROM runs').fetchone()[0],3)

    def test_crossed_zero_and_future_quotes_are_labeled_not_silently_validated(self):
        tape=QuoteRecorder(self.path)
        tape.quote(quote(bid_price=2),'indicative',NOW)
        tape.quote(quote(bid_size=0,timestamp=NOW+timedelta(seconds=1)),'opra',NOW)
        tape.close()
        with sqlite3.connect(self.path) as db:
            metadata=[json.loads(r[0]) for r in db.execute('SELECT metadata FROM events ORDER BY id')]
        self.assertEqual(metadata[0]['quality'],'crossed')
        self.assertEqual(metadata[1]['quality'],'zero_price_or_size')
        self.assertEqual(metadata[1]['arrival_delay_seconds'],-1)

    def test_local_size_limit_preserves_tape_and_exposes_drops(self):
        tape=QuoteRecorder(self.path,max_bytes=1)
        tape.quote(quote(),'indicative',NOW);tape.close()
        self.assertEqual(tape.snapshot()['dropped_events'],1)
        self.assertIn('size limit',tape.snapshot()['status'])
        self.assertTrue(self.path.exists())

    def test_queue_is_bounded_and_dropped_events_counted(self):
        tape=QuoteRecorder(self.path)
        tape.close()
        # Exercise the full bounded queue without a concurrently draining writer.
        tape.pending=__import__('queue').Queue(maxsize=1)
        tape.state['status']='Recording'
        tape.signal(Signal('QQQ','CALL',NOW,'a'),'indicative',NOW)
        tape.signal(Signal('QQQ','CALL',NOW,'b'),'indicative',NOW)
        self.assertEqual(tape.snapshot()['queued_events'],1)
        self.assertEqual(tape.snapshot()['dropped_events'],1)

    def test_observer_records_before_reference_engine_receives_quote(self):
        observer=Observer.__new__(Observer)
        observer.recorder=Mock();observer.engine=Mock();observer.contracts={};observer.feed='indicative'
        calls=[]
        observer.recorder.quote.side_effect=lambda *args:calls.append('record')
        observer.engine.on_quote.side_effect=lambda *args:calls.append('engine')
        asyncio.run(observer.quote_handler(quote()))
        self.assertEqual(calls,['record','engine'])


class ProbeTests(unittest.TestCase):
    def trading(self):
        client=Mock()
        client.get_option_contracts.return_value=Obj(option_contracts=[Obj(
            symbol='QQQ261016C00500000',tradable=True,size='100',root_symbol='QQQ',open_interest='500')])
        return client

    def test_both_feeds_explicit_and_acceptance_does_not_claim_live_access(self):
        options=Mock();options.get_option_latest_quote.return_value={'QQQ261016C00500000':quote()}
        result=probe(self.trading(),options,NOW)
        self.assertEqual([c.args[0].feed.value for c in options.get_option_latest_quote.call_args_list],['indicative','opra'])
        self.assertEqual(result['feeds']['opra']['request'],'Accepted')
        self.assertIn('stream access',result['limitation'])

    def test_remote_secret_body_is_never_in_diagnostic_result(self):
        options=Mock();exc=RuntimeError('SECRET REMOTE BODY');exc.status_code=403
        options.get_option_latest_quote.side_effect=[{},exc]
        result=probe(self.trading(),options,NOW)
        self.assertFalse(result['feeds']['indicative']['quote_returned'])
        self.assertEqual(result['feeds']['opra']['http'],403)
        self.assertNotIn('SECRET',json.dumps(result))

    def test_empty_contract_selection_does_not_guess_entitlement(self):
        trading=self.trading();trading.get_option_contracts.return_value=Obj(option_contracts=[])
        options=Mock();result=probe(trading,options,NOW)
        self.assertIn('not determined',result['status']);options.get_option_latest_quote.assert_not_called()


if __name__=='__main__':unittest.main()
