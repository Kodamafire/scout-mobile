"""Pure reference-price strategies for offline replay; no order API dependencies.

Settings are hypotheses, not optimized parameters or production trading rules.
"""
from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True)
class Variant:
    name: str
    adaptive: bool = False
    fast: bool = False
    partial: bool = False


VARIANTS = (
    Variant('volatility_only', adaptive=True),
    Variant('fast_timing_only', fast=True),
    Variant('volatility_and_fast', adaptive=True, fast=True),
    Variant('partial_volatility_and_fast', adaptive=True, fast=True, partial=True),
)


class ReferenceStrategy:
    def __init__(self, config, cost=.001, delay=1):
        if not 0 <= cost < 1 or delay < 1:
            raise ValueError('Nonnegative costs and at least one observation delay required')
        self.config, self.cost, self.delay = config, cost, delay
        self.state = None

    def step(self, o):
        price = float(o['price'])
        if not isfinite(price) or price <= 0:
            return None
        at = o['at']
        if self.state is None:
            self.state = dict(base=price, entry=price, peak=price, units=1/(price*(1+self.cost)),
                              cash=0., pending=None, fills=0, confirmations=0, confirm_at=None,
                              last_at=at, last_price=price, session=at.date(), reentries=0,
                              floor=price*.925, partial_done=False, last_exit=0., last_action='START')
            return self.value(price)
        r = self.state
        if at <= r['last_at']:
            return self.value(r['last_price'])
        previous = r['last_price']
        r['last_at'], r['last_price'] = at, price
        if at.date() != r['session']:
            r.update(session=at.date(),reentries=0,confirmations=0,confirm_at=None)
            if r['pending'] and r['pending']['side']=='BUY':
                r['pending']=None
        if r['pending']:
            r['pending']['remaining'] -= 1
            if r['pending']['remaining'] <= 0:
                side = r['pending']['side']
                if side == 'BUY':
                    bought = r['cash']/(price*(1+self.cost))
                    total = r['units']+bought
                    r['entry'] = (r['entry']*r['units']+price*bought)/total
                    r.update(units=total,cash=0.,peak=price,floor=r['entry']*.925,
                             partial_done=False,reentries=r['reentries']+1)
                else:
                    qty = r['units'] * (0.5 if side == 'HALF' else 1)
                    r['cash'] += qty*price*(1-self.cost)
                    r['units'] -= qty
                    r['last_exit'] = price
                    if side == 'HALF':
                        r['partial_done']=True
                r.update(pending=None,fills=r['fills']+1,confirmations=0,confirm_at=None,last_action=side)
            return self.value(price)
        gain = (price/r['entry']-1)*100
        r['peak'] = max(r['peak'],price)
        peak_gain = (r['peak']/r['entry']-1)*100
        fast = o.get('fast')
        weak = fast['weak'] if self.config.fast and fast is not None else o['signal']['entry_score'] < 70 or o['score'] >= 3
        activation = max(1.,.75*o['atr']) if self.config.adaptive else 1.
        active = peak_gain >= activation
        if self.config.adaptive:
            if active:
                trail = max(.75,o['atr']*(.75 if weak else 1.5))
                candidate=r['peak']*(1-trail/100)
                r['floor']=max(r['floor'],candidate)
            exit_signal = price <= r['floor']
            # Partial variant banks half on a weaker, tighter volatility warning.
            warning = active and weak and (1-price/r['peak'])*100 >= max(.75,.5*o['atr'])
        else:
            drop=(1-price/r['peak'])*100
            exit_signal=active and (gain <= .5*peak_gain or (drop>=.75 and weak))
            warning=exit_signal
        if r['units'] and gain <= -7.5:
            self.queue('SELL');return self.value(price)
        if r['units']:
            if self.config.partial and not r['partial_done'] and warning:
                self.queue('HALF');return self.value(price)
            if exit_signal:
                self.queue('SELL');return self.value(price)
        if r['cash'] > 0 and r['reentries'] < 2:
            if self.config.fast:
                # Intraday recovery must reclaim the last sale and rise on each confirmation.
                qualify=(fast is not None and fast['qualified'] and price>previous
                         and price>=r['last_exit'] and o['regime']!='BEARISH TREND')
            else:
                qualify=o['signal']['decision']=='QUALIFIED'
            if not qualify:
                r.update(confirmations=0,confirm_at=None)
            elif r['confirm_at'] is None or (at-r['confirm_at']).total_seconds()>=900:
                r['confirmations']+=1;r['confirm_at']=at
                if r['confirmations']>=2:
                    self.queue('BUY')
        return self.value(price)

    def queue(self, side):
        self.state['pending']={'side':side,'remaining':self.delay}
        self.state['last_action']=side+' SIGNAL'

    def value(self, price):
        r=self.state
        return r['cash']+r['units']*price*(1-self.cost)


def fast_features(frame):
    """Causal 15-minute indicators. The row uses only that row and previous bars."""
    close=frame['close'].astype(float)
    ema9=close.ewm(span=9,adjust=False).mean()
    ema21=close.ewm(span=21,adjust=False).mean()
    macd=close.ewm(span=12,adjust=False).mean()-close.ewm(span=26,adjust=False).mean()
    signal=macd.ewm(span=9,adjust=False).mean()
    avgvolume=frame['volume'].shift(1).rolling(20).mean()
    output=[]
    for i in range(len(frame)):
        if i<30:
            output.append(None);continue
        weak=sum([close.iloc[i]<ema9.iloc[i],ema9.iloc[i]<ema21.iloc[i],macd.iloc[i]<signal.iloc[i]])>=2
        qualified=(close.iloc[i]>ema9.iloc[i]>ema21.iloc[i]
                   and macd.iloc[i]>signal.iloc[i] and macd.iloc[i]>macd.iloc[i-1]
                   and frame['volume'].iloc[i]>=avgvolume.iloc[i])
        output.append({'weak':bool(weak),'qualified':bool(qualified)})
    return output
