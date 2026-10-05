"""Calls/puts selection and durable simulated execution. No broker imports."""
import json
import fcntl
import math
import sqlite3
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


def finite(*values):
    return all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in values)


@dataclass(frozen=True)
class Config:
    quote_max_age: float = 3
    signal_max_age: float = 90
    min_dte: int = 7
    max_dte: int = 30
    max_spread_fraction: float = .10
    min_open_interest: int = 100
    premium_risk_fraction: float = .01
    max_total_premium_fraction: float = .05
    cash_reserve_fraction: float = .35
    max_positions: int = 2
    stop_fraction: float = .25
    trail_activation: float = .20
    trail_fraction: float = .15
    session_loss_fraction: float = .02
    entry_timeout: float = 30
    exit_timeout: float = 15
    cooldown_seconds: float = 300
    scan_seconds: float = 60
    tick_seconds: float = 1

    def __post_init__(self):
        if not all(finite(v) and v > 0 for v in asdict(self).values()):
            raise ValueError('Research settings must be finite and positive.')
        if self.min_dte > self.max_dte or self.tick_seconds != 1:
            raise ValueError('Invalid expiry range or management cadence.')
        for name in ('max_spread_fraction', 'premium_risk_fraction', 'max_total_premium_fraction',
                     'cash_reserve_fraction', 'stop_fraction', 'trail_fraction', 'session_loss_fraction'):
            if getattr(self, name) >= 1:
                raise ValueError('Fractions must be below one.')


@dataclass(frozen=True)
class Quote:
    symbol: str
    bid: float
    ask: float
    bid_size: int
    ask_size: int
    at: datetime
    feed: str = 'opra'

    def usable(self, now, cfg, allow_synthetic=False):
        if self.at.tzinfo is None or now.tzinfo is None:
            return False
        age = (now-self.at).total_seconds()
        return (self.feed == 'opra' or allow_synthetic and self.feed == 'synthetic') and (
            finite(self.bid, self.ask, self.bid_size, self.ask_size) and
            self.bid > 0 and self.ask >= self.bid and self.bid_size > 0 and self.ask_size > 0 and
            self.bid_size == int(self.bid_size) and self.ask_size == int(self.ask_size) and
            0 <= age <= cfg.quote_max_age)


@dataclass(frozen=True)
class Contract:
    symbol: str
    underlying: str
    kind: str
    expiry: date
    delta: float
    iv: float
    open_interest: int
    metadata_at: datetime
    multiplier: int = 100
    tradable: bool = True


@dataclass(frozen=True)
class Signal:
    underlying: str
    direction: str
    at: datetime
    reason: str


def select_contracts(contracts, quotes, signal, now, cfg, allow_synthetic=False):
    """Return ranked eligible contracts; calls and puts have symmetric filters."""
    if signal.direction not in ('CALL', 'PUT') or signal.at.tzinfo is None:
        return []
    if not 0 <= (now-signal.at).total_seconds() <= cfg.signal_max_age:
        return []
    selected = []
    for c in contracts:
        q = quotes.get(c.symbol)
        if (not q or not q.usable(now, cfg, allow_synthetic) or q.symbol != c.symbol
            or (q.ask-q.bid)/((q.ask+q.bid)/2) > cfg.max_spread_fraction):
            continue
        if (c.metadata_at.tzinfo is None or not 0 <= (now-c.metadata_at).total_seconds() <= 120
            or not finite(c.delta, c.iv, c.open_interest) or c.iv <= 0 or c.iv > 2
            or c.multiplier != 100 or not c.tradable or c.kind.upper() != signal.direction
            or c.underlying != signal.underlying or c.open_interest < cfg.min_open_interest
            or not cfg.min_dte <= (c.expiry-now.date()).days <= cfg.max_dte
            or not .35 <= abs(c.delta) <= .65
            or signal.direction == 'CALL' and c.delta <= 0
            or signal.direction == 'PUT' and c.delta >= 0):
            continue
        selected.append(c)
    return sorted(selected, key=lambda c: ((quotes[c.symbol].ask-quotes[c.symbol].bid)/quotes[c.symbol].ask,
                                          abs(abs(c.delta)-.5), -c.open_interest, c.symbol))


def directional_signal(underlying, bars, now):
    """Experimental minute-bar trend + participation; no claimed trading edge.

    Bars: (timezone-aware opening timestamp, close, volume). Only completed,
    consecutive one-minute bars from today's session are considered.
    """
    rows = sorted(bars, key=lambda b: b[0])
    if len(rows) < 21 or now.tzinfo is None:
        return None
    rows = rows[-21:]
    if any(at.tzinfo is None or not finite(close, volume) or close <= 0 or volume < 0
           or at+timedelta(minutes=1) > now for at, close, volume in rows):
        return None
    if any((b[0]-a[0]).total_seconds() != 60 for a, b in zip(rows, rows[1:])):
        return None
    completed_at = rows[-1][0]+timedelta(minutes=1)
    if not 0 <= (now-completed_at).total_seconds() <= 90:
        return None
    closes = [r[1] for r in rows]
    volumes = [r[2] for r in rows]
    baseline = sum(volumes[:-1])/20
    if baseline <= 0 or volumes[-1]/baseline < 1.1:
        return None
    def ema(period):
        result = closes[0]
        for close in closes[1:]: result += 2/(period+1)*(close-result)
        return result
    fast, slow = ema(5), ema(15)
    if closes[-1] > fast > slow and closes[-1] > closes[-3]:
        return Signal(underlying, 'CALL', completed_at, 'Completed minute trend rising; volume confirmed.')
    if closes[-1] < fast < slow and closes[-1] < closes[-3]:
        return Signal(underlying, 'PUT', completed_at, 'Completed minute trend falling; volume confirmed.')
    return None


class PaperEngine:
    """Isolated reference ledger, not Alpaca paper fills or executable prices.

    Durable order intents precede simulated fills. One writer process and a
    transaction per event; a stream quote can fill at most its displayed size.
    """
    def __init__(self, path, capital, cfg=None, allow_synthetic=False):
        if not finite(capital) or capital <= 0:
            raise ValueError('Explicit positive research capital required.')
        self.cfg = cfg or Config()
        self.allow_synthetic = allow_synthetic
        self.lock = threading.RLock()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.writer_lock = open(str(path)+'.lock', 'a')
        try:
            fcntl.flock(self.writer_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.writer_lock.close()
            raise RuntimeError('Another options service already owns this ledger.')
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS ledger (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, at TEXT, data TEXT)')
        row = self.db.execute('SELECT data FROM ledger WHERE id=1').fetchone()
        self.state = json.loads(row[0]) if row else dict(capital=capital, cash=capital,
            positions={}, orders={}, seen={}, cooldown={}, session=None, session_equity=None,
            paused=False, heartbeat=None, alerts=[])
        if self.state['capital'] != capital:
            self.db.close()
            self.writer_lock.close()
            raise ValueError('Existing ledger capital differs; use its original capital.')
        self.state['accepting'] = False
        self.quotes = {}
        self.signals = {}
        self._save()

    def _save(self):
        self.db.execute('INSERT OR REPLACE INTO ledger VALUES (1,?)',
                        (json.dumps(self.state, allow_nan=False),))
        self.db.commit()

    def _event(self, now, kind, **data):
        self.db.execute('INSERT INTO events(at,data) VALUES (?,?)',
                        (now.isoformat(), json.dumps(dict(kind=kind, **data))))

    def _alert(self, now, message):
        if message not in self.state['alerts']:
            self.state['alerts'].append(message)
            self.state['alerts'] = self.state['alerts'][-100:]
            self._event(now, 'ALERT', reason=message)

    def _active(self, symbol=None):
        return [o for o in self.state['orders'].values()
                if o['status'] == 'OPEN' and (symbol is None or o['symbol'] == symbol)]

    def _equity(self):
        return self.state['cash']+sum(p['qty']*p['last_bid']*100 for p in self.state['positions'].values())

    def enter(self, contracts, signal, now, external=None):
        """Paper-only intent. Optional shared account snapshot blocks stale data.

        external contains equity, cash, gross_exposure, pending_orders, at.
        Production paper wiring requires this snapshot; demo uses isolated capital.
        """
        with self.lock:
            heartbeat = self.state.get('heartbeat')
            if (not self.state.get('accepting') or not heartbeat
                or not 0 <= (now-datetime.fromisoformat(heartbeat)).total_seconds() <= 2
                or self.state['session'] != now.date().isoformat() or self.state['paused']):
                return 'SESSION NOT READY OR PAUSED'
            selected = select_contracts(contracts, self.quotes, signal, now, self.cfg, self.allow_synthetic)
            if not selected:
                return 'NO ELIGIBLE CONTRACT'
            if signal.underlying in self.state['cooldown'] and now < datetime.fromisoformat(self.state['cooldown'][signal.underlying]):
                return 'COOLDOWN'
            exposed = list(self.state['positions'].values())+self._active()
            if any(p['underlying'] == signal.underlying for p in exposed):
                return 'DUPLICATE UNDERLYING'
            if len({p['symbol'] for p in exposed}) >= self.cfg.max_positions:
                return 'POSITION LIMIT'
            equity = self._equity()
            reserved = sum((o['qty']-o['filled'])*o['limit']*100 for o in self._active() if o['side'] == 'BUY')
            available = self.state['cash']-reserved-equity*self.cfg.cash_reserve_fraction
            premium = sum(p['qty']*p['entry']*100 for p in self.state['positions'].values())+reserved
            available = min(available, equity*self.cfg.premium_risk_fraction,
                            equity*self.cfg.max_total_premium_fraction-premium)
            if external is not None:
                if (external['at'].tzinfo is None or not 0 <= (now-external['at']).total_seconds() <= 15
                    or not finite(external['equity'], external['cash'], external['gross_exposure'])
                    or external['equity'] <= 0 or external['cash'] < 0
                    or external['gross_exposure'] < 0 or external['pending_orders']):
                    return 'SHARED ACCOUNT UNAVAILABLE OR PENDING'
                available = min(available, external['cash']-reserved-external['equity']*self.cfg.cash_reserve_fraction,
                                external['equity']*(1-self.cfg.cash_reserve_fraction)-external['gross_exposure']-reserved)
            affordable = [c for c in selected if self.quotes[c.symbol].ask*100 <= available]
            if not affordable:
                return 'PREMIUM BUDGET TOO SMALL'
            contract = affordable[0]
            quote = self.quotes[contract.symbol]
            qty = int(max(0, available)//(quote.ask*100))
            self._intent(contract.symbol, contract.underlying, contract.expiry.isoformat(), 'BUY', qty,
                         quote.ask, now, signal.reason, contract.kind)
            self._save()
            return 'SIMULATED BUY PENDING'

    def _intent(self, symbol, underlying, expiry, side, qty, price, now, reason, kind=None):
        oid = 'scopt-'+uuid.uuid4().hex[:24]
        self.state['orders'][oid] = dict(id=oid, symbol=symbol, underlying=underlying,
            expiry=expiry, kind=kind, side=side, qty=qty, filled=0, limit=price,
            created=now.isoformat(), status='OPEN', reason=reason)
        self._event(now, 'INTENT', order_id=oid, symbol=symbol, side=side, qty=qty, reason=reason)
        return oid

    def on_quote(self, quote, received_at):
        with self.lock:
            if not quote.usable(received_at, self.cfg, self.allow_synthetic):
                return False
            prior = self.state['seen'].get(quote.symbol)
            if prior and quote.at <= datetime.fromisoformat(prior):
                return False
            self.quotes[quote.symbol] = quote
            self.state['seen'][quote.symbol] = quote.at.isoformat()
            p = self.state['positions'].get(quote.symbol)
            if p:
                p['last_bid'] = quote.bid
                p['peak_bid'] = max(p['peak_bid'], quote.bid)
            for o in self._active(quote.symbol):
                if quote.at <= datetime.fromisoformat(o['created']):
                    continue  # Never fill against the signal's quote.
                buy = o['side'] == 'BUY'
                price = quote.ask if buy else quote.bid
                if buy and price > o['limit'] or not buy and price < o['limit']:
                    continue
                qty = min(o['qty']-o['filled'], quote.ask_size if buy else quote.bid_size)
                if buy:
                    if qty*price*100 > self.state['cash']:
                        self._alert(received_at, 'Simulated fill exceeds cash; reconciliation needed.')
                        continue
                    self.state['cash'] -= qty*price*100
                    p = self.state['positions'].setdefault(o['symbol'], dict(symbol=o['symbol'],
                        underlying=o['underlying'], expiry=o['expiry'], kind=o.get('kind'), qty=0, entry=0,
                        last_bid=quote.bid, peak_bid=quote.bid))
                    p['entry'] = (p['entry']*p['qty']+price*qty)/(p['qty']+qty)
                    p['qty'] += qty
                else:
                    p = self.state['positions'].get(o['symbol'])
                    if not p or qty > p['qty']:
                        self._alert(received_at, 'Simulated sell quantity mismatch; reconciliation needed.')
                        continue
                    self.state['cash'] += qty*price*100
                    p['qty'] -= qty
                    if p['qty'] == 0:
                        del self.state['positions'][o['symbol']]
                        self.state['cooldown'][o['underlying']] = (received_at+timedelta(seconds=self.cfg.cooldown_seconds)).isoformat()
                o['filled'] += qty
                if o['filled'] == o['qty']: o['status'] = 'FILLED'
                self._event(received_at, 'SIMULATED FILL', order_id=o['id'], qty=qty, price=price)
            self._save()
            return True

    def tick(self, now, market_open, close_at):
        with self.lock:
            self.state['heartbeat'] = now.isoformat()
            self.state['accepting'] = False
            if not market_open:
                if self.state['positions']: self._alert(now, 'Market closed with simulated options still held.')
                self._save()
                return
            if close_at.tzinfo is None or now.tzinfo is None or close_at <= now:
                raise ValueError('Fresh exchange close timestamp required.')
            session = now.date().isoformat()
            equity = self._equity()
            if self.state['session'] != session:
                self.state.update(session=session, session_equity=equity, paused=False)
            if equity <= self.state['session_equity']*(1-self.cfg.session_loss_fraction):
                self.state['paused'] = True
            cutoff = now >= close_at-timedelta(minutes=30)
            self.state['accepting'] = not cutoff and not self.state['paused']
            for o in self._active():
                age = (now-datetime.fromisoformat(o['created'])).total_seconds()
                if o['side'] == 'BUY' and (age >= self.cfg.entry_timeout or self.state['paused'] or cutoff):
                    o['status'] = 'CANCELED'
                    self._event(now, 'SIMULATED CANCEL', order_id=o['id'])
                elif o['side'] == 'SELL' and age >= self.cfg.exit_timeout:
                    self._alert(now, 'Exit remains unfilled: '+o['symbol'])
                    quote = self.quotes.get(o['symbol'])
                    if quote and quote.usable(now, self.cfg, self.allow_synthetic):
                        o['status'] = 'CANCELED'
                        self._intent(o['symbol'], o['underlying'], o['expiry'], 'SELL',
                                     o['qty']-o['filled'], quote.bid, now, 'REPRICE UNFILLED EXIT')
            for symbol, p in list(self.state['positions'].items()):
                q = self.quotes.get(symbol)
                if not q or not q.usable(now, self.cfg, self.allow_synthetic):
                    self._alert(now, 'Quote stale/unavailable for held option: '+symbol)
                    continue
                signal = self.signals.get(p['underlying'])
                invalidated = (signal is not None and 0 <= (now-signal.at).total_seconds() <= self.cfg.signal_max_age
                               and signal.direction != p.get('kind'))
                gain = q.bid/p['entry']-1
                peak_gain = p['peak_bid']/p['entry']-1
                reason = ('SESSION CUTOFF' if cutoff else 'EXPIRATION' if date.fromisoformat(p['expiry']) <= now.date()
                          else 'SESSION LOSS' if self.state['paused'] else 'SETUP INVALIDATED' if invalidated else 'PREMIUM LOSS' if gain <= -self.cfg.stop_fraction
                          else 'RUNNER TRAIL' if peak_gain >= self.cfg.trail_activation and q.bid <= p['peak_bid']*(1-self.cfg.trail_fraction)
                          else None)
                if reason:
                    for buy in self._active(symbol):
                        if buy['side'] == 'BUY':
                            buy['status'] = 'CANCELED'
                            self._event(now, 'SIMULATED CANCEL', order_id=buy['id'])
                if reason and not self._active(symbol):
                    self._intent(symbol, p['underlying'], p['expiry'], 'SELL', p['qty'], q.bid, now, reason)
            self._save()

    def update_signals(self, signals):
        with self.lock:
            self.signals = {s.underlying: s for s in signals}

    def snapshot(self):
        with self.lock:
            return json.loads(json.dumps(dict(mode='ISOLATED REFERENCE SIMULATION — NO BROKER ORDERS',
                equity=self._equity(), **self.state)))

    def close(self):
        self.db.close()
        self.writer_lock.close()
