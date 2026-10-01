"""Read-only snapshots of decision inputs, with a strict account-field allowlist."""
import hashlib
import json
from pathlib import Path


def account_snapshot(account,positions,open_orders,state):
    return dict(equity=float(account.equity),cash=float(account.cash),
                positions=[dict(symbol=str(p.symbol),qty=float(p.qty),avg_entry_price=float(getattr(p,'avg_entry_price',0) or 0),
                                market_value=float(p.market_value),unrealized_plpc=float(p.unrealized_plpc)) for p in positions],
                open_order_symbols=sorted(open_orders),
                decision_state={k:state.get(k) for k in ('high_water','warning_streak','profit_floor','legacy_profit_floor',
                                                       'exit_policy','last_confirmation_check','upgrade_challenge')})


def record_inputs(folder,cycle,observed_at,market_open,config,inputs,source_paths=()):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    sources={Path(p).name:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in source_paths}
    payload=dict(version=1,cycle=cycle,at=observed_at.isoformat(),market_open=bool(market_open),
                 config=config,source_hashes=sources,inputs=inputs,
                 scope='Decision inputs and reference data only; not an executable quote or fill log.')
    encoded=json.dumps(payload,allow_nan=False,separators=(',',':'))
    path=folder/(observed_at.date().isoformat()+'.jsonl')
    # Scheduled retries should not duplicate the same cycle.
    if path.exists():
        with path.open() as f:
            if any(json.loads(line).get('cycle')==cycle for line in f if line.strip()):
                return dict(status='ALREADY RECORDED',file=path.name)
    with path.open('a') as f:f.write(encoded+'\n')
    return dict(status='RECORDED',file=path.name,bytes=len(encoded.encode()))
