"""Staged paper-only order outbox. Not connected to the reference simulator.

Ambiguous submissions are reconciled by client ID, never automatically resubmitted.
This module does not compute signals, account budgets, or simulated fills.
"""
import fcntl
import json
import re
import sqlite3
import threading
import uuid
from decimal import Decimal, InvalidOperation
from pathlib import Path

TERMINAL = {'filled', 'canceled', 'expired', 'rejected'}
ACTIVE = {'new', 'accepted', 'pending_new', 'partially_filled', 'pending_cancel',
          'accepted_for_bidding', 'stopped', 'suspended', 'calculated'}


def decimal(value):
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError('Invalid number') from exc
    if not result.is_finite():
        raise ValueError('Non-finite number')
    return result


def field(obj, name):
    value = obj[name] if isinstance(obj, dict) else getattr(obj, name)
    return getattr(value, 'value', value)


class AlpacaPaperTransport:
    """No URL override and no live mode. Credentials come from host secrets."""
    def __init__(self, key, secret):
        from alpaca.trading.client import TradingClient
        self.client = TradingClient(key, secret, paper=True)

    def lookup(self, client_id):
        from alpaca.common.exceptions import APIError
        try:
            return self.client.get_order_by_client_id(client_id)
        except APIError as exc:
            # Only a definitive not-found is absence; timeout/auth/5xx propagates.
            if exc.status_code == 404:
                return None
            raise

    def submit(self, intent):
        from alpaca.trading.requests import LimitOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce, PositionIntent
        return self.client.submit_order(order_data=LimitOrderRequest(
            symbol=intent['symbol'], qty=intent['qty'],
            side=OrderSide.BUY if intent['side'] == 'BUY' else OrderSide.SELL,
            time_in_force=TimeInForce.DAY, client_order_id=intent['client_id'],
            limit_price=float(intent['limit']), extended_hours=False,
            position_intent=PositionIntent.BUY_TO_OPEN if intent['side'] == 'BUY'
            else PositionIntent.SELL_TO_CLOSE))

    def cancel(self, order_id):
        self.client.cancel_order_by_id(order_id)


class PaperOutbox:
    """Single-writer durable lifecycle, armed explicitly by the future coordinator.

    Reopening resets the arm. Requests are stored before any network call. No
    subsequent process can retry an uncertain submission automatically.
    """
    def __init__(self, path, transport):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.writer = path.with_suffix(path.suffix+'.lock').open('a')
        try:
            fcntl.flock(self.writer, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            self.writer.close()
            raise
        try:
            self.db = sqlite3.connect(path, check_same_thread=False)
            self.db.execute('PRAGMA journal_mode=WAL')
            self.db.execute('CREATE TABLE IF NOT EXISTS intents (id TEXT PRIMARY KEY, data TEXT NOT NULL)')
            self.db.commit()
        except BaseException:
            self.writer.close()
            raise
        self.lock = threading.RLock()
        self.transport = transport
        self.armed = False

    def close(self):
        self.db.close()
        self.writer.close()

    def records(self):
        with self.lock:
            return [json.loads(row[0]) for row in self.db.execute('SELECT data FROM intents ORDER BY rowid')]

    def _save(self, record):
        self.db.execute('INSERT OR REPLACE INTO intents VALUES (?,?)',
                        (record['client_id'], json.dumps(record, allow_nan=False)))
        self.db.commit()

    def stage(self, symbol, side, qty, limit):
        with self.lock:
            if not self.armed:
                raise RuntimeError('Paper gateway is not armed by an account coordinator')
            if not re.fullmatch(r'[A-Z]{1,6}[0-9]{6}[CP][0-9]{8}', symbol):
                raise ValueError('Standard option contract symbol required')
            if side not in {'BUY', 'SELL'} or type(qty) is not int or qty < 1 or decimal(limit) <= 0:
                raise ValueError('Invalid long-option limit order')
            if any(r['symbol'] == symbol and r['status'] not in TERMINAL for r in self.records()):
                raise RuntimeError('Unresolved order for contract')
            if side == 'SELL' and qty > self.owned_quantities().get(symbol, 0):
                raise RuntimeError('Sell exceeds broker-confirmed bot holdings')
            record = dict(client_id='scopt-'+uuid.uuid4().hex, symbol=symbol, side=side,
                          qty=qty, limit=str(decimal(limit)), status='STAGED',
                          filled_qty=0, filled_notional='0', broker_id=None, error=None)
            self._save(record)
            return record['client_id']

    def _apply(self, r, order):
        try:
            qty = decimal(field(order, 'filled_qty') or 0)
            status = field(order, 'status')
            if (field(order, 'client_order_id') != r['client_id'] or
                field(order, 'symbol') != r['symbol'] or
                str(field(order, 'side')).upper() != r['side'] or
                decimal(field(order, 'qty')) != r['qty'] or
                status not in ACTIVE | TERMINAL or
                qty != int(qty) or not r['filled_qty'] <= qty <= r['qty']):
                raise ValueError('Order identity, status or cumulative fill mismatch')
            avg = decimal(field(order, 'filled_avg_price') or 0)
            notional = qty*avg*100
            if qty and avg <= 0 or notional < decimal(r['filled_notional']):
                raise ValueError('Invalid cumulative fill cost')
            if status == 'filled' and qty != r['qty']:
                raise ValueError('Filled status without full quantity')
            r.update(status=status, filled_qty=int(qty), filled_notional=str(notional),
                     broker_id=str(field(order, 'id')), error=None)
        except (ValueError, KeyError, AttributeError, TypeError):
            r.update(status='REVIEW', error='Broker reconciliation mismatch')
            self.armed = False
        self._save(r)

    def sync(self):
        """Run outside the one-second manager. Caller owns risk and fresh account gates."""
        with self.lock:
            for r in self.records():
                if r['status'] in TERMINAL or r['status'] == 'REVIEW':
                    continue
                try:
                    found = self.transport.lookup(r['client_id'])
                    if found is not None:
                        self._apply(r, found)
                        continue
                    if r['status'] != 'STAGED' or not self.armed:
                        # NOT_FOUND after a possible send never permits blind retry.
                        r['error'] = 'Order not found; submission remains unresolved'
                        self._save(r)
                        continue
                    r['status'] = 'SUBMITTING'
                    self._save(r)  # Commit before network: crash recovery looks up ID.
                    self._apply(r, self.transport.submit(r))
                except Exception as exc:
                    r['error'] = type(exc).__name__  # No remote payloads or credentials.
                    self._save(r)
                    self.armed = False

    def cancel(self, client_id):
        with self.lock:
            r = next(r for r in self.records() if r['client_id'] == client_id)
            if r['status'] in TERMINAL:
                return
            if r['status'] == 'STAGED':
                r['status'] = 'canceled'
                self._save(r)
                return
            if not r['broker_id']:
                raise RuntimeError('Resolve ambiguous submission before canceling')
            r['status'] = 'CANCEL_REQUESTED'
            self._save(r)
            # A successful cancellation request is NOT a confirmed cancellation.
            try:
                self.transport.cancel(r['broker_id'])
            except Exception as exc:
                r['error'] = type(exc).__name__
                self.armed = False
                self._save(r)

    def owned_quantities(self):
        """Broker-confirmed cumulative fills only; cancellation races retain fills."""
        quantities = {}
        for r in self.records():
            quantities[r['symbol']] = quantities.get(r['symbol'], 0) + (
                r['filled_qty'] if r['side'] == 'BUY' else -r['filled_qty'])
        return quantities

    def verify_positions(self, broker_quantities):
        """Detect manual trades, expiry/exercise and unknown option positions.

        No quantity is adopted, fabricated, closed or exercised automatically.
        All account option holdings must agree before a coordinator can arm.
        """
        with self.lock:
            owned = {k: v for k, v in self.owned_quantities().items() if v}
            try:
                actual = {k: decimal(v) for k, v in broker_quantities.items() if decimal(v)}
            except (ValueError, TypeError, AttributeError):
                self.armed = False
                return False
            match = (all(v >= 0 for v in owned.values()) and owned == actual and
                     not any(r['status'] in {'SUBMITTING', 'REVIEW'} for r in self.records()))
            if not match:
                self.armed = False
            return match
