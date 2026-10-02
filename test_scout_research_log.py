from datetime import datetime
import json
from pathlib import Path
from types import SimpleNamespace as NS
import tempfile
import unittest
from scout_research_log import account_snapshot,record_inputs

class ResearchLogTests(unittest.TestCase):
    def test_account_fields_are_allowlisted(self):
        a=NS(equity='1000',cash='500',account_number='secret',api_key='secret')
        p=NS(symbol='ABC',qty='2',avg_entry_price='10',market_value='22',unrealized_plpc='.1',id='secret')
        x=account_snapshot(a,[p],{'ABC':NS(id='secret')},{'api_secret':'secret','warning_streak':{'ABC':1}})
        self.assertNotIn('secret',json.dumps(x));self.assertEqual(x['open_order_symbols'],['ABC'])
    def test_append_deduplication_and_source_hash(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'rule.py';source.write_text('rule one')
            now=datetime.fromisoformat('2026-10-01T16:00:00-07:00')
            out=Path(folder)/'inputs'
            for _ in range(2):record_inputs(out,'cycle',now,False,{'paper_only':True},{'scan':{'status':'UNAVAILABLE'}},[source])
            rows=(out/'2026-10-01.jsonl').read_text().splitlines();self.assertEqual(len(rows),1)
            row=json.loads(rows[0]);self.assertFalse(row['market_open']);self.assertEqual(len(row['source_hashes']['rule.py']),64)
            self.assertEqual(row['inputs']['scan']['status'],'UNAVAILABLE')
    def test_bad_numbers_cannot_write_misleading_json(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):record_inputs(folder,'one',datetime.now(),True,{}, {'price':float('nan')})
            self.assertFalse(list(Path(folder).glob('*.jsonl')))

if __name__=='__main__':unittest.main()

class CandidateCaptureTests(unittest.TestCase):
    def test_logging_keeps_candidate_choices_identical(self):
        import ast
        from unittest.mock import Mock
        from scout_portfolio_replay import load_rules
        source=Path('scout_runner.py').read_text();tree=ast.parse(source)
        function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='scan_candidates')
        good=dict(close=100,ema20=99,ema50=95,sma200=90,macd=2,macd_signal=1,
                  macd_rising=True,rsi14=60,atr_pct=1,relative_volume=1.3,avg_dollar_volume=1e8,rs20=5,return20=6)
        frames={'SPY':good,'GOOD':good,'PRICE':{**good,'close':500},'BAD':None}
        ns=load_rules();ns.update(MostActivesRequest=lambda **kw:kw,MostActivesBy=NS(VOLUME='volume'),
                                 bars_frame=lambda *a,**kw:frames,indicators=lambda frame,spy:frame)
        exec(compile(ast.Module(body=[function],type_ignores=[]),'<scan capture>','exec'),ns)
        screener=NS(get_most_actives=Mock(return_value=NS(most_actives=[NS(symbol=s) for s in ('GOOD','PRICE','BAD')])))
        normal=ns['scan_candidates'](screener,None,set())
        sink={};captured=ns['scan_candidates'](screener,None,set(),sink)
        self.assertEqual(normal,captured)
        self.assertEqual([r['symbol'] for r in captured],['GOOD'])
        self.assertEqual(sink['status'],'COMPLETE');self.assertEqual(len(sink['rows']),3)
