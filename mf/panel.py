"""
Panel construction, universe selection, carry, and the canonical variant set.

This module exists so that `run.py` (console) and `mf/report_page.py` (HTML)
cannot disagree about what "the index" is: both call `load_context`.
"""

import config as C
from mf import metrics, model, store


def month_range(first: str, last: str) -> list[str]:
    """Every month from `first` to `last` inclusive, with no holes.

    Building the grid from the CALENDAR rather than from the data means a
    month-over-month return can never silently span two months: a missing
    observation shows up as an absent price, not as a shifted row.
    """
    y, m = int(first[:4]), int(first[5:7])
    ly, lm = int(last[:4]), int(last[5:7])
    out = []
    while (y, m) <= (ly, lm):
        out.append(f"{y:04d}-{m:02d}-01")
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return out


# ---------------------------------------------------------------------------
# commodities: futures to FUTURES_END, calibrated Yahoo splice after
# ---------------------------------------------------------------------------
def _ols(x, y):
    mx, my = sum(x) / len(x), sum(y) / len(y)
    vx = sum((u - mx) ** 2 for u in x)
    beta = sum((u - mx) * (v - my) for u, v in zip(x, y)) / vx if vx else 1.0
    return my - beta * mx, beta


def _tracking_error(x, y, alpha, beta):
    res = [v - (alpha + beta * u) for u, v in zip(x, y)]
    m = sum(res) / len(res)
    return (sum((r - m) ** 2 for r in res) / (len(res) - 1) * 12) ** 0.5, m * 12


def splice_commodity(fut_idx: dict[str, float], yahoo_px: dict[str, float]):
    """Extend a futures excess-return index past C.FUTURES_END with Yahoo
    front-month returns mapped onto the futures series by regression.

    The two are different instruments: pysystemtrade holds a deferred contract
    (WTI and grains roughly nine months out), Yahoo quotes the front month,
    which is more volatile and jumps at each roll. So futures returns are
    regressed on Yahoo returns over C.SPLICE_CALIBRATION, r_fut = a + b r_yahoo,
    correcting both the average roll gap (a) and the difference in volatility
    (b). Returns (index, info); info reports the fit, the in-sample tracking
    error, and an honest holdout check (fit on the calibration window up to
    C.SPLICE_HOLDOUT, tracking error and bias measured after it).
    """
    fut_r = model.monthly_returns(fut_idx, sorted(fut_idx))
    yf_r = model.monthly_returns(yahoo_px, sorted(yahoo_px))
    lo, hi = C.SPLICE_CALIBRATION
    common = [m for m in sorted(fut_r) if lo <= m <= hi and m in yf_r]
    info = {"fidelity": float("nan"), "alpha": float("nan"), "beta": float("nan"),
            "te": float("nan"), "te_holdout": float("nan"), "bias_holdout": float("nan"),
            "spliced": False, "overlap": len(common)}
    out = {m: v for m, v in fut_idx.items() if m <= C.FUTURES_END}
    if len(common) < 60 or C.FUTURES_END not in out:
        return out, info
    x = [yf_r[m] for m in common]
    y = [fut_r[m] for m in common]
    info["fidelity"] = metrics.correlation(x, y)
    info["alpha"], info["beta"] = _ols(x, y)
    info["te"], _ = _tracking_error(x, y, info["alpha"], info["beta"])
    fit = [m for m in common if m < C.SPLICE_HOLDOUT]
    test = [m for m in common if m >= C.SPLICE_HOLDOUT]
    if len(fit) >= 60 and len(test) >= 24:
        a_, b_ = _ols([yf_r[m] for m in fit], [fut_r[m] for m in fit])
        info["te_holdout"], info["bias_holdout"] = _tracking_error(
            [yf_r[m] for m in test], [fut_r[m] for m in test], a_, b_)
    if info["fidelity"] < C.SPLICE_MIN_FIDELITY:
        return out, info
    level = out[C.FUTURES_END]
    for m in sorted(yf_r):
        if m <= C.FUTURES_END:
            continue
        level *= 1.0 + info["alpha"] + info["beta"] * yf_r[m]
        out[m] = level
    info["spliced"] = True
    return out, info


# ---------------------------------------------------------------------------
# candidate panel and universe selection
# ---------------------------------------------------------------------------
def build_candidates(conn):
    """{symbol: {month: level}} for every candidate market, plus splice info.

    Commodities are futures excess-return indices (spliced after FUTURES_END),
    bonds synthetic total-return indices from month-end yields, FX month-end
    spot. An FX market is live only while both policy rates exist, since its
    carry is a large part of a currency future's return.
    """
    panel: dict[str, dict[str, float]] = {}
    splice: dict[str, dict] = {}
    for sym in C.COMMODITIES:
        fut = dict(store.load_prices(conn, sym))
        if fut:
            panel[sym], splice[sym] = splice_commodity(
                fut, dict(store.load_prices(conn, "yf_" + sym)))
    for series, (sym, _, maturity, _) in C.BONDS.items():
        ylds = dict(store.load_yields(conn, series))
        if len(ylds) >= 13:
            panel[sym] = model.bond_price_index(
                ylds, sorted(ylds), maturity, zero_coupon=series in C.ZERO_COUPON_BONDS)
    usd = dict(store.load_yields(conn, "CBPOL_US"))
    pre, post, eur_sym = C.POLICY_RATE_EUR
    codes = {sym: [a] for a, sym in C.POLICY_RATE_AREAS.items()}
    codes[eur_sym] = [pre, post]
    for sym, _, _ in C.FX_PAIRS.values():
        px = dict(store.load_prices(conn, sym))
        rate_months = set()
        for code in codes.get(sym, []):
            rate_months |= {m for m, _ in store.load_yields(conn, f"CBPOL_{code}")}
        first_ok = min((m for m in rate_months if m in usd), default=None)
        if px and first_ok:
            panel[sym] = {m: v for m, v in px.items() if m >= first_ok}
    missing = sorted(set(C.CANDIDATES) - set(panel))
    if missing:
        raise RuntimeError(f"No data for {missing}. Run: python run.py fetch")
    return panel, splice


def grid(panel):
    """Months from BACKTEST_START to the last month every CONTINUING market has
    data. Markets whose data stops earlier (commodities that could not be
    spliced) simply leave the index then."""
    continuing = [max(v) for v in panel.values() if max(v) > C.FUTURES_END]
    return month_range(C.BACKTEST_START, min(continuing))


def select_universe(panel, months):
    """The same rule applied once to the whole sample (the static universe,
    kept as a comparison): data must reach the present."""
    rets = {s: model.monthly_returns(panel[s], months) for s in panel}
    alive = {s: r for s, r in rets.items() if max(panel[s]) >= months[-1]}
    kept, rows = _greedy_select(alive)
    for s in sorted(set(rets) - set(alive), key=lambda s: -C.LIQUIDITY[s]):
        name, sector = C.CANDIDATES[s]
        rows.append({"symbol": s, "name": name, "sector": sector, "volume": C.LIQUIDITY[s],
                     "kept": False, "reason": f"no usable data after {max(panel[s])[:7]}"})
    return sorted(kept, key=lambda s: -C.LIQUIDITY[s]), rows


def selection_schedule(cand, months):
    """Point-in-time universe, re-selected every January (C.SELECTION).

    At each review only data up to the previous month is used: a market needs
    `min_history` monthly returns and data up to the review, and correlations
    are measured over each pair's whole shared history so far (`lookback` =
    None) or the last `lookback` months. Incumbents are preferred: a market
    already in the index stays unless its correlation with a more liquid
    member exceeds the threshold by `incumbent_margin`. Correlations estimated
    from a few years of data are noisy, and without this the rule churns
    between near-duplicates. Liquidity ranks are today's volumes (historical
    volumes are not freely available) -- a stated limitation.
    Returns ({month: set of symbols}, [(review month, chosen)]).
    """
    rule = C.SELECTION
    rets = {s: model.monthly_returns(cand[s], months) for s in cand}
    schedule, reviews, current = {}, [], set()
    for i, m in enumerate(months):
        if i == 0 or m[5:7] == "01":
            lo = 0 if not rule.get("lookback") else max(0, i - rule["lookback"])
            window = months[lo:i]
            eligible = {s: {w: rets[s][w] for w in window if w in rets[s]} for s in cand}
            eligible = {s: r for s, r in eligible.items()
                        if len(r) >= rule["min_history"] and i > 0 and months[i - 1] in r}
            current, _ = _greedy_select(eligible, incumbents=current)
            reviews.append((m, sorted(current)))
        schedule[m] = current
    return schedule, reviews


def _greedy_select(rets, incumbents=frozenset()):
    """In order of liquidity, keep a market if it is liquid enough and its
    returns correlate less than the threshold with every market kept so far
    (incumbents get the extra `incumbent_margin`)."""
    rule = C.SELECTION
    kept, rows = [], []
    for s in sorted(rets, key=lambda s: -C.LIQUIDITY[s]):
        name, sector = C.CANDIDATES[s]
        row = {"symbol": s, "name": name, "sector": sector, "volume": C.LIQUIDITY[s],
               "kept": False, "reason": ""}
        if C.LIQUIDITY[s] < rule["min_volume"]:
            row["reason"] = f"volume below {rule['min_volume']}k contracts/day"
            rows.append(row)
            continue
        worst, worst_with = 0.0, None
        for k in kept:
            a, b, sh = metrics.align(rets[s], rets[k])
            if len(sh) >= 24:
                c = metrics.correlation(a, b)
                if abs(c) > abs(worst):
                    worst, worst_with = c, k
        limit = rule["max_correlation"] + (rule.get("incumbent_margin", 0.0)
                                           if s in incumbents else 0.0)
        if abs(worst) >= limit:
            row["reason"] = f"correlation {worst:+.2f} with {C.CANDIDATES[worst_with][0]}"
        else:
            row["kept"] = True
            row["reason"] = (f"highest correlation {worst:+.2f}"
                             + (f" ({C.CANDIDATES[worst_with][0]})" if worst_with else ""))
            kept.append(s)
        rows.append(row)
    return set(kept), rows


def build_panel(conn, universe="selected"):
    """(panel, months, holes, info). With C.SELECTION["mode"] == "annual" the
    panel holds every candidate and info["schedule"] says which may trade each
    month; with "static" the panel is the full-sample selection. `holes` lists
    missing months INSIDE a market's own history (data errors)."""
    cand, splice = build_candidates(conn)
    months = grid(cand)
    kept, rows = select_universe(cand, months)
    schedule, reviews = selection_schedule(cand, months)
    if universe == "all" or C.SELECTION["mode"] == "annual":
        panel = cand
    else:
        panel = {s: cand[s] for s in kept}
    holes = {}
    for s, v in panel.items():
        gap = [m for m in months if min(v) <= m <= max(v) and m not in v]
        if gap:
            holes[s] = gap
    return panel, months, holes, {
        "splice": splice, "selection": rows, "candidates": cand, "static": kept,
        "schedule": schedule if C.SELECTION["mode"] == "annual" else None,
        "reviews": reviews}


def sector_map() -> dict[str, str]:
    return {s: sec for s, (_, sec) in C.CANDIDATES.items()}


def averaged_symbols(conn) -> set[str]:
    """Symbols whose stored prices come from a monthly-AVERAGE source."""
    return {sym for sym, srcs in store.sources(conn, "prices").items()
            if srcs & C.AVERAGED_SOURCES}


# ---------------------------------------------------------------------------
# carry: spot / yields -> futures-equivalent returns
# ---------------------------------------------------------------------------
def _monthly_accrual(rate_pct: float) -> float:
    return (1.0 + rate_pct / 100.0) ** (1.0 / 12.0) - 1.0


def fx_carry(conn, months) -> dict[str, dict[str, float]]:
    """Covered interest parity: a long foreign-currency future earns the spot
    move plus (r_foreign - r_usd)/12, at the rates known at the START of the
    month."""
    usd = dict(store.load_yields(conn, "CBPOL_US"))
    pre, post, eur_sym = C.POLICY_RATE_EUR
    eur = dict(store.load_yields(conn, f"CBPOL_{pre}"))
    eur.update(store.load_yields(conn, f"CBPOL_{post}"))
    foreign = {sym: dict(store.load_yields(conn, f"CBPOL_{area}"))
               for area, sym in C.POLICY_RATE_AREAS.items()}
    foreign[eur_sym] = eur
    out: dict[str, dict[str, float]] = {}
    for sym, rf in foreign.items():
        out[sym] = {cur: (rf[prev] - usd[prev]) / 100.0 / 12.0
                    for prev, cur in zip(months, months[1:])
                    if prev in rf and prev in usd}
    return out


def funding_rates(conn) -> dict[str, dict[str, float]]:
    """{bond yield series: {month: local short rate, %}} from C.BOND_FUNDING
    (earlier codes take precedence where both exist)."""
    out = {}
    for series, codes in C.BOND_FUNDING.items():
        rate: dict[str, float] = {}
        for code in reversed(codes):
            rows = (store.load_rates(conn).items() if code == "TBILL"
                    else store.load_yields(conn, f"CBPOL_{code}"))
            rate.update(dict(rows))
        out[series] = rate
    return out


def bond_funding(funding, months) -> dict[str, dict[str, float]]:
    """Duration-model bond returns are TOTAL returns; a bond future earns the
    total return minus its LOCAL funding rate, known at the start of the month."""
    out = {}
    for series, (sym, _, _, _) in C.BONDS.items():
        rates = funding[series]
        out[sym] = {cur: -_monthly_accrual(rates[prev])
                    for prev, cur in zip(months, months[1:]) if prev in rates}
    return out


def build_carry_adj(conn, months):
    """{symbol: {month: return adjustment}}: FX carry and bond funding.
    Commodity futures returns already include their roll yield."""
    adj = {}
    adj.update(fx_carry(conn, months))
    adj.update(bond_funding(funding_rates(conn), months))
    return adj


# ---------------------------------------------------------------------------
# benchmarks
# ---------------------------------------------------------------------------
def spx_returns(conn, months) -> dict[str, float]:
    """S&P 500 TOTAL returns (dividends reinvested) on the backtest grid."""
    series = dict(store.load_prices(conn, C.SPX_SYMBOL))
    return model.monthly_returns(series, [m for m in months if m in series])


def aqr_tsmom(conn) -> dict[str, dict[str, float]]:
    """AQR TSMOM excess returns by sleeve, plus an ex-equity composite (equal
    weight of the commodity, bond and FX factors)."""
    out = {sec: dict(store.load_benchmark(conn, name))
           for sec, name in C.AQR_TSMOM_SLEEVES.items()}
    out["all"] = dict(store.load_benchmark(conn, "AQR_TSMOM"))
    common = set.intersection(*(set(out[s]) for s in C.AQR_TSMOM_SLEEVES))
    out["ex_equity"] = {m: sum(out[s][m] for s in C.AQR_TSMOM_SLEEVES) / 3
                        for m in common}
    return out


# ---------------------------------------------------------------------------
# variants
# ---------------------------------------------------------------------------
def backtest_kwargs(carry_adj, averaged, carry_signal=None, universe_by_month=None,
                    **overrides) -> dict:
    """The standard cost, carry, timing, signal and sizing settings (C.HEADLINE)."""
    kw = dict(universe_by_month=universe_by_month,
        lookback=C.LOOKBACK_MONTHS, cost_bps=C.COST_BPS_FALLBACK,
        carry_adj=carry_adj, cost_by_market=C.COST_BY_MARKET,
        roll_cost_by_market=C.ROLL_COST_BY_MARKET, cost_eras=C.COST_ERAS,
        overhead_bps_annual=C.ANNUAL_OVERHEAD_BPS, averaged_symbols=averaged,
        vol_window=C.VOL_WINDOW_MONTHS, cluster_of=C.CLUSTERS,
        carry_signal=carry_signal, **C.HEADLINE,
    )
    kw.update(overrides)
    return kw


def ctx_kwargs(ctx, **overrides) -> dict:
    return backtest_kwargs(ctx["carry"], ctx["averaged"], ctx["carry_signal"],
                           ctx["info"]["schedule"], **overrides)


def scale_costs(kw: dict, mult: float) -> dict:
    """Copy of backtest kwargs with every trading and roll cost scaled."""
    out = dict(kw)
    out["cost_by_market"] = {k: v * mult for k, v in kw["cost_by_market"].items()}
    out["roll_cost_by_market"] = {k: v * mult for k, v in kw["roll_cost_by_market"].items()}
    return out


def standard_variants(ctx):
    """The canonical variant set, in presentation order. The first row is the
    index; every other row changes one thing. Returns (base, variants,
    stacked, long_only)."""
    panel, months = ctx["panel"], ctx["months"]
    kw = ctx_kwargs(ctx)

    def run(label, pnl=None, **o):
        return model.run_backtest(pnl or panel, months, ctx["sector_of"], ctx["rates"],
                                  label=label, **{**kw, **o})

    vt = C.HEADLINE["target_vol"]
    base = run(f"Trend index ({vt:.0%} volatility)")
    variants = [
        base,
        run("Pure trend (no carry)", carry_weight=0.0),
        run("Trend + 50% carry", carry_weight=0.5),
        run("Continuous trend strength instead of sign", signal_type="strength"),
        run("Sector risk budget instead of clusters", weight_scheme="sector_inverse_vol"),
        run("12-month momentum only", multi_lookbacks=None),
        run("No volatility floor", vol_floor=None),
        run("Fixed universe (one full-sample selection)",
            pnl={s: panel[s] for s in ctx["info"]["static"]}, universe_by_month=None),
        run(f"All {len(panel)} candidates, no selection", universe_by_month=None),
        run("No volatility target (unlevered)", target_vol=None),
        run("Naive: 12m moving average, equal notional, unlevered",
            signal_type="sma", signal_on="price", multi_lookbacks=None,
            weight_scheme="equal", carry_weight=0.0, target_vol=None, vol_floor=None),
    ]
    long_only = model.run_long_only(panel, months, ctx["sector_of"], ctx["rates"],
                                    label="Always long, same sizing",
                                    **{**kw, "carry_weight": 0.0})
    variants.append(long_only)
    stacked = None
    if ctx["spx"]:
        stacked = model.return_stacked(
            base, ctx["spx"], ctx["rates"],
            financing_spread_bps=C.STACK_FINANCING_SPREAD_BPS,
            label=f"100% S&P 500 + 100% trend index")
    return base, variants, stacked, long_only


# ---------------------------------------------------------------------------
# diagnostics
# ---------------------------------------------------------------------------
def market_trend_returns(px, months, lookback, lagged, carry=None):
    """One market traded alone with the headline signal and timing, gross of
    costs: ({month: position x return}, {month: return}, {month: signal})."""
    rets = model.monthly_returns(px, months)
    if carry:
        rets = {m: r + carry.get(m, 0.0) for m, r in rets.items()}
    h = C.HEADLINE   # trend part of the signal only (carry excluded here)
    sigs, _ = model.build_signals(
        {"x": px}, months, {"x": rets}, lookback,
        multi_lookbacks=h.get("multi_lookbacks"), signal_type=h.get("signal_type", "sma"),
        signal_on=h.get("signal_on", "price"), averaged_symbols={"x"} if lagged else None)
    sig = sigs["x"]
    out = {}
    for prev, cur in zip(months, months[1:]):
        if prev in sig and cur in rets:
            out[cur] = sig[prev] * rets[cur]
    return out, rets, sig


def per_market_stats(panel, months, carry_adj, averaged, lookback=None):
    """Trend vs buy-and-hold for each market on its own."""
    lookback = lookback or C.LOOKBACK_MONTHS
    rows = []
    for s in sorted(panel):
        trend_by_m, rets, sig = market_trend_returns(
            panel[s], months, lookback, s in averaged, carry_adj.get(s))
        ms = sorted(trend_by_m)
        if len(ms) < 24:
            continue
        trend = [trend_by_m[m] for m in ms]
        hold = [rets[m] for m in ms]
        seq = [1 if sig[m] > 0 else -1 if sig[m] < 0 else 0 for m in months if m in sig]
        name, sector = C.CANDIDATES[s]
        rows.append({
            "symbol": s, "name": name, "sector": sector,
            "trend_cagr": metrics.cagr(trend), "hold_cagr": metrics.cagr(hold),
            "trend_sharpe": metrics.sharpe(trend), "hold_sharpe": metrics.sharpe(hold),
            "vol": metrics.ann_vol(trend),
            "flips": sum(1 for a, b in zip(seq, seq[1:]) if a != b),
            "n": len(trend), "averaged": s in averaged, "start": ms[0], "end": ms[-1],
        })
    return rows


def sleeve_returns(result, panel, months, carry_adj, sector_of):
    """Gross P&L of a backtest split by sector: {sector: {month: return}}."""
    rets = {s: model.monthly_returns(panel[s], months) for s in panel}
    out: dict[str, dict[str, float]] = {sec: {} for sec in C.SECTORS}
    for m in result.months:
        for s in panel:
            w = result.positions[s].get(m)
            r = rets[s].get(m)
            if w is None or r is None:
                continue
            r += carry_adj.get(s, {}).get(m, 0.0)
            out[sector_of[s]][m] = out[sector_of[s]].get(m, 0.0) + w * r
    return out


def subperiod_stats(months, returns, split=None):
    """Sharpe / CAGR before and after the split month (default 2008-01)."""
    split = split or C.SUBPERIOD_SPLIT
    pre = [r for m, r in zip(months, returns) if m < split]
    post = [r for m, r in zip(months, returns) if m >= split]
    return {
        "pre": {"sharpe": metrics.sharpe(pre), "cagr": metrics.cagr(pre),
                "n": len(pre), "start": months[0] if months else None},
        "post": {"sharpe": metrics.sharpe(post), "cagr": metrics.cagr(post),
                 "n": len(post), "end": months[-1] if months else None},
    }


# ---------------------------------------------------------------------------
# one entry point for run.py and the report
# ---------------------------------------------------------------------------
def load_context(conn, variants=True) -> dict:
    """Everything a backtest needs, built one way for every caller."""
    panel, months, holes, info = build_panel(conn)
    ctx = {
        "panel": panel, "months": months, "holes": holes, "info": info,
        "all_panel": info["candidates"],
        "rates": store.load_rates(conn),
        "sector_of": sector_map(),
        "averaged": averaged_symbols(conn) & set(info["candidates"]),
        "carry": build_carry_adj(conn, months),
        "spx": spx_returns(conn, months),
        "aqr": aqr_tsmom(conn),
    }
    ctx["carry_signal"] = carry_signals(conn, ctx["all_panel"], months, ctx["carry"])
    ctx["carry_signal_all"] = ctx["carry_signal"]
    if variants:
        base, var, stacked, long_only = standard_variants(ctx)
        ctx.update(base=base, variants=var, stacked=stacked, long_only=long_only)
    return ctx


# ---------------------------------------------------------------------------
# carry signal (for the trend + carry blend)
# ---------------------------------------------------------------------------
def carry_signals(conn, panel, months, carry_adj, vol_window=None):
    """{symbol: {month: carry signal in [-1, 1]}} known at the end of `month`.

    Annualised carry, divided by the market's trailing annual volatility (a
    "carry Sharpe ratio"), capped at +-1:
      FX           policy-rate differential (foreign minus USD)
      bonds        yield minus local funding rate (the term spread)
      commodities  roll yield from the futures curve; not available after
                   the futures data ends, so zero (neutral) then.
    """
    vol_window = vol_window or C.VOL_WINDOW_MONTHS
    usd = dict(store.load_yields(conn, "CBPOL_US"))
    pre, post, eur_sym = C.POLICY_RATE_EUR
    eur = dict(store.load_yields(conn, f"CBPOL_{pre}"))
    eur.update(store.load_yields(conn, f"CBPOL_{post}"))
    fx_rate = {sym: dict(store.load_yields(conn, f"CBPOL_{a}"))
               for a, sym in C.POLICY_RATE_AREAS.items()}
    fx_rate[eur_sym] = eur
    funding = funding_rates(conn)
    raw: dict[str, dict[str, float]] = {}
    for s in panel:
        sector = C.CANDIDATES[s][1]
        if sector == "fx":
            rf = fx_rate.get(s, {})
            raw[s] = {m: rf[m] - usd[m] for m in months if m in rf and m in usd}
        elif sector == "bond":
            series = next(k for k, v in C.BONDS.items() if v[0] == s)
            y, f = dict(store.load_yields(conn, series)), funding[series]
            raw[s] = {m: y[m] - f[m] for m in months if m in y and m in f}
        else:
            raw[s] = dict(store.load_yields(conn, "CARRY_" + s))
    out = {}
    for s in panel:
        rets = model.monthly_returns(panel[s], months)
        for m in rets:
            rets[m] += carry_adj.get(s, {}).get(m, 0.0)
        sig = {}
        for i, m in enumerate(months):
            if m not in raw[s]:
                continue
            v = model.trailing_vol(rets, months, i, vol_window)
            if v:
                sig[m] = max(-1.0, min(1.0, raw[s][m] / 100.0 / (v * 12 ** 0.5)))
        out[s] = sig
    return out
