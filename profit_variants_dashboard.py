"""Display archived research results; no market or broker access."""
import json
from html import escape
from pathlib import Path

LABELS = {'original_tight':'Original tight rule','volatility_only':'Volatility protection',
          'fast_timing_only':'Faster timing','volatility_and_fast':'Volatility + faster timing',
          'partial_volatility_and_fast':'Partial sale + both'}


def variants_html(path=None):
    path=Path(path) if path else Path(__file__).parent/'backtests/profit_variants_results.json'
    try:
        report=json.loads(path.read_text())
        rows=report['summary']
        selected=[r for r in rows if r['sample']=='five_session' and r['cost_bps']==10 and r['delay_bars']==1]
        later={r['variant']:r for r in rows if r['sample']=='later_reused' and r['cost_bps']==10 and r['delay_bars']==1}
        items=[]
        for r in selected:
            items.append(f'<li><b>{escape(LABELS.get(r["variant"],r["variant"]))}</b><br>'
                         f'Five-session mean: {r["return_pct"]:+.2f}% vs holding {r["hold_pct"]:+.2f}% · '
                         f'Later-period mean: {later[r["variant"]]["return_pct"]:+.2f}% vs holding '
                         f'{later[r["variant"]]["hold_pct"]:+.2f}%</li>')
        return ('<section class="panel"><h2>Profit strategy research</h2>'
                '<p>Built and tested. No new version has earned a change to Scout’s active rules.</p>'
                '<details><summary>Compare the historical variants</summary><ul>'+''.join(items)+'</ul>'
                '<p class="sub">Archived 15-minute reference-price simulations across nine symbols and about two months. '
                'Shown costs: 0.10% per side, next-observation fills. Also tested higher costs and delayed fills. '
                'Previously inspected periods, not fresh validation. These are correlated cases, not independent trades '
                'or actual portfolio returns. Some trials reduced drawdowns but all trailed holding on average in these samples.</p>'
                '<p><a style="color:#8dc5ff" href="https://github.com/Kodamafire/scout-mobile/blob/main/backtests/PROFIT_VARIANTS_REPORT.md">Full comparison and limitations</a></p>'
                '</details></section>')
    except (OSError,ValueError,KeyError,TypeError):
        return '<section class="panel"><h2>Profit strategy research</h2><p>Historical comparison unavailable.</p></section>'
