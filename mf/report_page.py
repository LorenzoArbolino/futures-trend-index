"""
The HTML report (output/report.html) and the README results block.

Every number and every claim on the page is computed from the same context the
CLI uses (panel.load_context); nothing is typed in by hand.
"""

import datetime as dt
import json
import statistics as stats

import config as C
from mf import metrics, model, panel as P, report as ch, research as R, store


def _css():
    def block(p):
        return "\n".join(f"    --{k}: {v};" for k, v in p.items())
    return f"""
:root {{ color-scheme: light; }}
.viz-root {{
{block(ch.LIGHT)}
}}
@media (prefers-color-scheme: dark) {{
  :root:where(:not([data-theme="light"])) .viz-root {{
    color-scheme: dark;
{block(ch.DARK)}
  }}
}}
:root[data-theme="dark"] .viz-root {{
  color-scheme: dark;
{block(ch.DARK)}
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0; padding: 0 20px 80px;
  background: var(--plane); color: var(--ink);
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
  font-size: 15px; line-height: 1.55;
}}
.wrap {{ max-width: 1000px; margin: 0 auto; }}
ol.steps li {{ margin: 0 0 10px; }}
header {{ padding: 40px 0 8px; }}
h1 {{ font-size: 30px; line-height: 1.2; margin: 0 0 6px; letter-spacing: -0.02em; }}
h2 {{ font-size: 21px; margin: 44px 0 6px; letter-spacing: -0.01em; }}
h3 {{ font-size: 15px; margin: 26px 0 4px; }}
p, li {{ color: var(--ink2); }}
.sub {{ color: var(--muted); font-size: 14px; }}
.card {{
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 12px; padding: 20px 22px 24px; margin: 16px 0 8px;
}}
.chart {{ width: 100%; height: auto; display: block; overflow: visible; }}
.grid {{ stroke: var(--grid); stroke-width: 1; }}
.axis {{ stroke: var(--axis); stroke-width: 1; }}
.tick {{ fill: var(--muted); font-size: 11px; font-variant-numeric: tabular-nums; }}
.rowlab {{ fill: var(--ink2); font-size: 12px; }}
.vallab {{ fill: var(--ink2); font-size: 11px; font-variant-numeric: tabular-nums; }}
.endlab {{ fill: var(--ink2); font-size: 11px; }}
.anno {{ fill: var(--muted); font-size: 10px; }}
.crosshair {{ stroke: var(--axis); stroke-width: 1; pointer-events: none; }}
.hovdot {{ pointer-events: none; }}
.hit:focus {{ outline: 2px solid var(--s1); outline-offset: -2px; }}
.legend {{ display: flex; flex-wrap: wrap; gap: 18px; margin: 4px 0 12px; }}
.lg {{ display: flex; align-items: center; gap: 7px; font-size: 13px; color: var(--ink2); }}
.sw {{ width: 11px; height: 11px; border-radius: 3px; flex: none; }}
.stats {{ display: flex; flex-wrap: wrap; gap: 12px; margin: 18px 0 4px; }}
.stat {{
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 10px; padding: 14px 18px; min-width: 168px; flex: 1 1 168px;
}}
.slab {{ font-size: 12px; color: var(--muted); }}
.sval {{ font-size: 27px; line-height: 1.15; margin-top: 2px; color: var(--ink); }}
.ssub {{ font-size: 12px; color: var(--muted); }}
table.data {{
  border-collapse: collapse; width: 100%; font-size: 13px;
  font-variant-numeric: tabular-nums; margin-top: 10px;
}}
table.data caption {{ text-align: left; color: var(--muted); padding-bottom: 6px; font-size: 12px; }}
table.data th {{
  text-align: right; font-weight: 600; color: var(--ink2);
  border-bottom: 1px solid var(--axis); padding: 6px 9px; white-space: nowrap;
}}
table.data th:first-child, table.data td:first-child {{ text-align: left; }}
table.data td {{ padding: 5px 9px; border-bottom: 1px solid var(--grid); text-align: right; }}
details.tbl {{ margin-top: 12px; }}
details.tbl summary {{
  cursor: pointer; color: var(--ink2); font-size: 13px; padding: 6px 0;
  user-select: none;
}}
.tip {{
  position: fixed; pointer-events: none; opacity: 0; z-index: 50;
  background: var(--surface); color: var(--ink);
  border: 1px solid var(--border); border-radius: 8px;
  padding: 8px 11px; font-size: 12px; line-height: 1.45;
  box-shadow: 0 6px 22px rgba(0,0,0,0.16); max-width: 300px;
  font-variant-numeric: tabular-nums; transition: opacity .09s;
}}
.tip b {{ color: var(--ink); font-weight: 600; }}
.note {{
  background: var(--surface); border: 1px solid var(--border);
  border-left: 3px solid var(--s1); border-radius: 10px;
  padding: 14px 18px; margin: 18px 0; font-size: 14px; color: var(--ink2);
}}
.toggle {{
  position: fixed; top: 14px; right: 16px; z-index: 60;
  background: var(--surface); color: var(--ink2);
  border: 1px solid var(--border); border-radius: 8px;
  padding: 7px 13px; font-size: 13px; cursor: pointer; font-family: inherit;
}}
code {{ font-family: ui-monospace, "Cascadia Code", Consolas, monospace; font-size: 13px; }}
footer {{ margin-top: 56px; padding-top: 18px; border-top: 1px solid var(--grid);
  color: var(--muted); font-size: 12.5px; }}
"""


_JS = r"""
const TIP = document.getElementById('tip');
function showTip(h, x, y) {
  TIP.innerHTML = h; TIP.style.opacity = '1';
  const r = TIP.getBoundingClientRect();
  let lx = x + 14, ly = y + 14;
  if (lx + r.width > window.innerWidth - 8) lx = x - r.width - 14;
  if (ly + r.height > window.innerHeight - 8) ly = y - r.height - 14;
  TIP.style.left = lx + 'px'; TIP.style.top = ly + 'px';
}
function hideTip() { TIP.style.opacity = '0'; }
document.querySelectorAll('rect.hit').forEach(function (el) {
  const tip = el.getAttribute('data-tip');
  el.addEventListener('mousemove', function (e) { showTip(tip, e.clientX, e.clientY); });
  el.addEventListener('mouseleave', hideTip);
  el.addEventListener('focus', function () {
    const b = el.getBoundingClientRect();
    showTip(tip, b.left + b.width / 2, b.top);
  });
  el.addEventListener('blur', hideTip);
});
function wireLine(id, cfg) {
  const svg = document.getElementById(id);
  const hit = document.getElementById(id + '-hit');
  const cross = document.getElementById(id + '-cross');
  if (!svg || !hit) return;
  const dots = cfg.series.map(function (_, i) {
    return document.getElementById(id + '-dot' + i);
  });
  const n = cfg.months.length;
  hit.addEventListener('mousemove', function (e) {
    const r = svg.getBoundingClientRect();
    const x = (e.clientX - r.left) / r.width * svg.viewBox.baseVal.width;
    let i = Math.round((x - cfg.ml) / cfg.pw * (n - 1));
    i = Math.max(0, Math.min(n - 1, i));
    const px = cfg.ml + i / (n - 1) * cfg.pw;
    cross.setAttribute('x1', px); cross.setAttribute('x2', px);
    cross.style.opacity = '1';
    let rows = '<b>' + cfg.months[i].slice(0, 7) + '</b>';
    cfg.series.forEach(function (s, si) {
      const nm = s[0], slot = s[1], v = s[2][i];
      const y = cfg.yOf[si][i];
      if (v === null || v === undefined || y === null) {
        dots[si].style.opacity = '0';
        return;
      }
      dots[si].setAttribute('cx', px);
      dots[si].setAttribute('cy', y);
      dots[si].style.opacity = '1';
      const txt = cfg.pct ? (v * 100).toFixed(1) + '%' : v.toFixed(2) + 'x';
      rows += '<br><span style="display:inline-block;width:9px;height:9px;'
           + 'border-radius:2px;background:var(--' + slot + ');margin-right:6px">'
           + '</span>' + nm + ' &nbsp;<b>' + txt + '</b>';
    });
    showTip(rows, e.clientX, e.clientY);
  });
  hit.addEventListener('mouseleave', function () {
    hideTip();
    cross.style.opacity = '0';
    dots.forEach(function (d) { d.style.opacity = '0'; });
  });
}
const btn = document.getElementById('themebtn');
let cur = null;
btn.addEventListener('click', function () {
  const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
  if (cur === null) cur = prefersDark ? 'light' : 'dark';
  else cur = (cur === 'dark') ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', cur);
  btn.textContent = (cur === 'dark') ? 'Light mode' : 'Dark mode';
});
"""


def _legend(items):
    return '<div class="legend">' + "".join(
        f'<span class="lg"><span class="sw" style="background:var(--{slot})">'
        f'</span>{ch.esc(name)}</span>' for name, slot in items
    ) + "</div>"


# ---------------------------------------------------------------------------
def build():
    conn = store.connect(C.DB_PATH)
    ctx = P.load_context(conn)
    conn.close()

    base, months, panel = ctx["base"], ctx["months"], ctx["panel"]
    spx, aqr, info, stacked = ctx["spx"], ctx["aqr"], ctx["info"], ctx["stacked"]
    boot = (C.BOOTSTRAP_SAMPLES, C.BOOTSTRAP_SEED, C.BOOTSTRAP_BLOCK)
    summ = {r.label: metrics.summarise(r.months, r.excess, r.total, r.turnover,
                                       spx, bootstrap=boot) for r in ctx["variants"]}
    bs = summ[base.label]
    sp = P.subperiod_stats(base.months, base.excess)
    vt = C.HEADLINE["target_vol"]
    gross = [sum(abs(base.positions[s][m]) for s in base.positions if m in base.positions[s])
             for m in base.months]
    gross_med = sorted(gross)[len(gross) // 2]
    dd_tot, dd_i, dd_j = metrics.max_drawdown(base.total)   # total return, like the tables
    st = R.stacking(ctx)
    s0 = st["rows"][0]
    s_idx = min(st["rows"], key=lambda r: abs(r["overlay_vol"] - vt))

    # --- common window for the three total-return series --------------------
    idx_tot = dict(zip(base.months, base.total))
    stk_tot = dict(zip(stacked.months, stacked.total))
    common = [m for m in base.months if m in spx and m in stk_tot]
    start = months[months.index(common[0]) - 1]
    axis = [start] + common

    def curve(by_month):
        eq, v = [1.0], 1.0
        for m in common:
            v *= 1 + by_month[m]
            eq.append(v)
        return eq

    def dd(by_month):
        out, v, peak = [0.0], 1.0, 1.0
        for m in common:
            v *= 1 + by_month[m]
            peak = max(peak, v)
            out.append(v / peak - 1)
        return out

    def stats_of(by_month):
        r = [by_month[m] for m in common]
        prev = dict(zip(months[1:], months))
        ex = [by_month[m] - ((1 + ctx["rates"][prev[m]] / 100) ** (1 / 12) - 1) for m in common]
        return {"cagr": metrics.cagr(r), "vol": metrics.ann_vol(r), "sharpe": metrics.sharpe(ex),
                "maxdd": metrics.max_drawdown(r)[0]}
    trio = [("Trend index", "s1", idx_tot), ("100% S&P 500 + 100% trend index", "s2", stk_tot),
            ("S&P 500 total return", "s3", spx)]
    trio_stats = {name: stats_of(d) for name, _, d in trio}

    eq_svg, eq_cfg = ch.line_chart("eq", axis, [(n, s, curve(d)) for n, s, d in trio],
                                   "Growth of 1 unit, total return", log=True)
    dd_svg, dd_cfg = ch.line_chart("dd", axis, [(n, s, dd(d)) for n, s, d in trio],
                                   "Drawdown from previous peak", height=260)
    legend = _legend([(n, s) for n, s, _ in trio])
    trio_tbl = ch.table(
        ["", "Annual return", "Volatility", "Sharpe", "Worst drawdown"],
        [[n, ch.fmt_p(trio_stats[n]["cagr"], 1), ch.fmt_p(trio_stats[n]["vol"], 1),
          ch.fmt_x(trio_stats[n]["sharpe"]), ch.fmt_p(trio_stats[n]["maxdd"], 1)] for n, _, _ in trio],
        f"{common[0][:7]} to {common[-1][:7]}, total returns (the index earns T-bill interest "
        "on its collateral). Sharpe ratios use returns over cash.")

    # --- the markets, and what the index holds now ---------------------------
    last = base.months[-1]
    sched = info["schedule"] or {}
    current = sorted(sched.get(months[months.index(last) - 1], set(panel)))
    pr = R.practitioner(ctx)
    i_last = months.index(last)
    rets_last = {s: model.monthly_returns(panel[s], months) for s in panel}
    for s in panel:
        for m in rets_last[s]:
            rets_last[s][m] += ctx["carry"].get(s, {}).get(m, 0.0)
    risk = {}
    for s in panel:
        w = base.positions[s].get(last)
        v = model.trailing_vol(rets_last[s], months, i_last - 1, C.VOL_WINDOW_MONTHS)
        if w is not None and v:
            risk[s] = abs(w) * v
    tot_risk = sum(risk.values()) or 1.0
    order = {sec: i for i, sec in enumerate(C.SECTORS)}
    pos_rows = []
    for s in sorted(current, key=lambda s: (order[ctx["sector_of"][s]], C.CLUSTERS[s], s)):
        name, sec = C.CANDIDATES[s]
        w = base.positions[s].get(last)
        pos_rows.append([name, sec, C.CLUSTERS[s].replace("_", " "),
                         "—" if w is None else ("long" if w > 0 else "short" if w < 0 else "flat"),
                         "—" if w is None else ch.fmt_p(w, 0),
                         ch.fmt_p(risk.get(s, 0) / tot_risk, 0)])
    pos_tbl = ch.table(["Market", "Sector", "Cluster", f"Position ({last[:7]})",
                        "Notional, % of capital", "Share of risk"], pos_rows,
                       "Share of risk: position x the market's own volatility, as a share of "
                       "the total (ignores correlations).")
    sec_share = {sec: sum(risk.get(s, 0) for s in current if ctx["sector_of"][s] == sec) / tot_risk
                 for sec in C.SECTORS}

    # --- where the returns come from ---------------------------------------------
    sleeves = P.sleeve_returns(base, panel, months, ctx["carry"], ctx["sector_of"])
    sl_rows = []
    for sec in C.SECTORS:
        x = [sleeves[sec][m] for m in sorted(sleeves[sec])]
        sl_rows.append([sec.title(), str(sum(1 for s in current if ctx["sector_of"][s] == sec)),
                        ch.fmt_p(stats.fmean(x) * 12, 1), ch.fmt_x(metrics.sharpe(x))])
    sl_tbl = ch.table(["Sector", "Markets today", "Contribution a year", "Sharpe on its own"], sl_rows,
                      "Before costs. The contributions add up to the index's gross return.")

    # --- calendar years -------------------------------------------------------------
    def yearly(d):
        out = {}
        for m in common:
            out[m[:4]] = (1 + out.get(m[:4], 0.0)) * (1 + d[m]) - 1
        return out
    yi, ys, yk = yearly(idx_tot), yearly(spx), yearly(stk_tot)
    yr_tbl = ch.table(["Year", "Trend index", "S&P 500", "Stacked"],
                      [[y, ch.fmt_p(yi[y], 1), ch.fmt_p(ys[y], 1), ch.fmt_p(yk[y], 1)]
                       for y in sorted(yi, reverse=True)],
                      "Total returns. Stacked = 100% S&P 500 + 100% trend index.")

    # --- stacking size table -----------------------------------------------------------
    st_tbl = ch.table(["Trend overlay volatility", "Sharpe", "Volatility", "Worst drawdown",
                       "Annual return"],
                      [[ch.fmt_p(r["overlay_vol"], 0) + (" (the index)" if r is s_idx else ""),
                        ch.fmt_x(r["sharpe"]), ch.fmt_p(r["vol"], 1), ch.fmt_p(r["maxdd"], 1),
                        ch.fmt_p(r["cagr"], 1)] for r in st["rows"]],
                      "100% S&P 500 plus the trend index scaled to each volatility.")

    # --- robustness (collapsed) -----------------------------------------------------------
    var_tbl = ch.table(
        ["Variant", "Sharpe", "95% CI", "Annual return", "Volatility", "Worst drawdown"],
        [[r.label, ch.fmt_x(summ[r.label]["sharpe"]),
          f'[{ch.fmt_x(summ[r.label]["sharpe_ci_lo"])}, {ch.fmt_x(summ[r.label]["sharpe_ci_hi"])}]',
          ch.fmt_p(summ[r.label]["cagr_total"], 1), ch.fmt_p(summ[r.label]["vol_excess"], 1),
          ch.fmt_p(summ[r.label]["max_dd_total"], 1)] for r in ctx["variants"]],
        "Each row changes one thing relative to the index. After costs, before fees; Sharpe "
        "from returns over cash.")
    kw = P.ctx_kwargs(ctx)
    cost_rows = []
    for mult in (0, 1, 2, 4):
        r = model.run_backtest(panel, months, ctx["sector_of"], ctx["rates"],
                               **P.scale_costs(kw, mult))
        cost_rows.append([f"x{mult}", ch.fmt_x(metrics.sharpe(r.excess)),
                          ch.fmt_p(sum(r.cost) / len(r.cost) * 12, 2)])
    cost_tbl = ch.table(["Trading costs", "Sharpe", "Cost a year"], cost_rows)
    uni_rows, prev_sel = [], set()
    for m, sel in info["reviews"]:
        sel = set(sel)
        if sel != prev_sel:
            uni_rows.append([m[:4], str(len(sel)),
                             ", ".join(sorted(C.CANDIDATES[x][0] for x in sel - prev_sel)) or "-",
                             ", ".join(sorted(C.CANDIDATES[x][0] for x in prev_sel - sel)) or "-"])
        prev_sel = sel
    sel_tbl = ch.table(["Review", "Markets", "Joined", "Left"], uni_rows,
                       "Only reviews where the membership changed are shown.")
    fee_tbl = ch.table(["Fees", "Annual return", "Sharpe", "Worst drawdown"],
                       [[r["label"], ch.fmt_p(r["cagr"], 1), ch.fmt_x(r["sharpe"]),
                         ch.fmt_p(r["maxdd"], 1)] for r in pr["fees"]])
    dec_tbl = ch.table(["Decade", "Realised volatility", "Sharpe", "Median gross exposure",
                        "Months at the leverage cap"],
                       [[r["decade"], ch.fmt_p(r["vol"], 1), ch.fmt_x(r["sharpe"]),
                         f'{r["gross"]:.1f}x', ch.fmt_p(r["capped"], 0)] for r in pr["decades"]])
    att_tbl = ch.table(["Market", "Share of gross profit"],
                       [[n, ch.fmt_p(v, 0)] for n, v in pr["attribution"][:10]])
    spl_tbl = ch.table(["Commodity", "Correlation", "Tracking error (holdout)", "Bias (holdout)"],
                       [[C.CANDIDATES[x][0], ch.fmt_x(v["fidelity"]), ch.fmt_p(v["te_holdout"], 0),
                         ch.fmt_p(v["bias_holdout"], 0)]
                        for x, v in sorted(info["splice"].items()) if v["spliced"]],
                       "Yahoo front-month returns mapped onto the futures series by regression; "
                       "fitted 2005-2018, measured 2019-2024.")
    x_aq, y_aq, _ = metrics.align(dict(zip(base.months, base.excess)), aqr["ex_equity"])

    # --- page ------------------------------------------------------------------------------
    n_sec = {sec: sum(1 for s in current if ctx["sector_of"][s] == sec) for sec in C.SECTORS}
    gen = dt.datetime.now().strftime("%Y-%m-%d")
    cfg = {"eq": eq_cfg, "dd": dd_cfg}
    ci = f"{ch.fmt_x(bs['sharpe_ci_lo'])}&ndash;{ch.fmt_x(bs['sharpe_ci_hi'])}"
    lbs = "/".join(str(x) for x in C.HEADLINE["multi_lookbacks"])
    cw = C.HEADLINE["carry_weight"]

    html_doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Trend Index Report</title>
<style>{_css()}</style>
</head>
<body class="viz-root">
<button class="toggle" id="themebtn">Dark mode</button>
<div class="tip" id="tip"></div>
<div class="wrap">
<header>
  <h1>Trend index</h1>
  <p class="sub">A managed-futures trend strategy designed to be stacked on equities &#183;
  {len(current)} futures markets today &#183; backtest {base.months[0][:7]} to {base.months[-1][:7]}
  &#183; updated {gen}</p>
</header>

<div class="stats">
  {ch.stat("Sharpe ratio", ch.fmt_x(bs['sharpe']), f"95% range {ci}".replace("&ndash;", "-"))}
  {ch.stat("Annual return", ch.fmt_p(bs['cagr_total'], 1), f"{ch.fmt_p(bs['cagr_excess'], 1)} above cash")}
  {ch.stat("Volatility", ch.fmt_p(bs['vol_excess'], 1), f"target {ch.fmt_p(vt, 0)}")}
  {ch.stat("Worst drawdown", ch.fmt_p(dd_tot, 1), f"{base.months[dd_i][:7]} to {base.months[dd_j][:7]}")}
  {ch.stat("Correlation to S&P 500", ch.fmt_x(bs.get('corr_benchmark')),
           f"{ch.fmt_x(bs.get('corr_benchmark_down'))} when stocks fall")}
  {ch.stat("S&P 500 + index", "Sharpe " + ch.fmt_x(s_idx['sharpe']),
           f"S&P alone {ch.fmt_x(s0['sharpe'])}")}
</div>

<h2>The index, the S&amp;P 500, and the two stacked</h2>
<div class="card">{legend}{eq_svg}</div>
{trio_tbl}
<p>Stacking means holding the S&amp;P 500 with your capital and the trend index on top as a
futures overlay: the futures need only margin, so the capital does both jobs. Because the
index tends to make money when stocks fall &mdash; in the {st['crash_n']} months the S&amp;P
500 lost more than 5% (on average {ch.fmt_p(st['crash_equity'], 1)}) it gained
{ch.fmt_p(st['crash_index'], 1)} on average &mdash; the combination earned more than stocks
alone with a smaller worst drawdown.</p>

<h2>Drawdowns</h2>
<div class="card">{legend}{dd_svg}</div>

<h2>How the index works</h2>
<ol class="steps">
  <li><strong>Markets.</strong> Every January the universe is re-chosen from
  {len(C.CANDIDATES)} liquid futures using only data available at the time: in order of
  liquidity, a market joins if no market already held explains half its moves (return
  correlation below {C.SELECTION['max_correlation']:.2f}); members already in the index are
  kept unless the overlap grows clearly larger. Performance is never used. Today:
  {len(current)} markets ({n_sec['commodity']} commodities, {n_sec['bond']} bonds,
  {n_sec['fx']} currencies), listed below.</li>
  <li><strong>Direction.</strong> At each month-end, every market gets a trend score: the
  average of the signs of its {lbs}-month returns (each +1 if the market rose, &minus;1 if it
  fell). {ch.fmt_p(1 - cw, 0)} of the position follows that trend score and {ch.fmt_p(cw, 0)}
  follows carry &mdash; the return a futures position earns if prices do not move (interest-rate
  differentials in currencies, the yield curve in bonds, the futures curve in commodities).</li>
  <li><strong>Size.</strong> Risk is shared equally between the three sectors, then equally
  between the economic groups inside each sector (energy, metals, grains, softs; US, European and
  Japanese bonds; European, commodity and yen currencies), then equally between the markets in
  each group. A group of similar markets therefore shares one risk budget instead of each
  taking a full one. Each market's risk is its volatility over the last
  {C.VOL_WINDOW_MONTHS} months, but never less than half the median of its sector: a very calm
  market is not levered up as if its calm were permanent.</li>
  <li><strong>Leverage.</strong> The whole book is scaled toward {ch.fmt_p(vt, 0)} expected
  volatility a year, from the recent volatilities and correlations of the markets held, with
  gross exposure capped at {C.HEADLINE['max_leverage']:.0f}x capital (typically
  {gross_med:.1f}x, mostly bonds, which move little). Realised volatility has run between
  {ch.fmt_p(min(r['vol'] for r in pr['decades']), 0)} and
  {ch.fmt_p(max(r['vol'] for r in pr['decades']), 0)} by decade.</li>
  <li><strong>Trading.</strong> Positions are reset once a month using month-end prices; the
  new position is held for the whole next month. Every trade and every futures roll pays a
  cost, about one tick per trade, higher in earlier decades. No management fee is deducted.</li>
</ol>
<p>Why it is built this way. Trend following earns money from persistent moves: prices
adjust slowly to news, and hedgers and investors trade in a way that lets moves run. Those
moves occur across many unrelated markets and at many speeds, so the design spends its
effort on spreading risk evenly across genuinely different bets and speeds, not on
predicting better. Since 2008 markets have moved together more often and central banks have
suppressed volatility for long stretches; the cluster budgets, the volatility floor and the
portfolio-level volatility target are the responses. None of this is a market view: the rules
are the same in every period. Sharpe ratio before {C.SUBPERIOD_SPLIT[:4]}:
{ch.fmt_x(sp['pre']['sharpe'])}; from {C.SUBPERIOD_SPLIT[:4]}: {ch.fmt_x(sp['post']['sharpe'])}.
The design was refined on the full history, so the later figure is a pseudo out-of-sample
check. The research is in <code>output/research.md</code>.</p>

<h2>What it holds now</h2>
<p>Positions held during {last[:7]}. Share of risk by sector: commodities
{ch.fmt_p(sec_share['commodity'], 0)}, bonds {ch.fmt_p(sec_share['bond'], 0)}, currencies
{ch.fmt_p(sec_share['fx'], 0)}.</p>
{pos_tbl}

<h2>Where the returns come from</h2>
{sl_tbl}

<h2>How much to stack</h2>
<p>More trend overlay raised the combined Sharpe ratio up to about 15&ndash;20% overlay
volatility, and kept lowering the worst drawdown. The index targets {ch.fmt_p(vt, 0)}, a
little below the historical best, because that best is estimated from one history.</p>
{st_tbl}

<h2>Costs, fees and risks</h2>
<p>The figures above are after trading and roll costs but before any management fee. As a fund
charging 2% a year plus 20% of gains, the Sharpe ratio would have been
{ch.fmt_x(pr['fees'][2]['sharpe'])} and the annual return {ch.fmt_p(pr['fees'][2]['cagr'], 1)}.
Estimated exchange margin uses a median {ch.fmt_p(pr['margin']['median'], 0)} of capital
({ch.fmt_p(pr['margin']['max'], 0)} at most). Profit is concentrated: Japan (the yen and JGBs)
contributed {ch.fmt_p(pr['japan_share'], 0)}, and without those two markets the Sharpe ratio is
{ch.fmt_x(pr['no_japan']['full'])} ({ch.fmt_x(pr['no_japan']['post'])} since
{C.SUBPERIOD_SPLIT[:4]}). Commodity data after {C.FUTURES_END[:7]} is an approximation (below);
the index was flat over that period (Sharpe {ch.fmt_x(pr['splice_period']['sharpe_after'])}).</p>
<details class="tbl"><summary>After fees</summary>{fee_tbl}</details>
<details class="tbl"><summary>Volatility and leverage by decade</summary>{dec_tbl}</details>
<details class="tbl"><summary>Profit by market (top ten)</summary>{att_tbl}</details>
<details class="tbl"><summary>Commodity data after {C.FUTURES_END[:7]}</summary>{spl_tbl}</details>

<h2>Calendar years</h2>
<details class="tbl"><summary>All {len(yi)} years</summary>{yr_tbl}</details>

<h2>Robustness</h2>
<details class="tbl"><summary>Variants: change one design choice at a time</summary>{var_tbl}</details>
<details class="tbl"><summary>Higher trading costs</summary>{cost_tbl}</details>
<details class="tbl"><summary>The universe over time</summary>{sel_tbl}</details>
<p class="sub">For reference, AQR&rsquo;s published time-series momentum factor on the same
asset classes had a Sharpe ratio of {ch.fmt_x(metrics.sharpe(y_aq))} over the same months,
before costs; its correlation with this index is {ch.fmt_x(metrics.correlation(x_aq, y_aq))}.</p>

<h2>Data and caveats</h2>
<ul>
  <li><strong>Commodities:</strong> daily back-adjusted futures prices from the open-source
  pysystemtrade project up to March 2024. That data stops there, so later months use Yahoo
  front-month prices mapped onto the futures series by a regression fitted over
  2005&ndash;2024 (tracking error on a holdout: see above). Treat the months since April 2024 as
  an approximation.</li>
  <li><strong>Bonds:</strong> month-end yields from the Federal Reserve, Bundesbank, Bank of
  England and Japan's Ministry of Finance, converted to futures-like returns (price change plus
  yield, minus the local short-term rate).</li>
  <li><strong>Currencies:</strong> BIS month-end exchange rates plus the interest-rate difference
  a currency future earns. <strong>S&amp;P 500:</strong> total return index.</li>
  <li><strong>This is a backtest.</strong> Results are after trading costs but before fees;
  a fund would charge 1&ndash;3% a year. Past results do not guarantee future ones.</li>
</ul>

<footer>Generated {gen} &#183; Python standard library only &#183; MIT licence &#183; not investment advice</footer>
</div>
<script>
{_JS}
const CFG = {json.dumps(cfg)};
Object.keys(CFG).forEach(function (k) {{ wireLine(k, CFG[k]); }});
</script>
</body>
</html>
"""
    C.OUTPUT.mkdir(parents=True, exist_ok=True)
    out = C.OUTPUT / "report.html"
    out.write_text(html_doc, encoding="utf-8")
    print(f"Report written: {out}  ({len(html_doc):,} bytes)")
    post_ci = metrics.bootstrap_sharpe_ci(
        [x for m, x in zip(base.months, base.excess) if m >= C.SUBPERIOD_SPLIT],
        C.BOOTSTRAP_SAMPLES, C.BOOTSTRAP_SEED, block=C.BOOTSTRAP_BLOCK)
    x_aq, y_aq, m_aq = metrics.align(dict(zip(base.months, base.excess)), aqr["ex_equity"])
    _update_readme(sp, post_ci, metrics.sharpe(y_aq), P.subperiod_stats(m_aq, y_aq),
                   metrics.correlation(x_aq, y_aq), base, summ, ctx["variants"], st)
    return out


def _update_readme(sp, post_ci, aqr_sh, aqr_sp, aqr_corr, base, summ, variants, st):
    """Rewrite the block between the RESULTS markers in README.md, so the
    numbers quoted there can never drift from the code."""
    readme = C.ROOT / "README.md"
    if not readme.exists():
        return
    text = readme.read_text(encoding="utf-8")
    a, b = "<!-- RESULTS:START -->", "<!-- RESULTS:END -->"
    if a not in text or b not in text:
        return
    f = metrics.fmt_num
    lines = [
        a,
        f"*Generated {dt.date.today()} by `python run.py report`. Returns "
        f"{base.months[0][:7]} to {base.months[-1][:7]} ({len(base)} months), excess of "
        "cash for the Sharpe ratio; annual return and worst drawdown are total returns (the index "
        "earns T-bill interest on its collateral). Net of trading and roll costs, before fees.*",
        "",
        "| Variant | Sharpe | 95% CI | Annual return | Volatility | Worst drawdown | Corr. S&P 500 |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in variants:
        s = summ[r.label]
        lines.append(f"| {r.label} | {f(s['sharpe'])} | [{f(s['sharpe_ci_lo'])}, "
                     f"{f(s['sharpe_ci_hi'])}] | {metrics.fmt_pct(s['cagr_total'], 1)} | "
                     f"{metrics.fmt_pct(s['vol_excess'], 1)} | "
                     f"{metrics.fmt_pct(s['max_dd_total'], 1)} | {f(s.get('corr_benchmark'))} |")
    s0 = st["rows"][0]
    s15 = min(st["rows"], key=lambda r: abs(r["overlay_vol"] - C.HEADLINE["target_vol"]))
    lines += [
        "",
        f"Sharpe before {C.SUBPERIOD_SPLIT[:4]}: **{f(sp['pre']['sharpe'])}**; from "
        f"{C.SUBPERIOD_SPLIT[:4]} (pseudo out-of-sample): **{f(sp['post']['sharpe'])}** (95% CI [{f(post_ci[0])}, "
        f"{f(post_ci[1])}]). AQR Time Series Momentum ex-equity, same months, gross of costs: "
        f"**{f(aqr_sh)}** ({f(aqr_sp['pre']['sharpe'])} / {f(aqr_sp['post']['sharpe'])}), "
        f"correlation {f(aqr_corr)}.",
        "",
        f"Stacked on the S&P 500 (total return, {st['start'][:4]}-{st['end'][:4]}): equities "
        f"alone Sharpe {f(s0['sharpe'])}, max drawdown {metrics.fmt_pct(s0['maxdd'], 0)}; with "
        f"100% of the index on top, Sharpe {f(s15['sharpe'])}, max drawdown "
        f"{metrics.fmt_pct(s15['maxdd'], 0)}. In the {st['crash_n']} months equities fell more "
        f"than 5% the index averaged {metrics.fmt_pct(st['crash_index'], 1)}.",
        b,
    ]
    i, j = text.index(a), text.index(b) + len(b)
    readme.write_text(text[:i] + "\n".join(lines) + text[j:], encoding="utf-8")
    print(f"README results block updated: {readme}")
