"""
Command-line entry point.

    python run.py fetch      download every source into data/mf.sqlite (cached)
    python run.py sanity     data coverage, provenance and integrity -- RUN FIRST
    python run.py backtest   headline, variants, diagnostics, sensitivity tables
    python run.py report     output/report.html + refresh the README results block
    python run.py research   output/research.md: data, design, persistence, breadth, stacking
    python run.py all        fetch -> sanity -> backtest -> report -> research

Add --force to `fetch` to ignore the cache and re-download everything.

Why `sanity` is its own step: almost every backtest disaster is a data problem
wearing a strategy costume. On this project it would have caught a benchmark
file frozen at 2024-12, a set of stale averaged Bund yields hiding under new
month-end data, and FX rates that were monthly averages rather than month-ends.
"""

import datetime as dt
import sys

import config as C
from mf import fetch, metrics, model, panel as P, sources as S, store


# ---------------------------------------------------------------------------
# fetch
# ---------------------------------------------------------------------------
def cmd_fetch(force=False):
    conn = store.connect(C.DB_PATH)
    stamp = dt.datetime.now().isoformat(timespec="seconds")
    failures = []

    def step(title, fn):
        print(f"\n{title}")
        try:
            fn()
        except Exception as exc:                      # report, keep going
            failures.append((title, str(exc)[:200]))
            print(f"   FAILED: {str(exc)[:200]}")

    def put(table, key, source, series: dict, note=""):
        rows = sorted(series.items())
        n = store.replace_series(conn, table, key, source, rows)
        store.log_fetch(conn, source, key, stamp, n, note)
        print(f"   {key:<14} {n:>5} months  {rows[0][0][:7]} -> {rows[-1][0][:7]}"
              f"  [{source}]")

    def futures():
        for sym, (_, code, _, _) in C.COMMODITIES.items():
            adj = S.fetch_pst("adjusted_prices_csv", code, C.PST_BASE, C.CACHE, force)
            mult = S.fetch_pst("multiple_prices_csv", code, C.PST_BASE, C.CACHE, force)
            monthly = S._last_per_month(S.parse_pst_daily_index(adj, mult))
            put("prices", sym, "pst_futures", monthly, "month-end excess-return index")
            put("yields", "CARRY_" + sym, "pst_futures",
                S._last_per_month(S.parse_pst_carry(mult)), "annualised roll yield, %")

    def yahoo_commodities():
        for sym, (_, _, yf, _) in C.COMMODITIES.items():
            daily, _ = fetch.fetch_yahoo_daily(yf, "1999-12-01", C.CACHE, force)
            monthly = fetch.aggregate_monthly(daily, min_days=C.MIN_TRADING_DAYS)
            put("prices", "yf_" + sym, "yahoo_front_month",
                {m: c for m, c, _, _ in monthly}, "month-end, front month")

    def wb():
        data = S.parse_wb_pinksheet(S.fetch_wb_pinksheet(C.CACHE, force))
        for col, sym in C.WB_RESEARCH.items():
            if col not in data:
                raise RuntimeError(f"Pink Sheet column missing: {col!r}")
            put("prices", "wb_" + sym, "wb_pinksheet", data[col], "monthly AVERAGE")

    def h15():
        data = S.parse_fed_h15(S.fetch_fed_h15(C.CACHE, force))
        for mat in ("3Mo", "2Y", "5Y", "10Y", "30Y"):
            put("yields", mat, "fed_h15", data[mat], "month-end")

    def fx():
        areas = {k: sym for k, (sym, _, _) in C.FX_PAIRS.items()}
        data = S.parse_bis_fx(S.fetch_bis_fx(C.FX_CURRENCIES, C.CACHE, force), areas)
        for sym in areas.values():
            put("prices", sym, "bis_xru_eop", data[sym], "end of period, inverted")

    def policy():
        pre, post, _ = C.POLICY_RATE_EUR
        areas = sorted(set(C.POLICY_RATE_AREAS) | {pre, post, "US"})
        data = S.parse_bis_policy_rates(S.fetch_bis_policy_rates(areas, C.CACHE, force))
        for area in areas:
            put("yields", f"CBPOL_{area}", "bis_cbpol", data[area])

    def bund():
        for mat in (2, 5, 10):
            try:
                path = S.fetch_bundesbank(mat, C.CACHE, force)
            except Exception as exc:
                raise RuntimeError(
                    f"{exc}\n   The Bundesbank API may be showing a browser check. Open\n"
                    f"   {S._BUBA_URL.format(mat=mat)}\n   in a browser and save the file as "
                    f"data/cache/bundesbank_bund_{mat}y_daily.csv, then rerun.") from None
            put("yields", f"DE_{mat}Y", "bundesbank_bbsis", S.parse_bundesbank(path), "month-end")

    def jgb():
        put("yields", "JP_10Y", "mof_japan",
            S.parse_mof_jgb(S.fetch_mof_jgb(C.CACHE, force), "10Y"), "month-end")

    def cgb():
        put("yields", "CA_10Y", "boc_valet",
            S.parse_boc_10y(S.fetch_boc_10y(C.CACHE, force)), "month-end")

    def gilt():
        put("yields", "GB_10Y", "boe_iadb",
            S.parse_boe_gilt(S.fetch_boe_gilt(C.CACHE, force)), "month-end, zero coupon")

    def aqr():
        for label, series in S.parse_aqr_tsmom(S.fetch_aqr_tsmom(C.CACHE, force)).items():
            put("benchmarks", label, "aqr_tsmom", series)

    def spx():
        daily, _ = fetch.fetch_yahoo_daily(C.SPX_YAHOO, C.SPX_FETCH_FROM, C.CACHE, force)
        monthly = fetch.aggregate_monthly(daily, min_days=C.MIN_TRADING_DAYS)
        put("prices", C.SPX_SYMBOL, "yahoo", {m: c for m, c, _, _ in monthly}, "month-end")

    print("=" * 78)
    print("FETCH -> " + str(C.DB_PATH))
    print("=" * 78)
    step("1. Commodity futures (pysystemtrade, daily -> month-end, to 2024-03)", futures)
    step("1b. Commodity front months (Yahoo, daily -> month-end), for the splice", yahoo_commodities)
    step("1c. World Bank Pink Sheet (monthly AVERAGES; research only)", wb)
    step("2. Fed H.15 (Treasury yields, daily -> month-end)", h15)
    step("3. BIS exchange rates (end of period, vs USD)", fx)
    step("4. BIS central bank policy rates (FX carry)", policy)
    step("5. Bundesbank 2/5/10Y (daily -> month-end)", bund)
    step("6. Bank of England 10Y gilt (daily -> month-end)", gilt)
    step("6b. Japan MoF 10Y JGB (daily -> month-end)", jgb)
    step("6c. Bank of Canada 10Y (daily -> month-end)", cgb)
    step("7. AQR benchmark factors", aqr)
    step("8. S&P 500 (Yahoo, daily -> month-end)", spx)
    conn.close()
    if failures:
        print(f"\n{len(failures)} step(s) failed:")
        for t, e in failures:
            print(f"  - {t}: {e}")
        raise SystemExit(1)


# ---------------------------------------------------------------------------
# sanity
# ---------------------------------------------------------------------------
def cmd_sanity():
    conn = store.connect(C.DB_PATH)
    print("=" * 78)
    print("DATA SANITY REPORT")
    print("=" * 78)
    problems = []

    for table in ("prices", "yields", "benchmarks"):
        srcs = store.sources(conn, table)
        print(f"\n{table}:")
        for key, first, last, n in store.coverage(conn, table):
            s = ",".join(sorted(srcs.get(key, ())))
            print(f"  {key:<16}{first[:7]:>9} -> {last[:7]:<9}{n:>6}   {s}")
            if len(srcs.get(key, ())) > 1:
                problems.append(f"{table}.{key} mixes sources {sorted(srcs[key])}")

    cur = f"{dt.date.today():%Y-%m}-01"
    for table in ("prices", "yields"):
        for key, _, last, _ in store.coverage(conn, table):
            if last >= cur:
                problems.append(f"{table}.{key} contains the incomplete month {cur[:7]}")

    panel, months, holes, info = P.build_panel(conn)
    averaged = P.averaged_symbols(conn)
    print(f"\nBacktest grid: {len(panel)} markets, {months[0][:7]} -> {months[-1][:7]}"
          f" ({len(months)} months)")
    late = sorted((min(v), s) for s, v in panel.items() if min(v) > months[0])
    print(f"  late entrants: {[f'{s} {m[:7]}' for m, s in late] or 'none'}")
    if holes:
        for s, ms in sorted(holes.items()):
            print(f"  HOLE {s}: {len(ms)} month(s) {ms[0][:7]} .. {ms[-1][:7]}")
            problems.append(f"{s} has {len(ms)} missing months inside the window")
    else:
        print("  no missing observations inside the window")

    print(f"\nMonthly-average sources {sorted(C.AVERAGED_SOURCES)} -> signal lagged"
          f" one extra month for: {len(averaged)} markets")
    print("\nLag-1 autocorrelation of monthly returns (averaging shows up as ~+0.25):")
    for s in sorted(panel, key=lambda s: (C.CANDIDATES[s][1], s)):
        r = model.monthly_returns(panel[s], months)
        x = [r[m] for m in months if m in r]
        ac = metrics.correlation(x[:-1], x[1:])
        tag = "averaged -> lagged" if s in averaged else ""
        # Only FX is policed: short bond yields genuinely trend with policy
        # cycles (2-year yield changes are ~0.2 autocorrelated in the US and
        # Germany alike), and commodities come from daily futures data.
        if s not in averaged and C.CANDIDATES[s][1] == "fx" and ac > 0.15:
            tag = "SUSPICIOUS: month-end source with averaged-looking returns"
            problems.append(f"{s} autocorrelation {ac:+.2f}")
        print(f"  {s:<13}{C.CANDIDATES[s][1]:<11}{ac:+.3f}  {tag}")

    print(f"\nCommodity splice after {C.FUTURES_END[:7]} (Yahoo front month, "
          f"calibrated on {C.SPLICE_CALIBRATION[0][:4]}-{C.SPLICE_CALIBRATION[1][:4]}):")
    for s, v in sorted(info["splice"].items()):
        print(f"  {s:<13}corr {v['fidelity']:.2f}  slope {v['beta']:.2f}  "
              f"holdout tracking error {v['te_holdout']:.0%}  "
              f"{'spliced' if v['spliced'] else 'STOPS at ' + C.FUTURES_END[:7]}")

    print(f"\nUniverse reviews ({C.SELECTION}):")
    prev = set()
    for m, sel in info["reviews"]:
        if set(sel) != prev:
            print(f"  {m[:4]}: {len(sel)} markets  +{sorted(set(sel) - prev)}  "
                  f"-{sorted(prev - set(sel))}")
        prev = set(sel)

    print("\nFull-sample selection (the 'fixed universe' comparison):")
    for r in info["selection"]:
        print(f"  {'KEEP' if r['kept'] else 'drop':<5}{r['name']:<20}{r['volume']:>6}k  {r['reason']}")

    rates = store.load_rates(conn)
    covered = sum(1 for m in months if m in rates)
    print(f"\n3-month T-bill (funding/collateral): {covered}/{len(months)} months covered")
    if covered < len(months):
        problems.append("T-bill rate missing for part of the window")
    conn.close()

    print("\n" + ("PROBLEMS:\n  - " + "\n  - ".join(problems) if problems
                  else "No problems found."))
    return problems


# ---------------------------------------------------------------------------
# backtest
# ---------------------------------------------------------------------------
def _hdr():
    print(f"{'variant':<40}{'CAGRex':>8}{'CAGRtot':>9}{'vol':>7}{'Sharpe':>8}"
          f"{'95% CI':>15}{'maxDD':>8}{'rSPX':>7}")
    print("-" * 102)


def _row(label, s):
    ci = (f"[{metrics.fmt_num(s.get('sharpe_ci_lo'))},"
          f"{metrics.fmt_num(s.get('sharpe_ci_hi'))}]") if "sharpe_ci_lo" in s else ""
    print(f"{label[:39]:<40}{metrics.fmt_pct(s['cagr_excess']):>8}"
          f"{metrics.fmt_pct(s['cagr_total']):>9}{metrics.fmt_pct(s['vol_excess'], 1):>7}"
          f"{metrics.fmt_num(s['sharpe']):>8}{ci:>15}"
          f"{metrics.fmt_pct(s['max_dd_excess'], 1):>8}"
          f"{metrics.fmt_num(s.get('corr_benchmark')):>7}")


def _boot():
    return (C.BOOTSTRAP_SAMPLES, C.BOOTSTRAP_SEED, C.BOOTSTRAP_BLOCK)


def cmd_backtest():
    conn = store.connect(C.DB_PATH)
    ctx = P.load_context(conn)
    conn.close()
    base, months = ctx["base"], ctx["months"]

    print("=" * 102)
    print("TREND INDEX -- RESULTS")
    print("=" * 102)
    n_sec = {sec: sum(1 for s in ctx["panel"] if ctx["sector_of"][s] == sec) for sec in C.SECTORS}
    print(f"Universe: {len(ctx['panel'])} markets {n_sec};"
          f" {len(ctx['averaged'])} on monthly-average data (signal lagged 1 month)")
    print(f"Returns:  {base.months[0][:7]} -> {base.months[-1][:7]} ({len(base)} months)")
    print("CAGRex = trend P&L over cash. CAGRtot = + T-bill collateral. Sharpe from"
          " excess; CI = 12-month block bootstrap.\n")
    _hdr()
    for r in ctx["variants"]:
        _row(r.label, metrics.summarise(r.months, r.excess, r.total, r.turnover,
                                        ctx["spx"], bootstrap=_boot()))

    print("\nSub-periods (headline):")
    sp = P.subperiod_stats(base.months, base.excess)
    print(f"  before {C.SUBPERIOD_SPLIT[:7]}: Sharpe {metrics.fmt_num(sp['pre']['sharpe'])},"
          f" CAGR {metrics.fmt_pct(sp['pre']['cagr'])}  ({sp['pre']['n']} months)")
    print(f"  from   {C.SUBPERIOD_SPLIT[:7]}: Sharpe {metrics.fmt_num(sp['post']['sharpe'])},"
          f" CAGR {metrics.fmt_pct(sp['post']['cagr'])}  ({sp['post']['n']} months)")

    print("\nSleeves vs AQR Time Series Momentum (same months; AQR is gross of costs):")
    sleeves = P.sleeve_returns(base, ctx["panel"], months, ctx["carry"], ctx["sector_of"])
    ours = dict(zip(base.months, base.excess))
    pairs = [("portfolio (net)", ours, ctx["aqr"]["ex_equity"])]
    pairs += [(f"{sec} sleeve (gross)", sleeves[sec], ctx["aqr"][sec]) for sec in C.SECTORS]
    for lab, a, b in pairs:
        x, y, sh = metrics.align(a, b)
        if len(sh) < 24:
            print(f"  {lab:<24} insufficient overlap")
            continue
        print(f"  {lab:<24} Sharpe {metrics.sharpe(x):5.2f}   AQR {metrics.sharpe(y):5.2f}"
              f"   corr {metrics.correlation(x, y):+.2f}   ({len(sh)} months)")

    print("\nPer market, traded alone (same lag and carry, gross of costs):")
    rows = P.per_market_stats(ctx["panel"], months, ctx["carry"], ctx["averaged"])
    print(f"  {'symbol':<13}{'sector':<11}{'trendSh':>8}{'trendCAGR':>11}{'holdCAGR':>10}"
          f"{'flips':>7}")
    for r in sorted(rows, key=lambda r: -r["trend_sharpe"]):
        print(f"  {r['symbol']:<13}{r['sector']:<11}{metrics.fmt_num(r['trend_sharpe']):>8}"
              f"{metrics.fmt_pct(r['trend_cagr']):>11}{metrics.fmt_pct(r['hold_cagr']):>10}"
              f"{r['flips']:>7}{'  (lagged)' if r['averaged'] else ''}")

    print("\nLookback sensitivity, headline sizing (robustness, not selection):")
    common = P.ctx_kwargs(ctx)
    for lb in C.LOOKBACK_GRID:
        r = model.run_backtest(ctx["panel"], months, ctx["sector_of"], ctx["rates"],
                               **{**common, "lookback": lb, "multi_lookbacks": None})
        print(f"  {lb:>2}m  Sharpe {metrics.fmt_num(metrics.sharpe(r.excess))}"
              f"{'   <- a priori' if lb == C.LOOKBACK_MONTHS else ''}")

    print("\nCost sensitivity (every trading and roll cost scaled):")
    for mult in (0, 1, 2, 4):
        r = model.run_backtest(ctx["panel"], months, ctx["sector_of"], ctx["rates"],
                               **P.scale_costs(common, mult))
        print(f"  x{mult}  Sharpe {metrics.fmt_num(metrics.sharpe(r.excess))}"
              f"   cost drag {metrics.fmt_pct(sum(r.cost) / len(r.cost) * 12)}/yr")

    C.OUTPUT.mkdir(parents=True, exist_ok=True)
    out = C.OUTPUT / "baseline_monthly.csv"
    eq_x, eq_t = metrics.equity_curve(base.excess), metrics.equity_curve(base.total)
    with out.open("w", encoding="utf-8", newline="") as f:
        f.write("month,excess_return,total_return,gross_return,cost,turnover,"
                "n_markets,equity_excess,equity_total\n")
        for i, m in enumerate(base.months):
            f.write(f"{m},{base.excess[i]:.8f},{base.total[i]:.8f},{base.gross[i]:.8f},"
                    f"{base.cost[i]:.8f},{base.turnover[i]:.6f},{base.n_markets[i]},"
                    f"{eq_x[i]:.6f},{eq_t[i]:.6f}\n")
    print(f"\nHeadline monthly series exported: {out}")


# ---------------------------------------------------------------------------
# research
# ---------------------------------------------------------------------------
def cmd_research(n_sims=200, draws=200):
    """Run the studies in mf/research.py and write output/research.md."""
    from mf import research as R
    conn = store.connect(C.DB_PATH)
    ctx = P.load_context(conn)
    conn.close()
    f2 = lambda x: metrics.fmt_num(x, 2)            # noqa: E731
    pc = lambda x, d=1: metrics.fmt_pct(x, d)       # noqa: E731

    def ci(r, key):
        if key + "_ci" not in r:
            return ""
        lo, hi = r[key + "_ci"]
        return f"{r[key]:+.2f} [{lo:+.2f}, {hi:+.2f}]"

    L = ["# Research notes", "",
         f"*Generated {dt.date.today()} by `python run.py research`; every number is "
         "recomputed from the data on each run. Sharpe ratios are annualised from monthly "
         "returns over cash. Intervals are 95%, from a 12-month block bootstrap.*", ""]

    # 1. averaging -------------------------------------------------------------
    print("1/9 averaging bias ...")
    rows, fid = R.averaging_bias(n_sims=n_sims)
    rw = next(r for r in rows if r["data"].startswith("Random") and r["prices"] == "monthly average"
              and not r["lag"])
    L += ["## 1. Free data can manufacture a trend-following track record", "",
          "Several free datasets publish the AVERAGE of each month's prices, not the month-end "
          "price (the World Bank Pink Sheet; BIS and ECB series by default). The average of "
          "month t already contains the second half of month t, so average-to-average returns "
          "are autocorrelated by construction (Working, 1960), and a trend rule harvests that "
          "autocorrelation as if it were a trend.", "",
          f"On **pure random walks** a 25-market 12-month trend index shows a Sharpe ratio of "
          f"**{f2(rw['sharpe'])}** on monthly averages (5th-95th percentile {f2(rw['p5'])} to "
          f"{f2(rw['p95'])}) and about zero on month-end prices. Lagging the signal one month "
          "removes the effect.", "",
          "| Data | Monthly price | Signal lag | Sharpe | Range / period |", "|---|---|---|---|---|"]
    for r in rows:
        rng = f"{f2(r['p5'])} to {f2(r['p95'])}" if "p5" in r else r.get("period", "")
        L.append(f"| {r['data']} | {r['prices']} | {'+1 month' if r['lag'] else 'none'} "
                 f"| {f2(r['sharpe'])} | {rng} |")
    L += ["", "World Bank averages are also misdated. Correlation of each World Bank monthly "
          "return with the true futures return of the same month, of the NEXT month's World Bank "
          "return with it, and of a free month-end source (Yahoo front month) for comparison:", "",
          "| Market | World Bank, same month | World Bank, next month | Yahoo month-end |",
          "|---|---|---|---|"]
    for r in fid:
        L.append(f"| {r['market']} | {f2(r['same_month'])} | {f2(r['next_month'])} | {f2(r['yahoo'])} |")
    L += [""]

    # 2. design -------------------------------------------------------------------
    print("2/9 design ...")
    rows, d0, d1 = R.design(ctx)
    L += ["## 2. Building the index one choice at a time", "",
          f"Each step adds one choice to the step above; alternatives change one thing in the "
          f"final index. Net of costs, {d0[:7]} to {d1[:7]}. The change columns give the "
          f"difference in Sharpe ratio against the previous step (for alternatives: against the "
          f"index), with a paired 95% interval, before {C.DESIGN_SPLIT[:4]} and from "
          f"{C.DESIGN_SPLIT[:4]}.", "",
          "The choices come from the trend-following literature and from first principles "
          "(README, *Why it is built this way*). The design was refined while looking at the "
          f"whole sample, so the period from {C.DESIGN_SPLIT[:4]} is a pseudo out-of-sample check, "
          "not a clean holdout. Read the intervals accordingly: most individual choices cannot be "
          "distinguished from zero on their own; the case for them is the prior reasoning plus "
          "the consistency of the direction.", "",
          f"| Step | Sharpe before {C.DESIGN_SPLIT[:4]} | from {C.DESIGN_SPLIT[:4]} | Full | Worst drawdown "
          f"| Change before {C.DESIGN_SPLIT[:4]} | Change from {C.DESIGN_SPLIT[:4]} |",
          "|---|---|---|---|---|---|---|"]
    for i, r in enumerate(rows):
        if r["kind"] == "alternative" and rows[i - 1]["kind"] == "step":
            L.append("| *Alternatives (change vs the index):* | | | | | | |")
        if r["kind"] == "reference":
            L.append("| *Reference:* | | | | | | |")
        L.append(f"| {r['step']} | {f2(r['design'])} | {f2(r['oos'])} | {f2(r['full'])} | "
                 f"{pc(r['maxdd'])} | {ci(r, 'd_design')} | {ci(r, 'd_oos')} |")
    L += ["", "Two choices were kept against the numbers, by judgment: the long/short sign "
          "signal rather than continuous trend strength (strength did better after "
          f"{C.DESIGN_SPLIT[:4]} and trades half as much, but worse before), and carry capped at 25% "
          "so the index stays a trend strategy. The volatility floor costs a little Sharpe; it is "
          "kept as protection against volatility regimes that end abruptly (section 7).", ""]

    # 3. persistence -------------------------------------------------------------------
    print("3/9 persistence ...")
    ps = R.persistence(ctx)
    L += ["## 3. Do markets that trend badly keep trending badly?", "",
          f"Every candidate market's trend Sharpe ratio before {ps['split'][:4]} against its trend "
          f"Sharpe ratio after ({ps['n']} markets, equal risk, gross):", "",
          f"- Rank correlation between the two halves: **{f2(ps['spearman'])}** (one-sided "
          f"permutation p-value {ps['p_value']:.2f}).",
          f"- Dropping the worst quarter on first-half evidence ({', '.join(sorted(ps['dropped']))}) "
          f"would have moved the second-half Sharpe from {f2(ps['second_all'])} to "
          f"{f2(ps['second_without_worst'])}.", "",
          "There is no evidence that a market's past trend performance predicts its future trend "
          "performance. The test is not powerful (a few dozen noisy Sharpe ratios), so it cannot "
          "rule out a small effect, but it gives no reason to prune markets by their backtest; "
          "the gain from doing so here is within noise.", "",
          "| Market | Sector | Before | After |", "|---|---|---|---|"]
    for r in sorted(ps["rows"], key=lambda r: r["first"]):
        L.append(f"| {r['name']} | {r['sector']} | {f2(r['first'])} | {f2(r['second'])} |")
    L += [""]

    # 4. breadth ----------------------------------------------------------------------
    print("4/9 breadth ...")
    b = R.breadth(ctx)
    curve, w_all = R.sharpe_vs_n(b["streams"], ctx["months"], draws=draws)
    pre, post = b["pre"], b["post"]
    tot = post["realised"] - pre["realised"]
    L += ["## 4. Why trend following weakened after 2008", "",
          "Scale every market's trend P&L to the same volatility and hold them in equal risk. "
          "The portfolio Sharpe ratio is then approximately", "",
          "    SR = S_bar x sqrt(N / (1 + (N - 1) rho_bar))", "",
          "with S_bar the average single-market Sharpe ratio and rho_bar the average correlation "
          "between the markets' trend P&Ls (exact for equal-volatility streams in equal weights). "
          "The square-rooted factor is the diversification multiplier; its square is the "
          f"effective number of independent bets. All {len(b['streams'])} candidates, trend "
          "signal only, gross.", "",
          f"| | Before {C.SUBPERIOD_SPLIT[:4]} | From {C.SUBPERIOD_SPLIT[:4]} |", "|---|---|---|",
          f"| Average single-market Sharpe (S_bar) | {f2(pre['s_bar'])} | {f2(post['s_bar'])} |",
          f"| Average correlation of trend P&Ls (rho_bar) | {f2(pre['rho_bar'])} | {f2(post['rho_bar'])} |",
          f"| Effective independent bets | {pre['enb']:.1f} | {post['enb']:.1f} |",
          f"| Predicted portfolio Sharpe | {f2(pre['predicted'])} | {f2(post['predicted'])} |",
          f"| Realised portfolio Sharpe | {f2(pre['realised'])} | {f2(post['realised'])} |",
          f"| Average correlation of the markets | {f2(pre['rho_asset'])} | {f2(post['rho_asset'])} |",
          f"| Average position alignment | {f2(pre['concordance'])} | {f2(post['concordance'])} |",
          "",
          f"The realised Sharpe ratio fell by {f2(-tot)}: about {f2(-b['d_sbar'])} from weaker "
          f"trends in the average market and {f2(-b['d_dm'])} from lost diversification "
          f"(effective bets {pre['enb']:.1f} -> {post['enb']:.1f}). Most of the extra correlation "
          "comes from positions being aligned while the markets co-move; the rest from positions "
          "aligning precisely when markets move together (shared macro trends):", "",
          f"| Trend co-movement | Before | From |", "|---|---|---|",
          f"| total | {pre['comove_total']:.3f} | {post['comove_total']:.3f} |",
          f"| alignment x market co-movement | {pre['comove_product']:.3f} | {post['comove_product']:.3f} |",
          f"| shared trends (remainder) | {pre['comove_interaction']:.3f} | {post['comove_interaction']:.3f} |",
          "", "Average single-market trend Sharpe by sector:", "",
          "| Sector | Before | From |", "|---|---|---|"]
    for sec, v in b["by_sector"].items():
        L.append(f"| {sec} | {f2(v['pre'])} | {f2(v['post'])} |")
    L += ["", "How many markets are enough? Equal-risk portfolios of N randomly drawn candidates "
          f"({draws} draws each, full sample, gross):", "",
          "| N | Median Sharpe | 10th-90th percentile | Identity |", "|---|---|---|---|"]
    for c in curve:
        L.append(f"| {c['n']} | {f2(c['median'])} | {f2(c['p10'])} to {f2(c['p90'])} | {f2(c['law'])} |")
    L += ["", f"{w_all['n']} markets are worth about {w_all['enb']:.0f} independent bets; beyond "
          "about 20 markets, extra names mostly narrow the spread of outcomes.", ""]

    # 5. stacking ----------------------------------------------------------------------
    print("5/9 stacking ...")
    st = R.stacking(ctx)
    L += ["## 5. Stacking the index on equities", "",
          f"100% S&P 500 total return (fully funded) plus the index as a futures overlay scaled to "
          f"each volatility, {st['start'][:7]} to {st['end'][:7]}, after costs and "
          f"{C.STACK_FINANCING_SPREAD_BPS} bps a year of financing drag, before fees:", "",
          "| Overlay volatility | Sharpe | Volatility | Worst drawdown | Annual return |",
          "|---|---|---|---|---|"]
    for r in st["rows"]:
        L.append(f"| {pc(r['overlay_vol'], 0)} | {f2(r['sharpe'])} | {pc(r['vol'])} | "
                 f"{pc(r['maxdd'])} | {pc(r['cagr'])} |")
    L += ["", f"In the {st['crash_n']} months the S&P 500 fell more than 5% (average "
          f"{pc(st['crash_equity'])}), the index returned {pc(st['crash_index'], 2)} on average. "
          "The stacked portfolio carries about the index's gross futures exposure on top of "
          "fully invested equities, so it needs the margin and cash buffer in section 7.", ""]

    # 6. rebalancing ------------------------------------------------------------------------
    print("6/9 rebalancing (daily data) ...")
    rb, nm = R.rebalancing()
    L += ["## 6. Weekly or monthly? And what if trades execute a day late?", "",
          f"Only the commodities have daily data, so this uses the {nm} commodity futures, "
          "1991 to March 2024, the index's trend signal, equal risk per market and the same "
          "trading costs:", "",
          "| Rebalancing | Sharpe | Annual return | Volatility | Worst drawdown | Turnover a year "
          "| Cost a year | 1-year vol, 10th-90th pct |", "|---|---|---|---|---|---|---|---|"]
    for r in rb:
        L.append(f"| {r['label']} | {f2(r['sharpe'])} | {pc(r['cagr'])} | {pc(r['vol'])} | "
                 f"{pc(r['maxdd'])} | {r['turnover']:.0f}x | {pc(r['cost'], 2)} | "
                 f"{pc(r['vol_p10'])}-{pc(r['vol_p90'])} |")
    L += ["", "Weekly rebalancing mainly improves risk: a shallower worst drawdown and steadier "
          "volatility, for twice the turnover. Executing a day after the signal costs a little "
          "at either frequency. The index is monthly because its bond and currency data are "
          "monthly; a live implementation would reasonably rebalance weekly. One sleeve and one "
          "history: treat the size of the gain as uncertain.", ""]

    # 7. practitioner views ------------------------------------------------------------------
    print("7/9 practitioner views ...")
    pr = R.practitioner(ctx)
    L += ["## 7. Practitioner views", "",
          "**After fees.** The index is reported before any management fee. With a flat annual fee (no performance fee):", "",
          "| Management fee | Annual return | Sharpe | Worst drawdown |", "|---|---|---|---|"]
    for r in pr["fees"]:
        L.append(f"| {r['label']} | {pc(r['cagr'])} | {f2(r['sharpe'])} | {pc(r['maxdd'])} |")
    L += ["", f"**Leverage and margin.** Positions target 15% ex-ante volatility with gross notional "
          f"capped at {C.HEADLINE['max_leverage']:.0f}x. Estimated exchange margin (bonds "
          f"{pc(C.MARGIN_RATE['bond'], 0)}, currencies {pc(C.MARGIN_RATE['fx'], 0)}, commodities "
          f"{pc(C.MARGIN_RATE['commodity'], 0)} of notional) uses a median {pc(pr['margin']['median'], 0)} "
          f"of capital, {pc(pr['margin']['p95'], 0)} at the 95th percentile, "
          f"{pc(pr['margin']['max'], 0)} at most.", "",
          "| Decade | Realised volatility | Sharpe | Median gross exposure | Months at the cap |",
          "|---|---|---|---|---|"]
    for r in pr["decades"]:
        L.append(f"| {r['decade']} | {pc(r['vol'])} | {f2(r['sharpe'])} | {r['gross']:.1f}x | {pc(r['capped'], 0)} |")
    L += ["", "**Concentration.** Share of the index's gross profit by market (top ten):", "",
          "| Market | Share |", "|---|---|"]
    for n, v in pr["attribution"][:10]:
        L.append(f"| {n} | {pc(v, 0)} |")
    nj = pr["no_japan"]
    L += ["", f"Japan (yen and JGB) contributed {pc(pr['japan_share'], 0)} of the profit. Without "
          f"those two markets the Sharpe ratio is {f2(nj['full'])} ({f2(nj['pre'])} before "
          f"{C.SUBPERIOD_SPLIT[:4]}, {f2(nj['post'])} after): the yen's long trends and decades of "
          "falling Japanese yields were a large, possibly unrepeatable, source of profit. Low "
          "measured volatility can also end abruptly (the Swiss franc floor in 2015, the end of "
          "yield-curve control), which is why the index floors each market's risk estimate at "
          "half its sector's median.", "",
          f"**The approximated period.** Commodity data after {C.FUTURES_END[:7]} comes from Yahoo "
          f"front-month prices mapped onto the futures series. Sharpe to {C.FUTURES_END[:7]}: "
          f"{f2(pr['splice_period']['sharpe_to'])}; since then ({pr['splice_period']['months_after']} "
          f"months): {f2(pr['splice_period']['sharpe_after'])}.", ""]

    # 8. data checks ----------------------------------------------------------------------
    print("8/9 data checks ...")
    dc = R.data_checks(ctx)
    L += ["## 8. How close are the constructed series to real futures?", "",
          "Bonds are built from yields and currencies from spot rates plus the interest-rate "
          "differential. Correlation of their monthly returns with the front-month futures "
          "(Yahoo, month-end, 2001 on):", "",
          "| Market | Future | Correlation | Months |", "|---|---|---|---|"]
    for r in dc:
        L.append(f"| {r['market']} | {r['future']} | {f2(r['corr'])} | {r['n']} |")
    L += ["", "Commodities after March 2024 (regression of futures returns on Yahoo front-month "
          "returns; tracking error measured on a holdout after 2019):", "",
          "| Commodity | Correlation | Slope | Tracking error (fit) | Tracking error (holdout) "
          "| Bias (holdout) |", "|---|---|---|---|---|---|"]
    for s, v in sorted(ctx["info"]["splice"].items()):
        L.append(f"| {C.CANDIDATES[s][0]} | {f2(v['fidelity'])} | {f2(v['beta'])} | {pc(v['te'])} "
                 f"| {pc(v['te_holdout'])} | {pc(v['bias_holdout'])} |"
                 + ("" if v["spliced"] else " not extended |"))
    L += [""]

    # 9. the universe over time -----------------------------------------------------------
    print("9/9 universe ...")
    L += ["## 9. The universe over time", "",
          f"Re-selected every January from point-in-time data (liquidity at least "
          f"{C.SELECTION['min_volume']}k contracts a day, return correlation below "
          f"{C.SELECTION['max_correlation']:.2f} with every more liquid member, incumbents kept "
          f"unless the correlation exceeds it by {C.SELECTION['incumbent_margin']:.2f}). "
          "Liquidity ranks use today's volumes, as historical volumes are not freely available.", "",
          "| Review | Markets | Joined | Left |", "|---|---|---|---|"]
    prev = set()
    for m, sel in ctx["info"]["reviews"]:
        sel = set(sel)
        if sel != prev:
            L.append(f"| {m[:4]} | {len(sel)} | "
                     f"{', '.join(sorted(C.CANDIDATES[s][0] for s in sel - prev)) or '-'} | "
                     f"{', '.join(sorted(C.CANDIDATES[s][0] for s in prev - sel)) or '-'} |")
        prev = sel
    L += [""]

    C.OUTPUT.mkdir(parents=True, exist_ok=True)
    out = C.OUTPUT / "research.md"
    out.write_text("\n".join(L), encoding="utf-8")
    print(f"Research notes written: {out}")


# ---------------------------------------------------------------------------
def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    cmd = args[0] if args else "all"
    force = "--force" in sys.argv
    if cmd == "fetch":
        cmd_fetch(force)
    elif cmd == "sanity":
        cmd_sanity()
    elif cmd == "backtest":
        cmd_backtest()
    elif cmd == "report":
        from mf import report_page
        report_page.build()
    elif cmd == "research":
        cmd_research()
    elif cmd == "all":
        cmd_fetch(force)
        if cmd_sanity():
            raise SystemExit("Sanity problems found; fix the data before backtesting.")
        cmd_backtest()
        from mf import report_page
        report_page.build()
        cmd_research()
    else:
        print(__doc__)
        raise SystemExit(1)


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:           # data problems: a message, not a traceback
        raise SystemExit(f"ERROR: {exc}")
