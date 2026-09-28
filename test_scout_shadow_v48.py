from scout_shadow_v48 import market_regime, shadow_decision

def m(**x):
    d=dict(close=110,ema20=105,ema50=100,sma200=90,rsi14=58,macd=2,macd_signal=1.5,macd_rising=True,atr_pct=1.8,relative_volume=1.6,avg_dollar_volume=80000000,rs20=8)
    d.update(x); return d

def test_bull_market_prefers_long():
    r=market_regime(m()); x=shadow_decision(m(),r)
    assert r["name"]=="BULLISH TREND"
    assert x["direction"]=="LONG" and x["decision"]=="QUALIFIED"
    assert x["short_case"]["decision"].startswith("NO TRADE")

def test_bear_market_prefers_short():
    bear=m(close=90,ema20=95,ema50=100,sma200=110,rsi14=42,macd=-2,macd_signal=-1,macd_rising=False,rs20=-8)
    r=market_regime(bear); x=shadow_decision(bear,r)
    assert r["name"]=="BEARISH TREND"
    assert x["direction"]=="SHORT" and x["decision"]=="QUALIFIED"
    assert x["long_case"]["decision"].startswith("NO TRADE")

def test_extended_short_waits():
    bear=m(close=80,ema20=95,ema50=100,sma200=110,rsi14=25,macd=-2,macd_signal=-1,macd_rising=False,rs20=-8)
    r=market_regime(bear); x=shadow_decision(bear,r)
    assert x["short_case"]["entry_score"] < 70
