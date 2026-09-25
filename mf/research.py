"""
Research studies built on the index. `run.py research` prints them and writes
output/research.md.

    averaging_bias()   how much Sharpe ratio monthly-AVERAGE prices manufacture,
                       on pure random walks and on real futures, and how poorly
                       the World Bank's averaged spot prices track futures
    design()           each construction choice, before and from 2008, with
                       paired confidence intervals for its effect
    persistence()      do markets that trended badly keep trending badly?
    practitioner()     fees, margin, leverage, realised volatility, concentration
    data_checks()      yield/spot-based bonds and currencies vs real futures
    rebalancing()      weekly vs monthly rebalancing and a one-day execution lag
    breadth()          the diversification identity: portfolio Sharpe = average
                       single-market Sharpe x diversification multiplier, and
                       which of the two fell after 2008
    stacking()         S&P 500 + k x the index: how much overlay to hold
"""

import math
import random
import statistics as stats

import config as C
from mf import metrics, model, panel as P, sources as S, store


# ---------------------------------------------------------------------------
# 1. The averaging bias
# ---------------------------------------------------------------------------
def _random_walk_panels(n_markets, n_months, seed, days=21, vol=0.20):
    """Pure random walks (zero drift, i.i.d. daily returns): there is NO trend
    to find. Returns month-end and monthly-average panels of the same paths."""
    rng = random.Random(seed)
    months = P.month_range("1900-01-01", "2200-01-01")[:n_months]
    sd = vol / math.sqrt(252)
    end_px, avg_px = {}, {}
    for k in range(n_markets):
        p, e, a = 100.0, {}, {}
        for m in months:
            path = []
            for _ in range(days):
                p *= math.exp(rng.gauss(0.0, sd))
                path.append(p)
            e[m], a[m] = path[-1], sum(path) / days
        end_px[f"rw{k}"], avg_px[f"rw{k}"] = e, a
    return end_px, avg_px, months


def _futures_daily_panels():
    """Month-end and monthly-average versions of the same daily futures
    excess-return indices (every candidate commodity)."""
    end_px, avg_px = {}, {}
    for sym, (_, code, _, _) in C.COMMODITIES.items():
        adj = S.fetch_pst("adjusted_prices_csv", code, C.PST_BASE, C.CACHE)
        mult = S.fetch_pst("multiple_prices_csv", code, C.PST_BASE, C.CACHE)
        daily = S.parse_pst_daily_index(adj, mult)
        end_px[sym] = S._last_per_month(daily)
        buckets: dict[str, list[float]] = {}
        for d, v in daily.items():
            buckets.setdefault(d[:7] + "-01", []).append(v)
        avg_px[sym] = {m: sum(v) / len(v) for m, v in buckets.items() if m in end_px[sym]}
    return end_px, avg_px


def averaging_bias(n_sims=200):
    """Rows (data, prices, lag, sharpe[, p5, p95]) and the World Bank fidelity
    table. Same rule throughout: 12-month moving average, equal notional,
    gross of costs."""
    def sharpe_of(pnl, months, lagged):
        r = model.run_backtest(pnl, months, {s: "commodity" for s in pnl}, {},
                               lookback=12, cost_bps=0, weight_scheme="equal",
                               averaged_symbols=set(pnl) if lagged else None)
        return metrics.sharpe(r.excess), r

    rows = []
    sims = {("month-end", False): [], ("monthly average", False): [],
            ("monthly average", True): []}
    for i in range(n_sims):
        e, a, ms = _random_walk_panels(25, 300, seed=1000 + i)
        sims[("month-end", False)].append(sharpe_of(e, ms, False)[0])
        sims[("monthly average", False)].append(sharpe_of(a, ms, False)[0])
        sims[("monthly average", True)].append(sharpe_of(a, ms, True)[0])
    for (label, lagged), v in sims.items():
        v = sorted(v)
        rows.append({"data": f"Random walks, 25 markets x 25 years ({n_sims} histories)",
                     "prices": label, "lag": lagged, "sharpe": stats.fmean(v),
                     "p5": v[int(0.05 * len(v))], "p95": v[int(0.95 * len(v)) - 1]})

    end_px, avg_px = _futures_daily_panels()
    months = P.month_range("1990-01-01", C.FUTURES_END)
    for label, pnl in (("month-end", end_px), ("monthly average", avg_px)):
        for lagged in (False, True):
            sh, r = sharpe_of(pnl, months, lagged)
            rows.append({"data": f"{len(pnl)} commodity futures, daily data",
                         "prices": label, "lag": lagged, "sharpe": sh,
                         "period": f"{r.months[0][:7]} to {r.months[-1][:7]}"})

    # How well do World Bank averaged spot prices track the futures?
    conn = store.connect(C.DB_PATH)
    fid = []
    for col, sym in C.WB_RESEARCH.items():
        wb = dict(store.load_prices(conn, "wb_" + sym))
        fut = end_px[sym]
        wr = model.monthly_returns(wb, sorted(wb))
        fr = model.monthly_returns(fut, sorted(fut))
        common = [m for m in sorted(fr) if m in wr and m >= "1990-01-01"]
        allm = sorted(wr)
        nxt = {allm[i]: allm[i + 1] for i in range(len(allm) - 1)}
        lagged = [m for m in common if nxt.get(m) in wr]
        yp = dict(store.load_prices(conn, "yf_" + sym))
        yf = model.monthly_returns(yp, sorted(yp))
        cy = [m for m in common if m in yf and m <= C.FUTURES_END]
        fid.append({"market": C.CANDIDATES[sym][0],
                    "same_month": metrics.correlation([fr[m] for m in common],
                                                      [wr[m] for m in common]),
                    "next_month": metrics.correlation([fr[m] for m in lagged],
                                                      [wr[nxt[m]] for m in lagged]),
                    "yahoo": metrics.correlation([fr[m] for m in cy], [yf[m] for m in cy])})
    conn.close()
    return rows, fid


# ---------------------------------------------------------------------------
# 2. Designing the index
# ---------------------------------------------------------------------------
def paired_sharpe_ci(a, b, n=2000, block=12, seed=3):
    """Block-bootstrap 95% interval for Sharpe(a) - Sharpe(b) on aligned
    monthly series (the same resampled months for both)."""
    rng = random.Random(seed)
    N = len(a)
    out = []
    for _ in range(n):
        idx = []
        while len(idx) < N:
            s0 = rng.randrange(N)
            idx.extend((s0 + k) % N for k in range(block))
        idx = idx[:N]
        out.append(metrics.sharpe([a[i] for i in idx]) - metrics.sharpe([b[i] for i in idx]))
    out.sort()
    return out[int(0.025 * n)], out[int(0.975 * n) - 1]


def design(ctx, start="1991-01-01"):
    """Each construction choice added in turn, net of costs, on common months
    from `start`, with the change in Sharpe ratio against the previous step and
    a paired block-bootstrap 95% interval, before and from C.DESIGN_SPLIT.

    The choices come from the literature and first principles, and the design
    was iterated on the full sample, so the later period is a pseudo
    out-of-sample check rather than a clean holdout."""
    kw = P.ctx_kwargs(ctx)
    naive = dict(signal_type="sma", signal_on="price", multi_lookbacks=None,
                 weight_scheme="equal", carry_weight=0.0, target_vol=None,
                 vol_floor=None, universe_by_month=None)
    static = {s: ctx["panel"][s] for s in ctx["info"]["static"]}
    steps = [
        ("Naive: 12m moving average, equal notional, unlevered, fixed universe",
         {**naive, "pnl": static}),
        ("+ equal risk per market", {**naive, "pnl": static, "weight_scheme": "inverse_vol"}),
        ("+ 1/3/12-month momentum signal",
         {**naive, "pnl": static, "weight_scheme": "inverse_vol", "signal_type": "tsmom",
          "signal_on": "excess", "multi_lookbacks": (1, 3, 12)}),
        ("+ cluster risk budget", {"pnl": static, "universe_by_month": None, "carry_weight": 0.0,
                                   "target_vol": None, "vol_floor": None}),
        ("+ 15% ex-ante volatility target", {"pnl": static, "universe_by_month": None,
                                             "carry_weight": 0.0, "vol_floor": None}),
        ("+ 25% carry", {"pnl": static, "universe_by_month": None, "vol_floor": None}),
        ("+ yearly point-in-time universe", {"vol_floor": None}),
        ("+ volatility floor = the index", {}),
    ]
    alternatives = [
        ("sector budget instead of clusters", {"weight_scheme": "sector_inverse_vol"}),
        ("continuous trend strength instead of sign", {"signal_type": "strength"}),
        ("12-month momentum only", {"multi_lookbacks": None}),
        ("no carry", {"carry_weight": 0.0}),
        ("50% carry", {"carry_weight": 0.5}),
    ]

    def series(o):
        o = dict(o)
        pnl = o.pop("pnl", ctx["panel"])
        r = model.run_backtest(pnl, ctx["months"], ctx["sector_of"], ctx["rates"],
                               **{**kw, **o})
        return {m: x for m, x in zip(r.months, r.excess) if m >= start}

    runs = [(lab, series(o), "step") for lab, o in steps]
    base = runs[-1][1]
    runs += [(lab, series(o), "alternative") for lab, o in alternatives]
    common = sorted(set.intersection(*(set(x) for _, x, _ in runs)))
    pre = [m for m in common if m < C.DESIGN_SPLIT]
    post = [m for m in common if m >= C.DESIGN_SPLIT]
    rows, prev = [], None
    for lab, x, kind in runs:
        ref = prev if kind == "step" else base
        row = {"kind": kind, "step": lab,
               "design": metrics.sharpe([x[m] for m in pre]),
               "oos": metrics.sharpe([x[m] for m in post]),
               "full": metrics.sharpe([x[m] for m in common]),
               "maxdd": metrics.max_drawdown([x[m] for m in common])[0]}
        if ref is not None and ref is not x:
            for key, ms in (("d_design", pre), ("d_oos", post)):
                a, b = [x[m] for m in ms], [ref[m] for m in ms]
                row[key] = metrics.sharpe(a) - metrics.sharpe(b)
                row[key + "_ci"] = paired_sharpe_ci(a, b)
        rows.append(row)
        if kind == "step":
            prev = x
    aqr = {m: v for m, v in ctx["aqr"]["ex_equity"].items() if m in set(common)}
    am = sorted(aqr)
    rows.append({"kind": "reference", "step": "AQR TSMOM ex-equity (gross of costs)",
                 "design": metrics.sharpe([aqr[m] for m in am if m < C.DESIGN_SPLIT]),
                 "oos": metrics.sharpe([aqr[m] for m in am if m >= C.DESIGN_SPLIT]),
                 "full": metrics.sharpe([aqr[m] for m in am]),
                 "maxdd": metrics.max_drawdown([aqr[m] for m in am])[0]})
    return rows, common[0], common[-1]


# ---------------------------------------------------------------------------
# 3. Unit-risk trend streams (shared by persistence and breadth)
# ---------------------------------------------------------------------------
def unit_risk_streams(ctx, panel=None, with_parts=False):
    """{symbol: {month: return}}: each market traded alone with the index's
    TREND signal (no carry), scaled to unit ex-ante volatility, gross."""
    panel = panel or ctx["all_panel"]
    kw = P.ctx_kwargs(ctx)
    months = ctx["months"]
    rets = {s: model.monthly_returns(panel[s], months) for s in panel}
    for s in panel:
        for m in rets[s]:
            rets[s][m] += ctx["carry"].get(s, {}).get(m, 0.0)
    sigs, _ = model.build_signals(
        panel, months, rets, kw["lookback"], multi_lookbacks=kw.get("multi_lookbacks"),
        signal_type=kw["signal_type"], signal_on=kw["signal_on"],
        averaged_symbols=ctx["averaged"], vol_window=kw["vol_window"])
    out, pos, raw = {}, {}, {}
    for s in panel:
        st, ps, rw = {}, {}, {}
        for i in range(len(months) - 1):
            sm, rm = months[i], months[i + 1]
            if sm not in sigs[s] or rm not in rets[s]:
                continue
            v = model.trailing_vol(rets[s], months, i, kw["vol_window"], s in ctx["averaged"])
            if v:
                st[rm] = sigs[s][sm] * rets[s][rm] / v
                ps[rm], rw[rm] = sigs[s][sm], rets[s][rm] / v
        out[s], pos[s], raw[s] = st, ps, rw
    return (out, pos, raw) if with_parts else out


def _eq_risk_portfolio(streams, syms, months):
    port = []
    for m in months:
        xs = [streams[s][m] for s in syms if m in streams[s]]
        if xs:
            port.append(sum(xs) / len(xs))
    return port


# ---------------------------------------------------------------------------
# 4. Do bad markets stay bad?
# ---------------------------------------------------------------------------
def persistence(ctx, split="2006-01-01", drop_fraction=0.25, min_obs=60):
    """Rank each candidate market by its trend Sharpe before `split`; does the
    ranking predict the trend Sharpe after? And would dropping the worst
    quarter (judged on the first half only) have helped in the second half?"""
    streams = unit_risk_streams(ctx)
    months = ctx["months"]
    rows = []
    for s, st in streams.items():
        a = [st[m] for m in sorted(st) if m < split]
        b = [st[m] for m in sorted(st) if m >= split]
        if len(a) >= min_obs and len(b) >= min_obs:
            rows.append({"symbol": s, "name": C.CANDIDATES[s][0], "sector": C.CANDIDATES[s][1],
                         "first": metrics.sharpe(a), "second": metrics.sharpe(b)})

    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0] * len(v)
        for k, i in enumerate(order):
            r[i] = k
        return r
    base_rank = ranks([r["first"] for r in rows])
    sec_rank = ranks([r["second"] for r in rows])
    rho = metrics.correlation(base_rank, sec_rank)
    rng = random.Random(1)
    hits = 0
    for _ in range(5000):                    # one-sided permutation p-value
        perm = sec_rank[:]
        rng.shuffle(perm)
        if metrics.correlation(base_rank, perm) >= rho:
            hits += 1
    ordered = sorted(rows, key=lambda r: r["first"])
    k = max(1, int(round(drop_fraction * len(rows))))
    dropped = {r["symbol"] for r in ordered[:k]}
    second = [m for m in months if m >= split]
    keep_all = metrics.sharpe(_eq_risk_portfolio(streams, [r["symbol"] for r in rows], second))
    keep_some = metrics.sharpe(_eq_risk_portfolio(
        streams, [r["symbol"] for r in rows if r["symbol"] not in dropped], second))
    return {"rows": rows, "spearman": rho, "p_value": hits / 5000, "n": len(rows),
            "dropped": [C.CANDIDATES[s][0] for s in dropped],
            "second_all": keep_all, "second_without_worst": keep_some, "split": split}


# ---------------------------------------------------------------------------
# 5. Breadth: the diversification identity
# ---------------------------------------------------------------------------
# For N return streams scaled to the same volatility and held in equal risk,
# the portfolio Sharpe ratio is (up to the dispersion of realised vols)
#
#     SR_p = S_bar x DM,    DM = sqrt(N / (1 + (N - 1) * rho_bar))
#
# with S_bar the average single-market Sharpe ratio and rho_bar the average
# pairwise correlation of the streams. DM^2 is the effective number of
# independent bets. The identity splits any change in performance into "the
# trends got weaker" (S_bar) and "the trends got more alike" (rho_bar).

def _window_stats(streams, months_w, min_obs=36):
    mset = set(months_w)
    live = {s: {m: x for m, x in st.items() if m in mset} for s, st in streams.items()}
    live = {s: st for s, st in live.items() if len(st) >= min_obs}
    syms = sorted(live)
    n = len(syms)
    if n < 2:
        return None
    sh = [metrics.sharpe([live[s][m] for m in sorted(live[s])]) for s in syms]
    corrs = []
    for i in range(n):
        for j in range(i + 1, n):
            a, b, _ = metrics.align(live[syms[i]], live[syms[j]])
            if len(a) >= min_obs:
                corrs.append(metrics.correlation(a, b))
    rho = stats.fmean(corrs)
    dm = math.sqrt(n / (1 + (n - 1) * rho))
    return {"n": n, "s_bar": stats.fmean(sh), "rho_bar": rho, "dm": dm,
            "enb": dm * dm, "predicted": stats.fmean(sh) * dm,
            "realised": metrics.sharpe(_eq_risk_portfolio(live, syms, months_w)),
            "sharpes": dict(zip(syms, sh))}


def correlation_anatomy(pos, raw, months_w, min_obs=36):
    """Why do two markets' trend P&Ls co-move? Their co-movement is the average
    of s_i s_j r_i r_j (positions x unit-risk returns). Split it into
        (a) E[s_i s_j] x E[r_i r_j]   positions aligned x markets co-moving
        (b) the remainder              positions align precisely WHEN the markets
                                       move together (a shared macro trend)
    Averages over all pairs."""
    mset = set(months_w)
    syms = sorted(s for s in raw if sum(1 for m in raw[s] if m in mset) >= min_obs)
    tot, a_part, rho_a, conc = [], [], [], []
    for i in range(len(syms)):
        for j in range(i + 1, len(syms)):
            p, q = syms[i], syms[j]
            common = [m for m in months_w if m in raw[p] and m in raw[q]]
            if len(common) < min_obs:
                continue
            ss = [pos[p][m] * pos[q][m] for m in common]
            rr = [raw[p][m] * raw[q][m] for m in common]
            tot.append(stats.fmean(x * y for x, y in zip(ss, rr)))
            a_part.append(stats.fmean(ss) * stats.fmean(rr))
            rho_a.append(metrics.correlation([raw[p][m] for m in common],
                                             [raw[q][m] for m in common]))
            conc.append(stats.fmean(ss))
    t, a = stats.fmean(tot), stats.fmean(a_part)
    return {"rho_asset": stats.fmean(rho_a), "concordance": stats.fmean(conc),
            "comove_total": t, "comove_product": a, "comove_interaction": t - a}


def breadth(ctx, split=None, roll=60, step=12):
    split = split or C.SUBPERIOD_SPLIT
    streams, pos, raw = unit_risk_streams(ctx, with_parts=True)
    months = [m for m in ctx["months"] if any(m in st for st in streams.values())]
    pre_m = [m for m in months if m < split]
    post_m = [m for m in months if m >= split]
    pre, post, full = (_window_stats(streams, pre_m), _window_stats(streams, post_m),
                       _window_stats(streams, months))
    for w, ms in ((pre, pre_m), (post, post_m), (full, months)):
        w.update(correlation_anatomy(pos, raw, ms))
    d_s = 0.5 * (post["s_bar"] - pre["s_bar"]) * (pre["dm"] + post["dm"])
    d_dm = 0.5 * (post["dm"] - pre["dm"]) * (pre["s_bar"] + post["s_bar"])
    rolling = []
    for k in range(0, len(months) - roll + 1, step):
        ms = months[k:k + roll]
        w = _window_stats(streams, ms, min_obs=roll // 2)
        if w:
            rolling.append({"end": ms[-1], **{x: w[x] for x in
                            ("n", "s_bar", "rho_bar", "enb", "predicted", "realised")}})
    by_sector = {}
    for sec in C.SECTORS:
        def avg(w):
            v = [x for s, x in w["sharpes"].items() if ctx["sector_of"][s] == sec]
            return stats.fmean(v) if v else float("nan")
        by_sector[sec] = {"pre": avg(pre), "post": avg(post)}
    return {"pre": pre, "post": post, "full": full, "d_sbar": d_s, "d_dm": d_dm,
            "rolling": rolling, "by_sector": by_sector, "streams": streams}


def sharpe_vs_n(streams, months, sizes=(1, 2, 3, 5, 8, 12, 16, 20, 25, 30, 35, 40),
                draws=200, seed=7, min_obs=120):
    """Realised Sharpe of equal-risk portfolios of N randomly drawn markets,
    next to the identity's prediction from the whole set's S_bar and rho_bar."""
    rng = random.Random(seed)
    mset = set(months)
    live = sorted(s for s, st in streams.items() if sum(1 for m in st if m in mset) >= min_obs)
    w = _window_stats({s: streams[s] for s in live}, months)
    out = []
    for n in sizes:
        if n > len(live):
            continue
        srs = sorted(metrics.sharpe(_eq_risk_portfolio(streams, rng.sample(live, n), months))
                     for _ in range(draws if n < len(live) else 1))
        out.append({"n": n, "median": srs[len(srs) // 2], "p10": srs[int(0.1 * len(srs))],
                    "p90": srs[max(0, int(0.9 * len(srs)) - 1)],
                    "law": w["s_bar"] * math.sqrt(n / (1 + (n - 1) * w["rho_bar"]))})
    return out, w


# ---------------------------------------------------------------------------
# 6. Stacking: how much overlay?
# ---------------------------------------------------------------------------
def stacking(ctx):
    """S&P 500 total return (fully funded) + the index scaled to each overlay
    volatility in C.STACK_OVERLAY_VOLS. Returns rows plus crisis statistics."""
    base = ctx["base"]
    target = C.HEADLINE["target_vol"] or metrics.ann_vol(base.excess)
    prev = dict(zip(ctx["months"][1:], ctx["months"]))
    rates = ctx["rates"]
    spread = C.STACK_FINANCING_SPREAD_BPS / 10_000 / 12
    ov = dict(zip(base.months, base.excess))
    ms = [m for m in base.months if m in ctx["spx"] and prev.get(m) in rates]
    rf = {m: (1 + rates[prev[m]] / 100) ** (1 / 12) - 1 for m in ms}
    eq_x = [ctx["spx"][m] - rf[m] for m in ms]
    rows = []
    for v in C.STACK_OVERLAY_VOLS:
        k = v / target
        x = [e + k * (ov[m] - spread) if v else e for e, m in zip(eq_x, ms)]
        tot = [a + rf[m] for a, m in zip(x, ms)]
        rows.append({"overlay_vol": v, "sharpe": metrics.sharpe(x), "vol": metrics.ann_vol(x),
                     "maxdd": metrics.max_drawdown(tot)[0], "cagr": metrics.cagr(tot)})
    crash = [m for m, e in zip(ms, eq_x) if e < -0.05]
    by_year = {}
    for m in base.months:
        by_year[m[:4]] = by_year.get(m[:4], 1.0) * (1 + ov[m])
    return {"rows": rows, "start": ms[0], "end": ms[-1],
            "crash_n": len(crash),
            "crash_index": stats.fmean(ov[m] for m in crash),
            "crash_equity": stats.fmean(ctx["spx"][m] for m in crash),
            "years": {y: by_year[y] - 1 for y in ("2008", "2014", "2020", "2022") if y in by_year}}


# ---------------------------------------------------------------------------
# 7. Practitioner views
# ---------------------------------------------------------------------------
def apply_fees(months, total, rates, mgmt, perf):
    """Monthly fund NAV returns after a management fee (accrued monthly on NAV)
    and a performance fee on gains above the high-water mark, crystallised each
    December. Returns net total returns."""
    nav, hwm, year_start_nav, net = 1.0, 1.0, 1.0, []
    accrued = 0.0
    for m, r in zip(months, total):
        gross_nav = nav * (1 + r) * (1 - mgmt / 12)
        new_nav = gross_nav
        if m[5:7] == "12":
            gain = gross_nav - max(hwm, 0.0)
            fee = perf * gain if gain > 0 else 0.0
            new_nav = gross_nav - fee
            hwm = max(hwm, new_nav)
        net.append(new_nav / nav - 1)
        nav = new_nav
    return net


def practitioner(ctx):
    """Fees, margin, leverage, realised volatility, concentration and the
    effect of the approximated post-2024 commodity data."""
    base = ctx["base"]
    ms, total, excess = base.months, base.total, base.excess
    prev = dict(zip(ctx["months"][1:], ctx["months"]))
    rf = [(1 + ctx["rates"][prev[m]] / 100) ** (1 / 12) - 1 for m in ms]
    out = {"fees": []}
    for lab, mg, pf in (("No fees", 0.0, 0.0), ("1% management, 10% performance", 0.01, 0.10),
                        ("2% management, 20% performance", 0.02, 0.20)):
        net = apply_fees(ms, total, ctx["rates"], mg, pf)
        ex = [n - r for n, r in zip(net, rf)]
        out["fees"].append({"label": lab, "cagr": metrics.cagr(net),
                            "sharpe": metrics.sharpe(ex), "maxdd": metrics.max_drawdown(net)[0]})
    # leverage, margin, cap binding, realised volatility by decade
    gross, margin = {}, {}
    for m in ms:
        g = mg = 0.0
        for s, pos in base.positions.items():
            w = abs(pos.get(m, 0.0))
            g += w
            mg += w * C.MARGIN_RATE[ctx["sector_of"][s]]
        gross[m], margin[m] = g, mg
    cap = C.HEADLINE.get("max_leverage", 99)
    decades = {}
    for m, x in zip(ms, excess):
        d = m[:3] + "0s"
        decades.setdefault(d, {"x": [], "capped": 0, "gross": []})
        decades[d]["x"].append(x)
        decades[d]["gross"].append(gross[m])
        decades[d]["capped"] += gross[m] >= cap * 0.999
    out["decades"] = [{"decade": d, "vol": metrics.ann_vol(v["x"]), "sharpe": metrics.sharpe(v["x"]),
                       "capped": v["capped"] / len(v["x"]),
                       "gross": sorted(v["gross"])[len(v["gross"]) // 2]}
                      for d, v in sorted(decades.items())]
    mv = sorted(margin.values())
    out["margin"] = {"median": mv[len(mv) // 2], "p95": mv[int(0.95 * len(mv))], "max": mv[-1]}
    # profit attribution by market (gross, before costs)
    rets = {s: model.monthly_returns(ctx["panel"][s], ctx["months"]) for s in base.positions}
    pnl = {}
    for s, pos in base.positions.items():
        tot = 0.0
        for m, w in pos.items():
            r = rets[s].get(m)
            if r is not None:
                tot += w * (r + ctx["carry"].get(s, {}).get(m, 0.0))
        if tot:
            pnl[s] = tot
    total_pnl = sum(pnl.values())
    out["attribution"] = sorted(((C.CANDIDATES[s][0], v / total_pnl) for s, v in pnl.items()),
                                key=lambda t: -t[1])
    kw = P.ctx_kwargs(ctx)
    no_japan = {m: s - {"jgb_10y", "fx_jpy"} for m, s in ctx["info"]["schedule"].items()}
    r = model.run_backtest(ctx["panel"], ctx["months"], ctx["sector_of"], ctx["rates"],
                           **{**kw, "universe_by_month": no_japan})
    sp = P.subperiod_stats(r.months, r.excess)
    out["no_japan"] = {"full": metrics.sharpe(r.excess), "pre": sp["pre"]["sharpe"],
                       "post": sp["post"]["sharpe"]}
    japan = sum(v for s, v in pnl.items() if s in ("jgb_10y", "fx_jpy")) / total_pnl
    out["japan_share"] = japan
    # the approximated period
    upto = [x for m, x in zip(ms, excess) if m <= C.FUTURES_END]
    after = [x for m, x in zip(ms, excess) if m > C.FUTURES_END]
    out["splice_period"] = {"sharpe_to": metrics.sharpe(upto), "sharpe_after": metrics.sharpe(after),
                            "months_after": len(after), "return_after": metrics.cagr(after)}
    eq = metrics.equity_curve(total)
    peak = max(eq)
    out["current_drawdown"] = eq[-1] / peak - 1
    out["peak_month"] = ms[eq.index(peak)]
    return out


# ---------------------------------------------------------------------------
# 8. Data checks against futures
# ---------------------------------------------------------------------------
def data_checks(ctx):
    """How closely the yield-based bonds and the spot-plus-carry currencies
    track the corresponding front-month futures (Yahoo, month-end, 2001+)."""
    from mf import fetch
    pairs = {"ust_10y": "ZN=F", "ust_5y": "ZF=F", "ust_2y": "ZT=F", "ust_30y": "ZB=F",
             "fx_eur": "6E=F", "fx_jpy": "6J=F", "fx_gbp": "6B=F", "fx_aud": "6A=F",
             "fx_cad": "6C=F", "fx_chf": "6S=F"}
    rows = []
    for s, yf in pairs.items():
        daily, _ = fetch.fetch_yahoo_daily(yf, "2000-01-01", C.CACHE)
        mon = {m: c for m, c, _, _ in fetch.aggregate_monthly(daily, min_days=C.MIN_TRADING_DAYS)}
        fr = model.monthly_returns(mon, sorted(mon))
        ours = model.monthly_returns(ctx["panel"][s], ctx["months"])
        ours = {m: r + ctx["carry"].get(s, {}).get(m, 0.0) for m, r in ours.items()}
        a, b, sh = metrics.align(ours, fr)
        sh = [m for m in sh if m >= "2001-01-01"]
        a = [ours[m] for m in sh]
        b = [fr[m] for m in sh]
        rows.append({"market": C.CANDIDATES[s][0], "future": yf, "corr": metrics.correlation(a, b),
                     "n": len(sh)})
    return rows


# ---------------------------------------------------------------------------
# 9. Rebalancing frequency and execution lag (daily commodity futures)
# ---------------------------------------------------------------------------
def rebalancing(markets=None, start="1991-01-01"):
    """Weekly vs monthly rebalancing, and trading at the signal close vs one
    day later, on the daily commodity futures (the only sleeve with daily
    data). Same rule as the index's trend signal (signs of the 1/3/12-month
    returns, ~21/63/252 trading days), equal risk per market, costs per trade."""
    import datetime as dt
    markets = markets or [s for s in C.COMMODITIES if s not in ("cm_cattle", "cm_hogs")]
    lv = {}
    for s in markets:
        code = C.COMMODITIES[s][1]
        lv[s] = S.parse_pst_daily_index(
            S.fetch_pst("adjusted_prices_csv", code, C.PST_BASE, C.CACHE),
            S.fetch_pst("multiple_prices_csv", code, C.PST_BASE, C.CACHE))
    days = sorted(d for d in set().union(*[set(v) for v in lv.values()])
                  if "1989-01-01" <= d <= "2024-03-28")
    pos_of = {d: i for i, d in enumerate(days)}
    rets = {s: {} for s in markets}
    for s in markets:
        ds = [d for d in days if d in lv[s]]
        for a, b in zip(ds, ds[1:]):
            rets[s][b] = lv[s][b] / lv[s][a] - 1

    def signal(s, i):
        parts = []
        for L in (21, 63, 252):
            if i - L < 0 or days[i] not in lv[s] or days[i - L] not in lv[s]:
                return None
            parts.append(1 if lv[s][days[i]] > lv[s][days[i - L]] else -1)
        return sum(parts) / 3

    def vol(s, i):
        xs = [rets[s][x] for x in days[max(0, i - 252): i + 1] if x in rets[s]]
        return stats.stdev(xs) * math.sqrt(252) if len(xs) > 120 else None

    def is_rebal(freq, a, b):
        if freq == "monthly":
            return a[:7] != b[:7]
        return dt.date.fromisoformat(a).isocalendar()[1] != dt.date.fromisoformat(b).isocalendar()[1]

    def run(freq, lag):
        w, pending, port, turn, cost = {}, None, [], 0.0, 0.0

        def apply(new):
            nonlocal w, turn
            c = 0.0
            for s in set(new) | set(w):
                dw = abs(new.get(s, 0) - w.get(s, 0))
                c += dw * C.COST_BY_MARKET[s] / 1e4
                turn += dw
            w = new
            return c

        for k, d in enumerate(days[:-1]):
            r = sum(x * rets[s].get(d, 0.0) for s, x in w.items())
            c = 0.0
            if pending is not None and pending[0] == k:
                c += apply(pending[1])
                pending = None
            if is_rebal(freq, d, days[k + 1]) and d >= "1990-01-01":
                live = [s for s in markets if signal(s, k) is not None and vol(s, k)]
                tgt = {s: signal(s, k) * 0.15 / vol(s, k) / math.sqrt(len(live)) for s in live}
                if lag == 0:
                    c += apply(tgt)
                else:
                    pending = (k + lag, tgt)
            port.append((d, r - c))
            cost += c
        port = [(d, x) for d, x in port if d >= start]
        mon = {}
        for d, x in port:
            mon[d[:7]] = mon.get(d[:7], 1.0) * (1 + x)
        m = [v - 1 for _, v in sorted(mon.items())]
        yrs = len(port) / 252
        roll = sorted(stats.stdev(m[i - 12:i]) * math.sqrt(12) for i in range(12, len(m)))
        return {"sharpe": metrics.sharpe(m), "cagr": metrics.cagr(m), "vol": metrics.ann_vol(m),
                "maxdd": metrics.max_drawdown(m)[0], "turnover": turn / yrs, "cost": cost / yrs,
                "vol_p10": roll[len(roll) // 10], "vol_p90": roll[9 * len(roll) // 10]}

    rows = []
    for lab, f, lag in (("Monthly, at the signal close", "monthly", 0),
                        ("Monthly, one day later", "monthly", 1),
                        ("Weekly, at the signal close", "weekly", 0),
                        ("Weekly, one day later", "weekly", 1)):
        rows.append({"label": lab, **run(f, lag)})
    return rows, len(markets)
