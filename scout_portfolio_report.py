"""Read-only display of portfolio research and input-recording status."""
import json
from html import escape
from pathlib import Path


def portfolio_html(logging_status=None,path=None):
    path=Path(path or Path(__file__).parent/'backtests/scout_portfolio_results.json')
    status=(logging_status or {}).get('status','NOT CHECKED')
    try:
        data=json.loads(path.read_text())
        rows=[r for r in data['results'] if r['sample']=='whole_reused' and r['cost_bps']==10 and r['delay_bars']==1]
        later={r['variant']:r for r in data['results'] if r['sample']=='later_reused' and r['cost_bps']==10 and r['delay_bars']==1}
        items=''.join(f'<li><b>{escape(r["variant"].replace("_"," "))}</b> · Return {r["return_pct"]:+.2f}% · '
                      f'Max decline {r["drawdown_pct"]:.2f}% · Later-period return {later[r["variant"]]["return_pct"]:+.2f}%</li>' for r in rows)
        return ('<section class="panel"><h2>Entry and portfolio comparison</h2>'
                '<p>Avoiding stretched entries is a candidate for further testing. Active rules are unchanged.</p>'
                f'<p>Decision-input recording: {escape(status)}.</p>'
                '<details><summary>Compare entries, exits and cash together</summary><ul>'+items+'</ul>'
                '<p class="sub">60 fixed-universe portfolio simulations, beginning in cash. Shown costs: 0.10% per side. '
                'Historical most-active lists are unavailable; completed daily inputs differ from live forming bars. '
                'The reused later period is not fresh validation. No-chasing improved the whole period and tied the later period. '
                'Input snapshots are now being saved for future validation.</p>'
                '<p><a style="color:#8dc5ff" href="https://github.com/Kodamafire/scout-mobile/blob/main/backtests/SCOUT_PORTFOLIO_REPORT.md">Full portfolio comparison</a></p></details></section>')
    except (OSError,ValueError,KeyError,TypeError):
        return '<section class="panel"><h2>Entry and portfolio comparison</h2><p>Report unavailable.</p></section>'
