import unittest
from unittest.mock import patch
from scout_loss_log import loss_rows, loss_log_html, loss_csv, refresh_loss_log, fetch_orders
from io import BytesIO
import json
import os
import tempfile
from pathlib import Path
from scout_loss_log import main


def order(id, side, qty, price, day, symbol='ABC', **extra):
    return dict(id=id, symbol=symbol, side=side, filled_qty=str(qty), filled_avg_price=str(price),
                filled_at=f'2026-09-{day:02d}T14:00:00Z', status='filled', **extra)


class LossTests(unittest.TestCase):
    def test_read_only_report_rebuilds_saved_dashboard(self):
        before = os.getcwd()
        with tempfile.TemporaryDirectory() as folder:
            try:
                os.chdir(folder)
                Path('.scout').mkdir()
                state = {'last_cycle': '2026-09-21T14:55:27-07:00',
                         'activity_journal': {'orders': {'s': {'reason': 'Exit'}}}}
                Path('.scout/scout_state_v43.json').write_text(json.dumps(state))
                Path('index.html').write_text('<main><section class="panel"><h2>Today’s summary</h2></section>'
                    '<div class="panel"><h2>Position replacement</h2></div></main>', encoding='utf-8')
                orders = [order('b', 'buy', 10, 20, 18), order('s', 'sell', 10, 18, 21)]
                with patch('scout_loss_log.fetch_orders', return_value=orders):
                    main()
                    main()
                html = Path('index.html').read_text(encoding='utf-8')
                self.assertEqual(html.count('id="loss-log"'), 1)
                self.assertIn('$20.00 loss', html)
                self.assertIn('Position replacement', html)
                self.assertTrue(Path('scout-loss-log.csv').exists())
            finally:
                os.chdir(before)

    def test_entry_exit_and_loss(self):
        rows = loss_rows([order('b', 'buy', 10, 20, 18), order('s', 'sell', 10, 18, 21)], {'s': {'reason': 'Risk exit'}})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['entry_price'], '20')
        self.assertEqual(rows[0]['loss_dollars'], '20')
        self.assertEqual(float(rows[0]['loss_percent']), 10)
        self.assertTrue(rows[0]['scout'])

    def test_prior_sales_consume_cost_and_winners_excluded(self):
        orders = [order('b1', 'buy', 2, 10, 16), order('b2', 'buy', 3, 20, 17),
                  order('s1', 'sell', 2, 12, 18), order('s2', 'sell', 3, 18, 21)]
        rows = loss_rows(orders, {'s2': {}})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['loss_dollars'], '6')
        self.assertEqual(rows[0]['entry_date'], '2026-09-17T14:00:00Z')

    def test_unknown_entries_and_partial_fills_not_totaled(self):
        orders = [order('s', 'sell', 10, 18, 21)]
        rows = loss_rows(orders, {'s': {}})
        self.assertIsNone(rows[0]['loss_dollars'])
        html = loss_log_html({'loss_log': {'rows': rows}})
        self.assertIn('0 confirmed losing sales', html)
        self.assertIn('1 Scout sales awaiting', html)
        partial = order('b', 'buy', 10, 20, 18)
        partial['status'] = 'canceled'
        self.assertIsNone(loss_rows([partial] + orders, {'s': {}})[0]['loss_dollars'])

    def test_unknown_ownership_separate(self):
        rows = loss_rows([order('b', 'buy', 10, 20, 18), order('s', 'sell', 10, 18, 21)], {})
        self.assertFalse(rows[0]['scout'])
        html = loss_log_html({'loss_log': {'rows': rows}})
        self.assertIn('0 confirmed losing sales', html)
        self.assertIn('Other paper-account sales: 1', html)

    def test_fifo_fractional_and_both_entries(self):
        rows = loss_rows([order('b1', 'buy', '.1', 10, 16), order('b2', 'buy', '.2', 20, 17),
                         order('s', 'sell', '.3', 15, 18)], {'s': {}})
        self.assertEqual(rows[0]['loss_dollars'], '0.5')
        self.assertEqual(rows[0]['last_entry_date'], '2026-09-17T14:00:00Z')

    def test_failed_history_keeps_prior_results(self):
        journal = {'loss_log': {'rows': [{'symbol': 'ABC'}]}}
        def fail(): raise RuntimeError('offline')
        refresh_loss_log(journal, fail)
        self.assertEqual(journal['loss_log']['rows'], [{'symbol': 'ABC'}])
        self.assertIn('unavailable', journal['loss_log']['status'])

    def test_csv_and_html_escape(self):
        rows = loss_rows([order('b', 'buy', 10, 20, 18), order('s', 'sell', 10, 18, 21)], {'s': {'reason': '=bad<script>'}})
        log = {'rows': rows}
        self.assertIn("'=bad<script>", loss_csv(log))
        self.assertNotIn('<script>', loss_log_html({'loss_log': log}))
        self.assertIn('download="scout-loss-log.csv"', loss_log_html({'loss_log': log}))

    @patch.dict('os.environ', {'ALPACA_API_KEY': 'test', 'ALPACA_SECRET_KEY': 'test'})
    def test_pagination_read_only_and_id_cursor(self):
        page = [{'id': str(i)} for i in range(500)]
        requests = []
        def response(req, **kw):
            requests.append(req)
            return BytesIO(json.dumps(page if len(requests) == 1 else [{'id': 'older'}]).encode())
        with patch('scout_loss_log.urlopen', response):
            self.assertEqual(len(fetch_orders()), 501)
        self.assertTrue(all(r.method == 'GET' for r in requests))
        self.assertIn('before_order_id=499', requests[1].full_url)
        self.assertTrue(all(r.full_url.startswith('https://paper-api.alpaca.markets/') for r in requests))


if __name__ == '__main__':
    unittest.main()

