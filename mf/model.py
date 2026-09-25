"""
The model: trend signal, position sizing, and the backtest loop.

THE TIMING CONVENTION -- the single most important thing in this file.

    signal for market i is computed from closes up to and including month t
    that signal determines the position held for the WHOLE of month t+1
    the return earned in month t+1 is credited to month t+1

So at no point does forming a position use information that did not exist yet.
This sounds obvious and is violated constantly, because it is so easy to line up
two arrays off by one and produce a spectacular equity curve that is pure
time travel. `tests/test_model.py` asserts it mechanically rather than trusting
that we got it right by reading the code.

A note on what the numbers here mean. Returns are computed on NOTIONAL: each
market is sized to a target fraction of portfolio notional and reset to that
target every month. We are not modelling margin, so there is no funding
or margin-call mechanic -- which is also why the natural output of this model is
an EXCESS return (position P&L only), with the collateral yield added separately
to get a total return.
"""

import math
import statistics as stats

# ---------------------------------------------------------------------------
# Bond return approximation from yield changes
# ---------------------------------------------------------------------------
def _par_modified_duration(coupon_rate: float, maturity_years: float) -> float:
    """Modified duration of a par bond (semi-annual coupons).

    For constant-maturity Treasury yields the bond is, by construction, priced at
    par on the observation date, so this is the right formula.

    coupon_rate: annual yield as a decimal (e.g. 0.04 for 4%)
    maturity_years: time to maturity (e.g. 10.0)

    Returns modified duration in years.  At very low yields the formula is still
    well-behaved (it approaches maturity in the limit).
    """
    if maturity_years <= 0:
        return 0.0
    if abs(coupon_rate) < 1e-9:
        return maturity_years      # limit as the yield goes to zero
    # The closed form below is valid for negative yields too (Bund 2016-2022).
    c2 = coupon_rate / 2.0         # semi-annual coupon rate
    n = 2.0 * maturity_years       # number of coupon periods
    denom = (1.0 + c2)
    pv_factor = 1.0 / (denom ** n)

    # Macaulay duration of a par bond (in semi-annual periods):
    #   D_sa = (1 + c2) / c2 × [1 - 1/(1+c2)^n]
    # This is the standard closed-form for a par bond where coupon = yield.
    mac_sa = ((1.0 + c2) / c2) * (1.0 - pv_factor)

    # Convert to years, then to modified duration:
    mac_yr = mac_sa / 2.0
    mod_dur = mac_yr / (1.0 + c2)
    return mod_dur


def bond_returns_from_yields(
    yield_series: dict[str, float],
    months: list[str],
    maturity_years: float,
    zero_coupon: bool = False,
) -> dict[str, float]:
    """Approximate bond total returns from monthly yield changes.

    Uses the standard first-order approximation:
        R_t ≈ y_{t-1}/12  -  D(y_{t-1}) × (y_t - y_{t-1})

    The first term is the carry (coupon income for one month), the second is the
    price change from the yield move.  This is exact for small yield changes and
    progressively approximate for large ones, but for monthly data on Treasuries
    the error is trivially small.

    When `zero_coupon` is True, uses modified_duration = maturity / (1 + y)
    instead of the par-bond formula.  Use this for zero-coupon yield series
    (e.g. BoE IUDMNZC), where the par-bond formula understates duration by ~15%.

    yield_series: {month: annualised yield in percent, e.g. 4.37}
    maturity_years: nominal maturity (2, 5, 10, or 30)

    Returns {month: total_return} for months where both current and prior yields
    exist.
    """
    out: dict[str, float] = {}
    for prev, cur in zip(months, months[1:]):
        y0 = yield_series.get(prev)
        y1 = yield_series.get(cur)
        if y0 is None or y1 is None:
            continue
        # Convert from percent to decimal for the computation.
        # Negative yields are real (Bund 2016-2022) and are kept: skipping them
        # would silently drop the market for six years.
        y0d = y0 / 100.0
        y1d = y1 / 100.0

        if zero_coupon:
            dur = maturity_years / (1.0 + y0d)
        else:
            dur = _par_modified_duration(y0d, maturity_years)
        carry = y0d / 12.0
        price_change = -dur * (y1d - y0d)
        out[cur] = carry + price_change
    return out


def bond_price_index(
    yield_series: dict[str, float],
    months: list[str],
    maturity_years: float,
    base: float = 100.0,
    zero_coupon: bool = False,
) -> dict[str, float]:
    """Cumulate bond returns into a synthetic price index.

    The model consumes a {month: close} panel. For bonds we have yields, not
    prices, so this function manufactures a price index that gives the same
    returns the duration model computes.  The absolute level is meaningless;
    what matters is that consecutive ratios equal 1 + R.
    """
    rets = bond_returns_from_yields(yield_series, months, maturity_years,
                                    zero_coupon=zero_coupon)
    idx: dict[str, float] = {}
    level = base
    for m in months:
        if m in rets:
            level *= (1.0 + rets[m])
            idx[m] = level
        elif not idx:
            idx[m] = level    # set the initial level before first return
    return idx


# ---------------------------------------------------------------------------
# Step 1: returns
# ---------------------------------------------------------------------------
def monthly_returns(series: dict[str, float], months: list[str]) -> dict[str, float]:
    """Simple month-over-month returns for one market, over an aligned month list.

    r_t = P_t / P_{t-1} - 1

    Prices here are spot prices, month-end values or synthetic bond indices; the
    carry that separates them from futures returns is added separately
    (`carry_adj` in run_backtest). A missing price yields no return for that
    month and the next: a gap is never bridged.
    """
    out = {}
    for prev, cur in zip(months, months[1:]):
        p0, p1 = series.get(prev), series.get(cur)
        if p0 in (None, 0) or p1 is None:
            continue
        out[cur] = p1 / p0 - 1.0
    return out


# ---------------------------------------------------------------------------
# Step 2: the trend signal
# ---------------------------------------------------------------------------
def trend_signals(
    series: dict[str, float],
    months: list[str],
    lookback: int,
    max_missing: int = 1,
) -> dict[str, int]:
    """+1 / -1 (/ 0) per month from price vs its own simple moving average.

    The rule, in full: at the close of month t, take the mean of the last
    `lookback` closes INCLUDING month t. If the close is above that mean, be
    long; below, be short.

    `max_missing` tolerates a few absent months inside the window. A mean over 11
    of 12 months is a perfectly good estimate, whereas refusing to trade would
    sideline a market for a whole year over one missing observation. The CURRENT
    month is always required, because it is the value being compared.

    Why a simple moving average and not something cleverer: it has exactly one
    parameter, it is the documented reference rule, and you can explain it in one
    sentence to someone who does not trust quant models. For a project whose
    point is understanding, that is worth more than a marginal Sharpe.
    """
    sig = {}
    for idx in range(lookback - 1, len(months)):
        window = [series.get(m) for m in months[idx - lookback + 1: idx + 1]]
        price = window[-1]
        if price is None:
            continue                     # no current price => nothing to compare
        present = [v for v in window if v is not None]
        if len(window) - len(present) > max_missing:
            continue                     # too sparse to call it a trend
        sma = sum(present) / len(present)
        sig[months[idx]] = 1 if price > sma else -1
    return sig


def tsmom_signals(series: dict[str, float], months: list[str],
                  lookback: int) -> dict[str, int]:
    """Time-series momentum (Moskowitz, Ooi & Pedersen 2012): +1 if the price
    rose over the last `lookback` months, -1 if it fell. Uses only the prices at
    t and t - lookback, both known at the end of month t."""
    sig = {}
    for i in range(lookback, len(months)):
        p1, p0 = series.get(months[i]), series.get(months[i - lookback])
        if p1 is not None and p0:
            sig[months[i]] = 1 if p1 > p0 else -1
    return sig


def strength_signals(series, rets_s, months, lookbacks, vol_window=36,
                     averaged=False, full_at=2.0):
    """Continuous trend strength in [-1, +1]: for each lookback L, the log
    change of the index over L months divided by its expected standard
    deviation (trailing monthly vol x sqrt(L)) -- a t-statistic of the trend --
    scaled so that |t| >= `full_at` is a full position; averaged over the
    lookbacks. A weak trend earns a small position instead of a full one,
    which is the whole difference from the sign rule."""
    out = {}
    for i in range(max(lookbacks), len(months)):
        m = months[i]
        vol = trailing_vol(rets_s, months, i, vol_window, averaged)
        if not vol or series.get(m) is None:
            continue
        parts = []
        for L in lookbacks:
            p0 = series.get(months[i - L])
            if not p0 or series[m] <= 0 or p0 <= 0:
                break
            t = math.log(series[m] / p0) / (vol * math.sqrt(L))
            parts.append(max(-1.0, min(1.0, t / full_at)))
        if len(parts) == len(lookbacks):
            out[m] = sum(parts) / len(parts)
    return out


def excess_index(rets: dict[str, float], months: list[str]) -> dict[str, float]:
    """Cumulate (carry-adjusted) monthly returns into an index level. Signals
    computed on it see what a futures position actually earned, including
    carry, rather than the raw spot price."""
    idx, level, started = {}, 1.0, False
    for m in months:
        if m in rets:
            level *= 1.0 + rets[m]
            started = True
        if started or m in rets:
            idx[m] = level
    return idx


def build_signals(panel, months, rets, lookback, multi_lookbacks=None,
                  signal_type="sma", signal_on="price",
                  averaged_symbols=None, signals=None, vol_window=36):
    """{symbol: {month: position in [-1, 1]}} known at the END of that month,
    plus the warm-up length. `rets` are the carry-adjusted monthly returns
    (used when signal_on == "excess"). Markets priced from monthly averages get
    their signal shifted one month later (see run_backtest)."""
    symbols = sorted(panel)
    if signals is not None:
        # Externally supplied positions in [-1, +1] per market-month (e.g. an
        # always-long benchmark). The same lag, sizing and costs apply.
        sigs = {s: dict(signals.get(s, {})) for s in symbols}
        warmup = lookback
    else:
        src = ({s: excess_index(rets[s], months) for s in symbols}
               if signal_on == "excess" else panel)
        if signal_type == "strength":
            lbs = multi_lookbacks or (lookback,)
            sigs = {s: strength_signals(src[s], rets[s], months, lbs, vol_window,
                                        s in set(averaged_symbols or ()))
                    for s in symbols}
            warmup = max(lbs) + 1
        elif signal_type == "tsmom":
            lbs = multi_lookbacks or (lookback,)
            per = {s: [tsmom_signals(src[s], months, lb) for lb in lbs] for s in symbols}
            sigs = {s: {m: sum(p[m] for p in per[s]) / len(lbs)
                        for m in set.intersection(*(set(p) for p in per[s]))}
                    for s in symbols}
            warmup = max(lbs) + 1
        else:
            sigs = {s: trend_signals(src[s], months, lookback) for s in symbols}
            warmup = lookback

    # Working (1960) correction: for markets whose prices are monthly averages,
    # shift the signal by one month so that position for month t+1 uses the
    # signal from month t−1. This removes the overlap between the signal
    # window and the return window that averaging creates.
    if averaged_symbols:
        for s in symbols:
            if s in averaged_symbols and s in sigs:
                old = sigs[s]
                sigs[s] = {months[j]: old[months[j - 1]]
                           for j in range(1, len(months)) if months[j - 1] in old}
        warmup += 1                         # one more month of history needed
    return sigs, warmup


def trailing_vol(rets_s: dict[str, float], months: list[str], i: int,
                 window: int, averaged: bool = False) -> float | None:
    """Stdev of one market's monthly returns over the `window` months ending at
    months[i] (inclusive), or None with too little history. Averaged-price
    returns are scaled by sqrt(3/2): averaging a random walk shrinks monthly
    variance to about two thirds."""
    hist = [rets_s[m] for m in months[max(0, i - window + 1): i + 1] if m in rets_s]
    if len(hist) < min(12, window):
        return None
    v = stats.stdev(hist) * (1.5 ** 0.5 if averaged else 1.0)
    return v if v > 0 else None


# ---------------------------------------------------------------------------
# Step 3: weights
# ---------------------------------------------------------------------------
def equal_weights(symbols: list[str]) -> dict[str, float]:
    """The baseline: every market gets the same notional.

    This is already a risk decision, not a neutral default: with equal NOTIONAL
    a market ten times as volatile as another carries ten times the risk. The
    risk-based schemes in run_backtest exist to fix that.
    """
    if not symbols:
        return {}
    w = 1.0 / len(symbols)
    return {s: w for s in symbols}


def _era_multiplier(eras: list[tuple[str, float]], month: str) -> float:
    mult = 1.0
    for start, m in eras:
        if month >= start:
            mult = m
    return mult


def cluster_risk_weights(active, inv, sector_of, cluster_of):
    """Three-level risk budget: equal per sector, equal per cluster inside the
    sector, equal per market inside the cluster (inverse volatility). Returned
    as notional weights normalised to 100% gross."""
    tree: dict[str, dict[str, list[str]]] = {}
    for s in active:
        tree.setdefault(sector_of[s], {}).setdefault(cluster_of.get(s, s), []).append(s)
    raw = {}
    for clusters in tree.values():
        for members in clusters.values():
            for s in members:
                raw[s] = inv[s] / (len(tree) * len(clusters) * len(members))
    tot = sum(raw.values())
    return {s: w / tot for s, w in raw.items()}


def expanding_vol(rets_s: dict[str, float], months: list[str], min_obs: int = 36):
    """Point-in-time long-run volatility: stdev of ALL of a market's monthly
    returns up to and including months[i], for every i (None before min_obs)."""
    out, n, s1, s2 = [], 0, 0.0, 0.0
    for m in months:
        r = rets_s.get(m)
        if r is not None:
            n, s1, s2 = n + 1, s1 + r, s2 + r * r
        out.append(math.sqrt(max(0.0, (s2 - s1 * s1 / n) / (n - 1)))
                   if n >= min_obs else None)
    return out


def scale_to_target_vol(pos, rets, months, i, window, target, max_leverage,
                        averaged=frozenset(), vol_override=None):
    """Scale positions so the portfolio's ex-ante annual volatility equals
    `target`; gross exposure capped at max_leverage.

    The covariance is estimated from the trailing `window` months (data up to
    months[i] only). With `vol_override` ({symbol: monthly vol}) each market's
    own volatility is replaced by the given one while the estimated
    correlations are kept -- how a volatility floor reaches the portfolio
    level, so a market whose volatility is artificially suppressed cannot
    drag the whole book into extra leverage."""
    hist = months[max(0, i - window + 1): i + 1]
    hist_set = set(hist)
    syms = [s for s in pos if pos[s]]
    cov: dict[tuple[str, str], float] = {}
    for x in syms:
        for y in syms:
            if (y, x) in cov:
                cov[(x, y)] = cov[(y, x)]
                continue
            common = [m for m in hist if m in rets[x] and m in rets[y]]
            if len(common) < 12:
                continue
            xa = [rets[x][m] for m in common]
            xb = [rets[y][m] for m in common]
            ma, mb = sum(xa) / len(xa), sum(xb) / len(xb)
            c = sum((u - ma) * (v - mb) for u, v in zip(xa, xb)) / (len(common) - 1)
            if x in averaged and y in averaged:
                c *= 1.5
            cov[(x, y)] = c
    var = 0.0
    for (x, y), c in cov.items():
        if vol_override and x in vol_override and y in vol_override:
            sx, sy = cov.get((x, x)), cov.get((y, y))
            if sx and sy and sx > 0 and sy > 0:
                c = c / math.sqrt(sx * sy) * vol_override[x] * vol_override[y]
        var += pos[x] * pos[y] * c
    if var <= 0:
        return pos
    vol = (var * 12) ** 0.5
    gross = sum(abs(w) for w in pos.values())
    k = min(target / vol, max_leverage / gross if gross else max_leverage)
    return {s: w * k for s, w in pos.items()}


class BacktestResult:
    """Plain container. Attributes, not a dict, so typos fail loudly."""

    def __init__(self):
        self.months: list[str] = []          # month the return was EARNED in
        self.excess: list[float] = []        # position P&L only
        self.total: list[float] = []         # excess + collateral yield
        self.turnover: list[float] = []      # notional traded at the rebalance
        self.cost: list[float] = []          # cost charged, in return units
        self.gross: list[float] = []         # excess before costs
        self.n_markets: list[int] = []
        self.positions: dict[str, dict[str, float]] = {}   # sym -> month -> signed weight
        self.label: str = ""

    def __len__(self):
        return len(self.months)


def run_backtest(
    panel: dict[str, dict[str, float]],
    months: list[str],
    sector_of: dict[str, str],
    rates: dict[str, float],
    lookback: int,
    cost_bps: float,
    weight_scheme: str = "equal",
    vol_window: int = 36,
    multi_lookbacks=None,
    label: str = "",
    carry_adj: dict[str, dict[str, float]] | None = None,
    cost_by_market: dict[str, float] | None = None,
    overhead_bps_annual: float = 0.0,
    averaged_symbols: set[str] | None = None,
    signals: dict[str, dict[str, float]] | None = None,
    signal_type: str = "sma",
    signal_on: str = "price",
    roll_cost_by_market: dict[str, float] | None = None,
    cost_eras: list[tuple[str, float]] | None = None,
    cluster_of: dict[str, str] | None = None,
    carry_signal: dict[str, dict[str, float]] | None = None,
    carry_weight: float = 0.0,
    target_vol: float | None = None,
    max_leverage: float = 5.0,
    vol_floor: float | None = None,
    vol_floor_ref: str = "sector",
    max_position: float | None = None,
    universe_by_month: dict[str, set[str]] | None = None,
) -> BacktestResult:
    """Run the index over an aligned month list.

    `panel`  {symbol: {month: close}}   -- must already be aligned/filtered
    `months` the aligned, contiguous month list, oldest first
    `rates`  {month: annualised T-bill percent} for the collateral leg
    `carry_adj`  {symbol: {month: monthly_carry}} -- added to the price
        returns (FX interest-rate differential, bond funding). Signals see it
        only when `signal_on` == "excess".
    `cost_by_market`  {symbol: bps} -- per-market execution cost.
        When given, each market's turnover is costed at its own rate rather than
        the flat `cost_bps`.
    `roll_cost_by_market`  {symbol: bps per month} charged on the ABSOLUTE
        position held, for rolling futures contracts.
    `cost_eras`  [(first month, multiplier), ...] scaling trading and roll
        costs by period (trading was dearer before electronic markets).
    `overhead_bps_annual`  annual overhead (data, platform, exchange fees) in bps,
        deducted monthly as overhead_bps_annual / 10000 / 12.
    `averaged_symbols`  set of symbols whose prices are monthly averages (e.g.
        Pink Sheet). For these, the signal is lagged by one extra month to
        remove the Working (1960) averaging bias: position for month t+1 uses
        the signal from month t−1 instead of t.
    `signals`  optional {symbol: {month: position in [-1, 1]}} replacing the
        built-in rule.
    `signal_type`  "sma" (price vs its moving average), "tsmom" (sign of the
        trailing return) or "strength" (the trailing return in units of its own
        volatility, full position at two standard deviations; see
        build_signals). Several lookbacks are averaged if `multi_lookbacks`.
    `signal_on`  "price" (index level as stored) or "excess" (the
        carry-adjusted excess-return index).
    `cluster_of`  {symbol: cluster} for weight_scheme "cluster_inverse_vol":
        equal risk per sector, equal risk per cluster inside a sector, equal
        risk per market inside a cluster.
    `carry_signal`, `carry_weight`  optional {symbol: {month: carry in [-1, 1]}}
        blended in: position = (1 - w) x trend + w x carry.
    `vol_floor`  if set, a market's volatility estimate is never below this
        fraction of a reference volatility -- in sizing and in the portfolio
        volatility target. `vol_floor_ref` "sector": the median current
        volatility of the markets of the same sector (a 2-year bond cannot be
        sized as if it were a tenth as risky as a 10-year bond); "own": the
        market's own long-run (expanding-window) volatility.
    `max_position`  if set, cap on each market's notional as a fraction of
        capital, applied after volatility targeting.
    `universe_by_month`  optional {month: set of symbols allowed to trade},
        e.g. a universe re-selected every year from point-in-time data.
    `target_vol`  if set, positions are scaled each month so the portfolio's
        EX-ANTE volatility (trailing covariance of the markets held, current
        weights) equals this annual level, with gross exposure capped at
        `max_leverage`. Costs are charged on the scaled positions.
    `weight_scheme`  "equal" notional; "inverse_vol" (every market sized to equal risk);
        "sector_inverse_vol" (equal risk per market inside a sector, equal risk
        budget per sector). Vol = trailing `vol_window`-month stdev of the
        market's own returns up to the signal month; averaged-price markets
        have it scaled up by sqrt(3/2), because averaging a random walk shrinks
        monthly variance to about two thirds.

    Returns a BacktestResult whose month i return was EARNED in months[i], using
    a signal formed at the previous month. Nothing here reads forward.
    """
    symbols = sorted(panel)

    # Precompute per-market returns and signals once.
    rets = {s: monthly_returns(panel[s], months) for s in symbols}

    # Carry adjustments (FX rate differential, bond funding) turn price returns
    # into futures-equivalent excess returns.
    if carry_adj:
        for s in symbols:
            adj_s = carry_adj.get(s)
            if not adj_s:
                continue
            for m in rets[s]:
                a = adj_s.get(m, 0.0)
                if a:
                    rets[s][m] += a

    sigs, warmup = build_signals(
        panel, months, rets, lookback, multi_lookbacks=multi_lookbacks,
        signal_type=signal_type, signal_on=signal_on,
        averaged_symbols=averaged_symbols, signals=signals, vol_window=vol_window)
    if carry_signal and carry_weight:
        for s in symbols:
            cs = carry_signal.get(s, {})
            sigs[s] = {m: (1 - carry_weight) * v + carry_weight * cs.get(m, 0.0)
                       for m, v in sigs[s].items()}

    avg_set = set(averaged_symbols or ())
    longrun = ({s: expanding_vol(rets[s], months) for s in symbols}
               if vol_floor and vol_floor_ref == "own" else {})
    raw_vol_cache: dict[int, dict[str, float]] = {}

    def raw_vols(i):
        if i not in raw_vol_cache:
            raw_vol_cache[i] = {s: v for s in symbols
                                if (v := trailing_vol(rets[s], months, i, vol_window, s in avg_set))}
        return raw_vol_cache[i]

    def market_vol(s, i):
        v = raw_vols(i).get(s)
        if not v or not vol_floor:
            return v
        if vol_floor_ref == "own":
            ref = longrun[s][i]
        else:
            peers = sorted(x for k, x in raw_vols(i).items()
                           if sector_of.get(k) == sector_of.get(s))
            ref = peers[len(peers) // 2] if peers else None
        return max(v, vol_floor * ref) if ref else v
    res = BacktestResult()
    res.label = label or f"{weight_scheme} / {lookback}m"
    res.positions = {s: {} for s in symbols}

    prev_pos: dict[str, float] = {}      # signed weight held during the prior month
    cost_rate = cost_bps / 10_000.0
    overhead_monthly = overhead_bps_annual / 10_000.0 / 12.0

    # i indexes the SIGNAL month; the return is earned in months[i + 1].
    # Starting at warmup - 1 is the first month a full-window signal exists.
    for i in range(warmup - 1, len(months) - 1):
        sig_month = months[i]
        ret_month = months[i + 1]

        # --- markets with a live signal at the signal month
        active = [s for s in symbols if sig_month in sigs[s]]
        if universe_by_month is not None:
            allowed = universe_by_month.get(sig_month, set())
            active = [s for s in active if s in allowed]
        if not active:
            continue

        # --- weights (known at the signal month)
        if weight_scheme in ("inverse_vol", "sector_inverse_vol", "cluster_inverse_vol"):
            inv = {}
            for s in active:
                v = market_vol(s, i)
                if v:
                    inv[s] = 1.0 / v
            active = [s for s in active if s in inv]
            if not active:
                continue
            if weight_scheme == "inverse_vol":
                tot = sum(inv.values())
                weights = {s: inv[s] / tot for s in active}
            elif weight_scheme == "cluster_inverse_vol":
                weights = cluster_risk_weights(active, inv, sector_of, cluster_of or {})
            else:
                by_sec: dict[str, list[str]] = {}
                for s in active:
                    by_sec.setdefault(sector_of[s], []).append(s)
                raw = {s: inv[s] / (len(by_sec[sector_of[s]]) * len(by_sec))
                       for s in active}
                # express each sector's risk budget in notional, then renormalise
                # to 100% gross so the scheme stays unlevered like the others
                tot = sum(raw.values())
                weights = {s: w / tot for s, w in raw.items()}
        else:
            weights = equal_weights(active)

        # --- signed target positions for the coming month
        pos = {s: weights[s] * sigs[s][sig_month] for s in active}
        if target_vol:
            vo = ({s: market_vol(s, i) for s in pos if market_vol(s, i)}
                  if vol_floor else None)
            pos = scale_to_target_vol(pos, rets, months, i, vol_window,
                                      target_vol, max_leverage, avg_set, vol_override=vo)
        if max_position:
            pos = {k: max(-max_position, min(max_position, w)) for k, w in pos.items()}

        # --- turnover: notional we must trade to move from prev_pos to pos.
        # At inception prev_pos is empty, so turnover is ~1.0: you genuinely do
        # pay to put the book on, and pretending otherwise is free money.
        keys = set(pos) | set(prev_pos)
        turn = sum(abs(pos.get(k, 0.0) - prev_pos.get(k, 0.0)) for k in keys)
        if cost_by_market:
            # Per-market cost: each market's turnover x its own cost rate.
            cost = sum(
                abs(pos.get(k, 0.0) - prev_pos.get(k, 0.0))
                * cost_by_market.get(k, cost_bps) / 10_000.0
                for k in keys
            )
        else:
            cost = turn * cost_rate
        if roll_cost_by_market:
            cost += sum(abs(w) * roll_cost_by_market.get(k, 0.0) / 10_000.0
                        for k, w in pos.items())
        if cost_eras:
            cost *= _era_multiplier(cost_eras, ret_month)
        # Add monthly overhead (data, platform, exchange/clearing fees).
        cost += overhead_monthly

        # --- realised return in the FOLLOWING month
        gross = 0.0
        counted = 0
        for s, w in pos.items():
            r = rets[s].get(ret_month)
            if r is None:
                continue
            gross += w * r
            counted += 1
        if counted == 0:
            continue

        excess = gross - cost

        # --- collateral leg. The yield is the one observable at the
        # SIGNAL month: that is the rate you could actually lock in before the
        # month began. Using the ret_month rate would be a small look-ahead.
        ann = rates.get(sig_month)
        carry = (1.0 + ann / 100.0) ** (1.0 / 12.0) - 1.0 if ann is not None else 0.0

        res.months.append(ret_month)
        res.gross.append(gross)
        res.cost.append(cost)
        res.excess.append(excess)
        res.total.append(excess + carry)
        res.turnover.append(turn)
        res.n_markets.append(counted)
        for s, w in pos.items():
            res.positions[s][ret_month] = w

        prev_pos = pos

    return res


def run_long_only(panel, months, sector_of, rates, label="Always long", **kw):
    """Benchmark: same markets, same weights and costs, permanently long.

    If the trend index merely tracked this, all we would have learned is that
    these markets went up. The gap between the two is the value of the trend
    rule itself, separated from the value of simply being in these markets.
    """
    const = {s: {m: 1 for m in months if m in panel[s]} for s in panel}
    kw = {k: v for k, v in kw.items()
          if k not in ("signal_type", "signal_on", "multi_lookbacks")}
    kw.setdefault("lookback", 12)
    kw.setdefault("cost_bps", 10.0)
    return run_backtest(panel, months, sector_of, rates, signals=const,
                        label=label, **kw)


# ---------------------------------------------------------------------------
# Return stacking: 100% equities + 100% MF overlay
# ---------------------------------------------------------------------------
def return_stacked(
    mf_result: BacktestResult,
    spx_returns: dict[str, float],
    rates: dict[str, float],
    financing_spread_bps: float = 20.0,
    label: str = "Return stacked (100% SPX + 100% MF)",
) -> BacktestResult:
    """Combine equity buy-and-hold with the MF trend overlay.

    The idea: hold $100 of S&P 500 with actual capital, then use the equity
    as collateral to enter $100 notional of managed futures via futures/swaps.
    Financing cost for the overlay ≈ risk-free rate + a small spread (box
    spreads or similar). Since the MF excess return is already above the
    risk-free rate by construction, the combined return is:

        R_stacked = R_spx + R_mf_excess - financing_spread

    When the two are uncorrelated this adds the overlay's excess return at
    little extra risk; it also adds the overlay's own drawdowns on top of the
    equity drawdowns, which the report shows.
    """
    out = BacktestResult()
    out.label = label
    spread_monthly = (1 + financing_spread_bps / 10_000) ** (1/12) - 1

    for i, m in enumerate(mf_result.months):
        r_spx = spx_returns.get(m)
        if r_spx is None:
            continue
        r_mf = mf_result.excess[i]

        # Stacked return: equity + MF overlay - financing spread on the overlay
        stacked = r_spx + r_mf - spread_monthly

        out.months.append(m)
        out.excess.append(stacked)      # "excess" here = the combined return
        out.total.append(stacked)       # same (equity is already total)
        out.gross.append(stacked)
        out.cost.append(mf_result.cost[i])
        out.turnover.append(mf_result.turnover[i])
        out.n_markets.append(mf_result.n_markets[i])
    return out
