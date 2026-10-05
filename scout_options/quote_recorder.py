"""Bounded local quote tape and read-only data-access probe. No orders."""
import json
import math
import queue
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path


class QuoteRecorder:
    def __init__(self,path,max_bytes=256*1024*1024,queue_size=10000):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        self.max_bytes=max_bytes
        self.pending=queue.Queue(maxsize=queue_size)
        self.lock=threading.Lock();self.stop=threading.Event();self.ready=threading.Event()
        self.run_id=uuid.uuid4().hex
        self.state=dict(status='Starting',recorded_events=0,recorded_quotes=0,received_events=0,dropped_events=0,
                        last_quote_at=None,last_received_at=None,feed=None,
                        precision='SDK datetime precision; original nanoseconds are not preserved',
                        mode='Quote observations only; no fills or brokerage orders')
        self.thread=threading.Thread(target=self._write,daemon=True);self.thread.start()
        self.ready.wait(timeout=2)

    def snapshot(self):
        with self.lock:return dict(self.state,queued_events=self.pending.qsize())

    def _enqueue(self,row):
        with self.lock:
            self.state['received_events']+=1
            if self.state['status'] not in ('Starting','Recording'):
                self.state['dropped_events']+=1;return
        try:self.pending.put_nowait(row)
        except queue.Full:
            with self.lock:self.state['dropped_events']+=1

    def quote(self,q,feed,received_at,contract=None):
        try:
            if feed not in ('indicative','opra') or q.timestamp.tzinfo is None or received_at.tzinfo is None:
                raise ValueError('Missing provenance')
            values=tuple(float(getattr(q,k)) for k in ('bid_price','ask_price','bid_size','ask_size'))
            if not all(math.isfinite(v) and v>=0 for v in values):raise ValueError('Invalid quote')
            bid,ask,bs,as_=values
            metadata=dict(bid_exchange=str(getattr(q,'bid_exchange',None)),
                          ask_exchange=str(getattr(q,'ask_exchange',None)),
                          conditions=getattr(q,'conditions',None),
                          arrival_delay_seconds=(received_at-q.timestamp).total_seconds(),
                          quality='crossed' if bid>ask else 'zero_price_or_size' if min(values)<=0 else 'normal',
                          contract=contract)
            row=('quote',q.symbol,feed,q.timestamp.isoformat(),received_at.isoformat(),
                 bid,ask,bs,as_,json.dumps(metadata,default=str,allow_nan=False))
        except (ValueError,TypeError,AttributeError,OverflowError):
            with self.lock:self.state['received_events']+=1;self.state['dropped_events']+=1
            return
        self._enqueue(row)
        with self.lock:
            self.state.update(last_quote_at=q.timestamp.isoformat(),last_received_at=received_at.isoformat(),feed=feed)

    def signal(self,signal,feed,received_at):
        self._enqueue(('signal',signal.underlying,feed,signal.at.isoformat(),received_at.isoformat(),
                       None,None,None,None,json.dumps(dict(direction=signal.direction,reason=signal.reason))))

    def _size(self):
        return sum(p.stat().st_size for p in (self.path,Path(str(self.path)+'-wal')) if p.exists())

    def _write(self):
        db=None
        try:
            db=sqlite3.connect(self.path)
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY, run_id TEXT, kind TEXT, symbol TEXT, feed TEXT,
                    market_at TEXT, received_at TEXT, bid REAL, ask REAL,
                    bid_size REAL, ask_size REAL, metadata TEXT);
                CREATE INDEX IF NOT EXISTS events_symbol_time ON events(symbol,market_at);
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY, started_at TEXT, ended_at TEXT,
                    received INTEGER, recorded INTEGER, dropped INTEGER, status TEXT);
            ''')
            with db:
                db.execute('INSERT INTO runs VALUES (?,?,NULL,0,0,0,?)',
                           (self.run_id,datetime.now(timezone.utc).isoformat(),'Recording'))
            with self.lock:self.state['status']='Recording'
            self.ready.set()
            while not self.stop.is_set() or not self.pending.empty():
                batch=[]
                try:batch.append(self.pending.get(timeout=.2))
                except queue.Empty:continue
                while len(batch)<200:
                    try:batch.append(self.pending.get_nowait())
                    except queue.Empty:break
                if self._size()>=self.max_bytes:
                    with self.lock:
                        self.state['status']='Paused: local tape size limit reached'
                        self.state['dropped_events']+=len(batch)
                else:
                    with db:
                        db.executemany('INSERT INTO events VALUES (NULL,?,?,?,?,?,?,?,?,?,?,?)',
                                       [(self.run_id,*row) for row in batch])
                    with self.lock:
                        self.state['recorded_events']+=len(batch)
                        self.state['recorded_quotes']+=sum(row[0]=='quote' for row in batch)
                snapshot=self.snapshot()
                with db:
                    db.execute('UPDATE runs SET received=?,recorded=?,dropped=?,status=? WHERE run_id=?',
                        (snapshot['received_events'],snapshot['recorded_events'],snapshot['dropped_events'],snapshot['status'],self.run_id))
            snapshot=self.snapshot()
            with db:
                db.execute('UPDATE runs SET ended_at=?,received=?,recorded=?,dropped=?,status=? WHERE run_id=?',
                    (datetime.now(timezone.utc).isoformat(),snapshot['received_events'],snapshot['recorded_events'],
                     snapshot['dropped_events'],'Closed: '+snapshot['status'],self.run_id))
        except Exception as exc:
            with self.lock:self.state['status']='Recorder unavailable: '+type(exc).__name__
        finally:
            self.ready.set()
            if db:db.close()

    def close(self):
        with self.lock:
            if self.state['status']=='Recording':self.state['status']='Closing'
        self.stop.set();self.thread.join(timeout=5)
        if not self.thread.is_alive():
            with self.lock:
                if self.state['status']=='Closing':self.state['status']='Closed'


def probe(trading,options,now):
    """Latest-quote REST acceptance is not proof of stream entitlement or freshness."""
    from alpaca.trading.requests import GetOptionContractsRequest
    from alpaca.trading.enums import AssetStatus
    from alpaca.data.requests import OptionLatestQuoteRequest
    from alpaca.data.enums import OptionsFeed
    from zoneinfo import ZoneInfo
    day=now.astimezone(ZoneInfo('America/New_York')).date()
    response=trading.get_option_contracts(GetOptionContractsRequest(underlying_symbols=['QQQ'],
        status=AssetStatus.ACTIVE,expiration_date_gte=day+timedelta(days=7),
        expiration_date_lte=day+timedelta(days=30),limit=50))
    choices=[c for c in response.option_contracts if c.tradable and int(c.size)==100 and c.root_symbol=='QQQ']
    if not choices:return dict(status='No suitable contract returned; access not determined')
    symbol=max(choices,key=lambda c:int(c.open_interest or 0)).symbol
    result=dict(contract=symbol,checked_at=now.isoformat(),feeds={},
                limitation='REST probe only; stream access and current executable quotes are not verified. No subscriptions changed.')
    for feed in ('indicative','opra'):
        try:
            quotes=options.get_option_latest_quote(OptionLatestQuoteRequest(symbol_or_symbols=[symbol],feed=OptionsFeed(feed)))
            q=quotes.get(symbol)
            result['feeds'][feed]=dict(request='Accepted',quote_returned=q is not None,
                                      quote_at=q.timestamp.isoformat() if q else None)
        except Exception as exc:
            status=getattr(exc,'status_code',None)
            result['feeds'][feed]=dict(request='Unavailable',http=status if type(status) is int and 100<=status<=599 else None,
                                      error=type(exc).__name__)
    return result


def main():
    from .local_runner import private_load
    from alpaca.trading.client import TradingClient
    from alpaca.data.historical import OptionHistoricalDataClient
    try:
        key,secret=private_load()
        print(json.dumps(probe(TradingClient(key,secret,paper=True),OptionHistoricalDataClient(key,secret),
                               datetime.now(timezone.utc)),indent=2))
    except Exception as exc:
        print('Quote access check unavailable: '+type(exc).__name__+'. Credentials and remote bodies stay hidden.')


if __name__=='__main__':main()
