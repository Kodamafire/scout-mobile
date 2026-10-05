"""Deterministic options lifecycle tests; no credentials or broker orders."""
import ast
import asyncio
import copy
import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from scout_options.core import Config, Contract, Quote, Signal, PaperEngine, select_contracts, directional_signal
from scout_options.service import manage

NOW = datetime(2026,10,5,15,tzinfo=timezone.utc)
CFG = Config()


def contract(kind='CALL', symbol=None):
    return Contract(symbol or kind,'SPY',kind,NOW.date()+timedelta(days=14),
                    .5 if kind=='CALL' else -.5,.3,1000,NOW)


def quote(symbol='CALL', at=NOW, bid=.88, ask=.90, size=10, feed='opra'):
    return Quote(symbol,bid,ask,size,size,at,feed)


class SelectorTests(unittest.TestCase):
    def test_both_directions_and_ranking(self):
        contracts=[contract('CALL'),contract('PUT')]
        quotes={c.symbol:quote(c.symbol) for c in contracts}
        for kind in ('CALL','PUT'):
            chosen=select_contracts(contracts,quotes,Signal('SPY',kind,NOW,'test'),NOW,CFG)
            self.assertEqual([c.kind for c in chosen],[kind])

    def test_bad_stale_future_indicative_and_wide_quotes_block_entry(self):
        c=contract();s=Signal('SPY','CALL',NOW,'test')
        for q in [quote(at=NOW-timedelta(seconds=4)),quote(at=NOW+timedelta(seconds=1)),
                  quote(feed='indicative'),quote(bid=0),quote(bid=1,ask=.9),
                  quote(bid=.1,ask=1),quote(size=0),quote(ask=float('nan'))]:
            self.assertEqual(select_contracts([c],{c.symbol:q},s,NOW,CFG),[])

    def test_contract_and_signal_validation(self):
        c=contract();q=quote();s=Signal('SPY','CALL',NOW,'test')
        for bad in [replace(c,expiry=NOW.date()),replace(c,delta=-.5),replace(c,delta=.1),
                    replace(c,iv=float('nan')),replace(c,iv=0),replace(c,multiplier=10),
                    replace(c,tradable=False),replace(c,open_interest=1),
                    replace(c,metadata_at=NOW-timedelta(seconds=121))]:
            self.assertEqual(select_contracts([bad],{c.symbol:q},s,NOW,CFG),[])
        for s in [replace(s,at=NOW-timedelta(seconds=91)),replace(s,direction='WAIT')]:
            self.assertEqual(select_contracts([c],{c.symbol:q},s,NOW,CFG),[])

    def test_completed_minute_signals_are_symmetric(self):
        for direction,sign in [('CALL',1),('PUT',-1)]:
            bars=[(NOW-timedelta(minutes=21-i),100+sign*i,200 if i==20 else 100) for i in range(21)]
            self.assertEqual(directional_signal('SPY',bars,NOW).direction,direction)
            self.assertIsNone(directional_signal('SPY',bars[:-1],NOW))
            bad=bars.copy();bad[5]=(bad[5][0]+timedelta(seconds=1),bad[5][1],100)
            self.assertIsNone(directional_signal('SPY',bad,NOW))
            self.assertIsNone(directional_signal('SPY',bars,NOW+timedelta(minutes=2)))


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/'ledger.sqlite'
        self.engine=PaperEngine(self.path,10000)
        self.engine.tick(NOW,True,NOW+timedelta(hours=5))
        self.engine.on_quote(quote(),NOW)

    def tearDown(self):
        self.engine.close();self.tmp.cleanup()

    def buy(self, kind='CALL'):
        if kind=='PUT': self.engine.on_quote(quote('PUT'),NOW)
        return self.engine.enter([contract(kind)],Signal('SPY',kind,NOW,'test'),NOW)

    def fill(self):
        self.buy()
        self.engine.on_quote(quote(at=NOW+timedelta(seconds=1)),NOW+timedelta(seconds=1))

    def test_next_quote_only_and_duplicate_intent_guard(self):
        self.assertEqual(self.buy(),'SIMULATED BUY PENDING')
        self.assertEqual(self.buy(),'DUPLICATE UNDERLYING')
        self.assertFalse(self.engine.on_quote(quote(),NOW))
        self.assertEqual(self.engine.snapshot()['positions'],{})
        self.engine.on_quote(quote(at=NOW+timedelta(seconds=1)),NOW+timedelta(seconds=1))
        self.assertEqual(self.engine.snapshot()['positions']['CALL']['qty'],1)
        self.assertAlmostEqual(self.engine.snapshot()['cash'],9910)

    def test_put_fills_and_opposite_signal_exits(self):
        self.buy('PUT')
        at=NOW+timedelta(seconds=1)
        self.engine.on_quote(quote('PUT',at),at)
        self.engine.update_signals([Signal('SPY','CALL',at,'reverse')])
        self.engine.tick(at,True,NOW+timedelta(hours=5))
        self.assertEqual(self.engine._active()[0]['reason'],'SETUP INVALIDATED')

    def test_partial_fills_do_not_reconsume_quote(self):
        self.engine.cfg=replace(CFG,premium_risk_fraction=.03)
        self.buy()
        at=NOW+timedelta(seconds=1)
        self.engine.on_quote(quote(at=at,size=1),at)
        self.assertEqual(self.engine.snapshot()['positions']['CALL']['qty'],1)
        self.assertEqual(self.engine._active()[0]['filled'],1)
        self.assertFalse(self.engine.on_quote(quote(at=at,size=1),at))
        self.engine.on_quote(quote(at=at+timedelta(seconds=1),size=1),at+timedelta(seconds=1))
        self.assertEqual(self.engine.snapshot()['positions']['CALL']['qty'],2)

    def test_restart_restores_intent_without_reissuing_and_needs_fresh_quote(self):
        self.buy();oid=self.engine._active()[0]['id']
        self.engine.close();self.engine=PaperEngine(self.path,10000)
        self.assertEqual(self.engine._active()[0]['id'],oid)
        self.assertEqual(self.buy(),'SESSION NOT READY OR PAUSED')
        at=NOW+timedelta(seconds=1)
        self.engine.on_quote(quote(at=at),at)
        self.assertEqual(self.engine.snapshot()['positions']['CALL']['qty'],1)
        self.assertEqual(len(self.engine.snapshot()['orders']),1)

    def test_only_one_service_can_own_ledger(self):
        with self.assertRaises(RuntimeError): PaperEngine(self.path,10000)

    def test_stale_data_warns_and_never_fakes_exit_fill(self):
        self.fill();at=NOW+timedelta(seconds=5)
        self.engine.tick(at,True,NOW+timedelta(hours=5))
        self.assertEqual(self.engine._active(),[])
        self.assertTrue(any('stale' in a for a in self.engine.snapshot()['alerts']))
        self.assertEqual(len(self.engine.snapshot()['positions']),1)

    def test_loss_exit_fills_later_and_cooldown_blocks_reentry(self):
        self.fill();at=NOW+timedelta(seconds=2)
        self.engine.on_quote(quote(at=at,bid=.60,ask=.62),at)
        self.engine.tick(at,True,NOW+timedelta(hours=5))
        self.assertEqual(self.engine._active()[0]['reason'],'PREMIUM LOSS')
        self.assertEqual(len(self.engine.snapshot()['positions']),1)
        self.engine.on_quote(quote(at=at+timedelta(seconds=1),bid=.60,ask=.62),at+timedelta(seconds=1))
        self.assertEqual(self.engine.snapshot()['positions'],{})
        self.assertEqual(self.engine.enter([contract()],Signal('SPY','CALL',at+timedelta(seconds=1),'x'),at+timedelta(seconds=1)),'COOLDOWN')

    def test_runner_trail_and_reprice_failed_exit(self):
        self.fill();at=NOW+timedelta(seconds=2)
        self.engine.on_quote(quote(at=at,bid=1.3,ask=1.32),at)
        self.engine.tick(at,True,NOW+timedelta(hours=5))
        self.assertEqual(self.engine._active(),[])
        at+=timedelta(seconds=1)
        self.engine.on_quote(quote(at=at,bid=1.05,ask=1.07),at)
        self.engine.tick(at,True,NOW+timedelta(hours=5))
        self.assertEqual(self.engine._active()[0]['reason'],'RUNNER TRAIL')
        at+=timedelta(seconds=16)
        self.engine.on_quote(quote(at=at,bid=.95,ask=.97),at)
        self.engine.tick(at,True,NOW+timedelta(hours=5))
        self.assertEqual(len(self.engine._active()),1)
        self.assertEqual(self.engine._active()[0]['limit'],.95)
        self.assertEqual(self.engine._active()[0]['reason'],'REPRICE UNFILLED EXIT')

    def test_cutoff_cancels_entry_and_blocks_new_intents(self):
        self.buy();at=NOW+timedelta(seconds=1)
        self.engine.tick(at,True,at+timedelta(minutes=20))
        self.assertEqual(self.engine._active(),[])
        self.assertEqual(self.buy(),'SESSION NOT READY OR PAUSED')

    def test_budget_and_shared_pending_orders(self):
        self.engine.cfg=replace(CFG,premium_risk_fraction=.001)
        self.assertEqual(self.buy(),'PREMIUM BUDGET TOO SMALL')
        self.engine.cfg=CFG
        external=dict(at=NOW,equity=10000,cash=10000,gross_exposure=0,pending_orders=True)
        self.assertEqual(self.engine.enter([contract()],Signal('SPY','CALL',NOW,'x'),NOW,external),
                         'SHARED ACCOUNT UNAVAILABLE OR PENDING')
        external.update(pending_orders=False,cash=3500)
        self.assertEqual(self.engine.enter([contract()],Signal('SPY','CALL',NOW,'x'),NOW,external),
                         'PREMIUM BUDGET TOO SMALL')

    def test_wide_spread_does_not_disable_existing_loss_protection(self):
        self.fill();at=NOW+timedelta(seconds=2)
        self.engine.on_quote(quote(at=at,bid=.5,ask=1),at)
        self.engine.tick(at,True,NOW+timedelta(hours=5))
        self.assertEqual(self.engine._active()[0]['reason'],'PREMIUM LOSS')

    def test_session_loss_pauses_entries_but_manages_positions(self):
        self.engine.cfg=replace(CFG,premium_risk_fraction=.03)
        self.buy();at=NOW+timedelta(seconds=1)
        self.engine.on_quote(quote(at=at),at)
        at+=timedelta(seconds=1)
        self.engine.on_quote(quote(at=at,bid=.1,ask=.12),at)
        self.engine.tick(at,True,NOW+timedelta(hours=5))
        self.assertTrue(self.engine.snapshot()['paused'])
        self.assertEqual(self.engine._active()[0]['side'],'SELL')

    def test_partial_buy_is_canceled_before_protective_exit(self):
        self.engine.cfg=replace(CFG,premium_risk_fraction=.03)
        self.buy();at=NOW+timedelta(seconds=1)
        self.engine.on_quote(quote(at=at,size=1),at)
        at+=timedelta(seconds=1)
        self.engine.on_quote(quote(at=at,bid=.50,ask=1,size=1),at)
        self.engine.tick(at,True,NOW+timedelta(hours=5))
        active=self.engine._active()
        self.assertEqual([(o['side'],o['qty']) for o in active],[('SELL',1)])

    def test_expiration_is_an_exit_trigger(self):
        self.fill();at=NOW+timedelta(days=14)
        self.engine.on_quote(quote(at=at),at)
        self.engine.tick(at,True,at+timedelta(hours=5))
        self.assertEqual(self.engine._active()[0]['reason'],'EXPIRATION')

    def test_affordable_contract_selected_after_expensive_ranked_contract(self):
        expensive=replace(contract(symbol='EXPENSIVE'),open_interest=2000)
        self.engine.on_quote(quote('EXPENSIVE',bid=1.99,ask=2),NOW)
        result=self.engine.enter([expensive,contract()],Signal('SPY','CALL',NOW,'x'),NOW)
        self.assertEqual(result,'SIMULATED BUY PENDING')
        self.assertEqual(self.engine._active()[0]['symbol'],'CALL')

    def test_entry_timeout_releases_reserved_budget(self):
        self.buy();at=NOW+timedelta(seconds=30)
        self.engine.tick(at,True,NOW+timedelta(hours=5))
        self.assertEqual(self.engine._active(),[])
        self.engine.on_quote(quote(at=at),at)
        self.assertEqual(self.engine.enter([contract()],Signal('SPY','CALL',at,'x'),at),'SIMULATED BUY PENDING')


class SafetyTests(unittest.TestCase):
    def test_adapter_has_paper_lock_and_no_order_submission(self):
        tree=ast.parse(Path('scout_options/alpaca_observer.py').read_text())
        calls=[n for n in ast.walk(tree) if isinstance(n,ast.Call)]
        clients=[n for n in calls if isinstance(n.func,ast.Name) and n.func.id=='TradingClient']
        self.assertGreaterEqual(len(clients),1)
        self.assertTrue(all(any(k.arg=='paper' and isinstance(k.value,ast.Constant) and k.value.value is True for k in client.keywords) for client in clients))
        forbidden={'submit_order','close_position','close_all_positions','cancel_order_by_id','exercise_options_position'}
        self.assertFalse(any(isinstance(n.func,ast.Attribute) and n.func.attr in forbidden for n in calls))

    def test_one_second_manager_does_not_wait_for_scanner(self):
        class Engine:
            cfg=Config()
            def __init__(self):self.times=[]
            def tick(self,*args):
                self.times.append(asyncio.get_running_loop().time())
                if len(self.times)==3: stop.set()
            def snapshot(self):return {'mode':'test'}
        async def exercise():
            engine=Engine()
            with tempfile.TemporaryDirectory() as tmp:
                def context():
                    now=datetime.now(timezone.utc)
                    return True,now+timedelta(hours=1),now
                await manage(engine,context,Path(tmp)/'status.json',stop)
            self.assertEqual(len(engine.times),3)
            self.assertTrue(all(.8 <= b-a <= 1.5 for a,b in zip(engine.times,engine.times[1:])))
        stop=asyncio.Event();asyncio.run(exercise())

if __name__=='__main__':unittest.main()
