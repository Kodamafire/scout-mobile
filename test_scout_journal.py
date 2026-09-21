"""Journal regression tests with simulated broker responses only."""
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import Mock
from scout_journal import record_cycle, matched_sales, journal_html


def order(side, qty, price, day, status='filled'):
    return dict(symbol='ABC', side=side, qty=str(qty), price=str(price), status=status,
                filled_at=f'2026-09-{day:02d}T14:00:00+00:00', reason='Recorded decision')


class JournalTests(unittest.TestCase):
    def test_fifo_multiple_entries_and_partial_position_sale(self):
        j = {'orders': {'b1': order('buy', 2, 10, 21), 'b2': order('buy', 3, 20, 22),
                        's1': order('sell', 4, 25, 23), 's2': order('sell', 1, 18, 24)}}
        sales = matched_sales(j)
        self.assertEqual([s['pnl'] for s in sales], [40, -2])
        self.assertEqual([s['days'] for s in sales], [1.5, 2])

    def test_unknown_opening_cost_is_not_profit(self):
        self.assertIsNone(matched_sales({'orders': {'s': order('sell', 10, 100, 22)}})[0]['pnl'])

    def test_partial_fill_prevents_misleading_results(self):
        j = {'orders': {'b': order('buy', 10, 10, 21), 'p': order('sell', 5, 15, 22, 'canceled'),
                        's': order('sell', 10, 20, 23)}}
        self.assertIsNone(matched_sales(j)[0]['pnl'])
        self.assertEqual(len(matched_sales(j)), 1)

    def test_fractional_quantities(self):
        j = {'orders': {'b': order('buy', '0.3', '10.01', 21), 's': order('sell', '0.3', '11.01', 22)}}
        self.assertEqual(matched_sales(j)[0]['pnl'], .3)

    def test_requested_is_not_filled_and_retries_deduplicate(self):
        report = dict(cycle='one', positions=[], candidates=[dict(symbol='ABC', entry_score=8,
                      order_status='SUBMITTED fake')], upgrade=None, market_open=True,
                      journal_date='2026-09-21', updated='Sep 21')
        state = {'last_cycle': '2026-09-21T07:00:00-07:00'}
        broker = NS(get_order_by_id=Mock(return_value=NS(status='new', side='buy', filled_qty='0',
                        filled_avg_price=None, filled_at=None)))
        j = record_cycle(report, state, broker)
        self.assertEqual(matched_sales(j), [])
        event_count = len(j['events'])
        record_cycle(report, state, broker)
        self.assertEqual(len(j['events']), event_count)
        broker.get_order_by_id.return_value = NS(status='filled', side='buy', filled_qty='2',
                filled_avg_price='10', filled_at=datetime(2026, 9, 21, 14, tzinfo=timezone.utc))
        record_cycle(report, state, broker)
        self.assertEqual(j['orders']['fake']['status'], 'filled')
        count = len(j['events'])
        record_cycle(report, state, broker)
        self.assertEqual(len(j['events']), count)

    def test_order_fetch_failure_keeps_pending_order(self):
        state = {'last_cycle': '2026-09-21T07:00:00-07:00'}
        report = dict(cycle='one', positions=[], candidates=[dict(symbol='ABC', entry_score=8,
                      order_status='SUBMITTED fake')], upgrade=None)
        j = record_cycle(report, state, NS(get_order_by_id=Mock(side_effect=RuntimeError())))
        self.assertEqual(j['orders']['fake']['status'], 'submitted')
        self.assertIn('unavailable', j['sync_note'])

    def test_escaping_and_empty_states(self):
        report = dict(updated='old snapshot', market_open=False, journal_date='2026-09-21')
        html = journal_html({}, report)
        self.assertIn('next scheduled cycle', html)
        self.assertIn('No fully filled sales', html)
        j = {'events': [dict(at='2026-09-21', cycle='one', symbol='<script>', action='Position checked', reason='<bad>')]}
        html = journal_html(j, report)
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;script&gt;', html)

    def test_previous_day_events_not_counted_as_today(self):
        report = dict(updated='Sep 22', market_open=True, journal_date='2026-09-22')
        j = {'events': [dict(at='2026-09-21', cycle='one', symbol='ABC', action='Buy requested', reason='Qualified')]}
        self.assertIn('0 recorded checks; 0 order requests', journal_html(j, report))

if __name__ == '__main__':
    unittest.main()

