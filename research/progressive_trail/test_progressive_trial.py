import json,sqlite3,tempfile,unittest
from pathlib import Path
from progressive_trial import floor_for,replay,synthetic,compare,collect
class Tests(unittest.TestCase):
 def test_tiers_and_ratchet(self):
  floor=None
  for peak,expected in [(1.2,1.02),(1.5,1.35),(2.,1.95),(3.,2.94),(10.,9.90)]:
   floor=floor_for(1,peak,floor,True);self.assertAlmostEqual(floor,expected)
  self.assertEqual(floor_for(1,2,floor,True),floor);self.assertAlmostEqual(floor_for(1,2,None,False),1.70)
 def test_next_quote_fill(self):
  t=synthetic()['trades'][0];r=replay(t,True)
  self.assertEqual(r['trigger_at'],t['quotes'][5]['received_at']);self.assertEqual(r['fills'][0]['at'],t['quotes'][6]['received_at']);self.assertAlmostEqual(r['fills'][0]['price'],1.89)
 def test_whipsaw(self):
  r=compare(synthetic())['rows'][1];self.assertLess(r['progressive']['net_pnl'],r['baseline']['net_pnl'])
 def test_censored(self):
  r=compare(synthetic());self.assertEqual(r['incomplete_pairs'],1);self.assertIsNone(r['rows'][-1]['progressive']['net_pnl'])
 def test_partial_stale_and_indicative(self):
  t=synthetic()['trades'][0];t['qty']=2;r=replay(t,True);self.assertEqual(len(r['fills']),2)
  t['quotes'][6]['feed']='indicative';t['quotes'][7]['received_at']='2026-10-07T14:00:30+00:00'
  r=replay(t,True);self.assertIsNone(r['net_pnl']);self.assertGreaterEqual(r['discarded_quotes'],2)
 def test_cost_sensitivity(self):
  t=synthetic()['trades'][0];self.assertAlmostEqual(replay(t,True)['net_pnl']-replay(t,True,2)['net_pnl'],3.30)
 def test_gap(self):
  r=replay(synthetic()['trades'][4],True);self.assertLess(r['fills'][0]['price'],r['floor'])
 def test_common_exit(self):
  t=synthetic()['trades'][0];t['common_exit_at']=t['quotes'][1]['received_at'];r=replay(t,True)
  self.assertEqual(r['exit_reason'],'shared non-trail exit');self.assertEqual(r['fills'][0]['at'],t['quotes'][2]['received_at'])
 def test_collector_readonly_wal_and_rules(self):
  with tempfile.TemporaryDirectory() as tmp:
   home=Path(tmp);folder=home/'.scout-options';folder.mkdir();t=synthetic()['trades'][0]
   ledger=sqlite3.connect(folder/'research.sqlite');ledger.execute('PRAGMA journal_mode=WAL');ledger.executescript('CREATE TABLE ledger(id INTEGER,data TEXT);CREATE TABLE events(id INTEGER PRIMARY KEY,at TEXT,data TEXT);')
   order=dict(side='BUY',symbol='SPY_TEST',qty=1,created=t['entry_at']);ledger.execute('INSERT INTO ledger VALUES(1,?)',(json.dumps({'orders':{'one':order}}),));ledger.execute('INSERT INTO events VALUES(1,?,?)',(t['entry_at'],json.dumps(dict(kind='SIMULATED FILL',order_id='one',qty=1,price=1.))));ledger.commit()
   tape=sqlite3.connect(folder/'quotes.sqlite');tape.executescript('CREATE TABLE events(id INTEGER PRIMARY KEY,kind TEXT,symbol TEXT,market_at TEXT,received_at TEXT,bid REAL,ask REAL,bid_size REAL,ask_size REAL,feed TEXT);')
   for i,q in enumerate(t['quotes']):tape.execute('INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?,?)',(i,'quote','SPY_TEST',q['market_at'],q['received_at'],q['bid'],q['ask'],1,1,'opra'))
   (folder/'options-status.json').write_text(json.dumps({'research_settings':{'trail_fraction':.15}}))
   tape.commit();before=ledger.execute('SELECT data FROM ledger').fetchone()[0];data=collect(home)
   self.assertEqual(len(data['trades']),1);self.assertEqual(compare(data)['completed_pairs'],1);self.assertEqual(before,ledger.execute('SELECT data FROM ledger').fetchone()[0])
   (folder/'options-status.json').write_text(json.dumps({'research_settings':{'trail_fraction':.1}}))
   with self.assertRaises(ValueError):collect(home)
   (folder/'options-status.json').write_text(json.dumps({'trading_rules':{'risk_fraction':.01}}))
   self.assertTrue(compare(collect(home))['blocked']);self.assertEqual(compare(collect(home))['completed_pairs'],0)
   tape.close();ledger.close()
if __name__=='__main__':unittest.main()
