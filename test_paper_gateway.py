"""Offline fake-broker failure/recovery tests; no remote API calls."""
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from scout_options.paper_gateway import PaperOutbox, AlpacaPaperTransport

SYMBOL = 'SPY261023C00600000'


class Broker:
    def __init__(self):
        self.orders = {}
        self.sends = 0
        self.cancels = 0
        self.timeout_after_accept = False
        self.timeout_before_accept = False
        self.lookup_error = False

    def lookup(self, cid):
        if self.lookup_error:
            raise TimeoutError('secret remote payload')
        return self.orders.get(cid)

    def submit(self, r):
        self.sends += 1
        if self.timeout_before_accept:
            raise TimeoutError()
        order = dict(id='broker-'+r['client_id'], client_order_id=r['client_id'],
                     symbol=r['symbol'], side=r['side'].lower(), qty=str(r['qty']),
                     status='new', filled_qty='0', filled_avg_price=None)
        self.orders[r['client_id']] = order
        if self.timeout_after_accept:
            raise TimeoutError()
        return order

    def cancel(self, oid):
        self.cancels += 1


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)/'outbox.sqlite'
        self.broker = Broker()
        self.box = PaperOutbox(self.path, self.broker)

    def tearDown(self):
        self.box.close()
        self.tmp.cleanup()

    def stage(self):
        self.box.armed = True
        return self.box.stage(SYMBOL, 'BUY', 2, '1.00')

    def fill(self, cid, qty='2', status='filled'):
        self.broker.orders[cid].update(filled_qty=qty, filled_avg_price='1.00', status=status)
        self.box.sync()

    def test_disarmed_and_invalid_orders(self):
        with self.assertRaises(RuntimeError): self.box.stage(SYMBOL, 'BUY', 1, 1)
        self.box.armed = True
        for sym, side, qty, price in [('SPY','BUY',1,1),(SYMBOL,'SHORT',1,1),
                                      (SYMBOL,'BUY',True,1),(SYMBOL,'BUY',1,'NaN')]:
            with self.assertRaises(ValueError): self.box.stage(sym,side,qty,price)
        with self.assertRaises(RuntimeError): self.box.stage(SYMBOL,'SELL',1,1)

    def test_intent_persisted_and_duplicate_blocked(self):
        cid = self.stage()
        self.assertEqual(self.box.records()[0]['status'], 'STAGED')
        with self.assertRaises(RuntimeError): self.box.stage(SYMBOL,'BUY',1,1)
        self.box.sync(); self.box.sync()
        self.assertEqual(self.broker.sends,1)
        self.assertEqual(self.box.records()[0]['client_id'],cid)

    def test_timeout_after_accept_restart_recovers_once(self):
        cid=self.stage(); self.broker.timeout_after_accept=True
        self.box.sync()
        self.assertFalse(self.box.armed)
        self.box.close(); self.box=PaperOutbox(self.path,self.broker)
        self.assertFalse(self.box.armed)
        self.box.sync(); self.fill(cid)
        self.assertEqual(self.broker.sends,1)
        self.assertEqual(self.box.owned_quantities(),{SYMBOL:2})

    def test_timeout_before_accept_never_blind_retries(self):
        self.stage(); self.broker.timeout_before_accept=True
        self.box.sync(); self.box.armed=True; self.box.sync()
        self.assertEqual(self.broker.sends,1)
        self.assertEqual(self.box.records()[0]['status'],'SUBMITTING')
        self.assertFalse(self.box.verify_positions({}))

    def test_lookup_failure_blocks_send_without_remote_payload(self):
        self.stage(); self.broker.lookup_error=True; self.box.sync()
        self.assertEqual(self.broker.sends,0)
        self.assertFalse(self.box.armed)
        self.assertEqual(self.box.records()[0]['error'],'TimeoutError')

    def test_cumulative_partial_fills_are_not_double_counted(self):
        cid=self.stage(); self.box.sync()
        self.fill(cid,'1','partially_filled'); self.box.sync()
        self.assertEqual(self.box.owned_quantities(),{SYMBOL:1})
        self.fill(cid)
        self.assertEqual(self.box.records()[0]['filled_notional'],'200.00')
        self.assertTrue(self.box.verify_positions({SYMBOL:'2'}))
        self.assertFalse(self.box.verify_positions({SYMBOL:'3'}))
        self.assertFalse(self.box.armed)

    def test_cancel_fill_race_requires_confirmation(self):
        cid=self.stage(); self.box.sync(); self.fill(cid,'1','partially_filled')
        self.box.cancel(cid)
        self.assertEqual(self.box.records()[0]['status'],'CANCEL_REQUESTED')
        with self.assertRaises(RuntimeError): self.box.stage(SYMBOL,'BUY',1,1)
        self.fill(cid,'2','canceled')
        self.assertEqual(self.box.owned_quantities(),{SYMBOL:2})

    def test_sell_only_bot_owned_and_confirmed_fills_reduce_holdings(self):
        cid=self.stage(); self.box.sync(); self.fill(cid)
        with self.assertRaises(RuntimeError): self.box.stage(SYMBOL,'SELL',3,1)
        sell=self.box.stage(SYMBOL,'SELL',2,1); self.box.sync(); self.fill(sell)
        self.assertTrue(self.box.verify_positions({}))

    def test_mismatch_and_unknown_positions_disarm(self):
        cid=self.stage(); self.box.sync()
        self.broker.orders[cid]['symbol']='QQQ261023P00600000'
        self.box.sync()
        self.assertEqual(self.box.records()[0]['status'],'REVIEW')
        self.assertFalse(self.box.armed)
        self.assertFalse(self.box.verify_positions({'UNKNOWN':1}))

    def test_regressing_cumulative_fill_requires_review(self):
        cid=self.stage(); self.box.sync(); self.fill(cid,'1','partially_filled')
        self.fill(cid,'0','new')
        self.assertEqual(self.box.records()[0]['status'],'REVIEW')
        self.assertEqual(self.box.owned_quantities(),{SYMBOL:1})

    def test_restart_does_not_submit_staged_unarmed_order(self):
        self.stage(); self.box.close(); self.box=PaperOutbox(self.path,self.broker)
        self.box.sync(); self.assertEqual(self.broker.sends,0)

    def test_transport_is_paper_only_and_uses_long_option_limit_intent(self):
        with patch('alpaca.trading.client.TradingClient') as client:
            transport=AlpacaPaperTransport('fake-key','fake-secret')
            client.assert_called_once_with('fake-key','fake-secret',paper=True)
            transport.submit(dict(symbol=SYMBOL,qty=1,side='BUY',limit='1',client_id='scopt-test'))
            request=client.return_value.submit_order.call_args.kwargs['order_data']
            self.assertEqual(request.position_intent.value,'buy_to_open')
            self.assertEqual(request.type.value,'limit')
            self.assertEqual(request.time_in_force.value,'day')
            self.assertFalse(request.extended_hours)
            transport.submit(dict(symbol=SYMBOL,qty=1,side='SELL',limit='1',client_id='scopt-test2'))
            self.assertEqual(client.return_value.submit_order.call_args.kwargs['order_data'].position_intent.value,'sell_to_close')

    def test_malformed_position_data_disarms(self):
        self.box.armed=True
        self.assertFalse(self.box.verify_positions({SYMBOL:'NaN'}))
        self.assertFalse(self.box.armed)

    def test_single_writer(self):
        with self.assertRaises(BlockingIOError): PaperOutbox(self.path,self.broker)

if __name__ == '__main__': unittest.main()
