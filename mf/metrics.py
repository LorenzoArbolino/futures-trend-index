"""
Performance statistics .

Two things in here are easy to get subtly wrong, so they are spelled out:

1. WHICH SHARPE.
   A Sharpe ratio is (return above cash) / volatility. Our EXCESS series is
   already return above cash by construction -- the futures overlay is unfunded --
   so its Sharpe is simply mean/stdev, with no rate subtracted. The TOTAL series
   has the T-bill yield added back in, so computing mean/stdev on it would
   double-count the cash return and inflate the Sharpe, sometimes dramatically in
   a high-rate decade. Both series therefore share ONE Sharpe, computed from the
   excess series, and every table says so.

2. GEOMETRIC VS ARITHMETIC.
   CAGR compounds (what you actually ended up with). The mean monthly return
   annualised does not (it ignores that a -50% needs a +100% to recover). We
   report CAGR as the headline and keep arithmetic figures only where
   the statistics require them, such as inside the Sharpe.
"""

import math
import random
import statistics as stats

MONTHS = 12


def equity_curve(returns: list[float], start: float = 1.0) -> list[float]:
    """Compounded value of 1 unit invested, one point per month."""
    eq, v = [], start
    for r in returns:
        v *= (1.0 + r)
        eq.append(v)
    return eq


def cagr(returns: list[float]) -> float:
    if not returns:
        return float("nan")
    total = 1.0
    for r in returns:
        total *= (1.0 + r)
    years = len(returns) / MONTHS
    if years <= 0 or total <= 0:
        return float("nan")
    return total ** (1.0 / years) - 1.0


def ann_vol(returns: list[float]) -> float:
    if len(returns) < 2:
        return float("nan")
    return stats.stdev(returns) * math.sqrt(MONTHS)


def sharpe(excess_returns: list[float]) -> float:
    """Annualised Sharpe of an ALREADY-EXCESS return series."""
    if len(excess_returns) < 2:
        return float("nan")
    sd = stats.stdev(excess_returns)
    if sd == 0:
        return float("nan")
    return (stats.fmean(excess_returns) / sd) * math.sqrt(MONTHS)


def max_drawdown(returns: list[float]) -> tuple[float, int, int]:
    """Worst peak-to-trough decline, plus its start and end indices."""
    eq = equity_curve(returns)
    peak, peak_i = -float("inf"), 0
    worst, w_start, w_end = 0.0, 0, 0
    for i, v in enumerate(eq):
        if v > peak:
            peak, peak_i = v, i
        dd = v / peak - 1.0
        if dd < worst:
            worst, w_start, w_end = dd, peak_i, i
    return worst, w_start, w_end


def drawdown_series(returns: list[float]) -> list[float]:
    eq = equity_curve(returns)
    out, peak = [], -float("inf")
    for v in eq:
        peak = max(peak, v)
        out.append(v / peak - 1.0)
    return out


def calmar(returns: list[float]) -> float:
    mdd, _, _ = max_drawdown(returns)
    if mdd == 0:
        return float("nan")
    return cagr(returns) / abs(mdd)


def hit_rate(returns: list[float]) -> float:
    if not returns:
        return float("nan")
    return sum(1 for r in returns if r > 0) / len(returns)


def correlation(a: list[float], b: list[float]) -> float:
    """Pearson correlation of two equal-length, already-aligned series."""
    n = min(len(a), len(b))
    if n < 3:
        return float("nan")
    a, b = a[:n], b[:n]
    ma, mb = stats.fmean(a), stats.fmean(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    da = math.sqrt(sum((x - ma) ** 2 for x in a))
    db = math.sqrt(sum((y - mb) ** 2 for y in b))
    if da == 0 or db == 0:
        return float("nan")
    return num / (da * db)


def align(series_a: dict[str, float], series_b: dict[str, float]):
    """Restrict two month-keyed series to their shared months, in order.

    Correlations computed on unaligned series are a silent, common bug -- you get
    a number, it just is not the number you think it is.
    """
    shared = sorted(set(series_a) & set(series_b))
    return [series_a[m] for m in shared], [series_b[m] for m in shared], shared


def bootstrap_sharpe_ci(
    excess_returns: list[float], samples: int, seed: int,
    level: float = 0.95, block: int = 12,
):
    """Percentile confidence interval for the Sharpe ratio, circular block
    bootstrap.

    A backtest is ONE draw from the space of possible histories, so the Sharpe
    is quoted with an interval. Resampling contiguous 12-month blocks rather than
    single months preserves autocorrelation and volatility clustering, which an
    i.i.d. bootstrap would destroy (making the interval too narrow).
    """
    n = len(excess_returns)
    if n < 24:
        return (float("nan"), float("nan"))
    block = max(1, min(block, n))
    rng = random.Random(seed)
    out = []
    for _ in range(samples):
        draw: list[float] = []
        while len(draw) < n:
            start = rng.randrange(n)
            draw.extend(excess_returns[(start + k) % n] for k in range(block))
        s = sharpe(draw[:n])
        if not math.isnan(s):
            out.append(s)
    if not out:
        return (float("nan"), float("nan"))
    out.sort()
    lo_i = int((1 - level) / 2 * len(out))
    hi_i = int((1 + level) / 2 * len(out)) - 1
    return out[lo_i], out[max(lo_i, hi_i)]


def summarise(
    months: list[str],
    excess: list[float],
    total: list[float],
    turnover: list[float] | None = None,
    benchmark: dict[str, float] | None = None,
    bootstrap: tuple[int, int] | None = None,
) -> dict:
    """The full metric set for one return stream."""
    mdd_x, s_i, e_i = max_drawdown(excess)
    mdd_t, _, _ = max_drawdown(total)

    row = {
        "n_months": len(excess),
        "start": months[0] if months else None,
        "end": months[-1] if months else None,
        "cagr_excess": cagr(excess),
        "cagr_total": cagr(total),
        "vol_excess": ann_vol(excess),
        "vol_total": ann_vol(total),
        # One Sharpe, from the excess series -- see the module docstring.
        "sharpe": sharpe(excess),
        "max_dd_excess": mdd_x,
        "max_dd_total": mdd_t,
        "dd_start": months[s_i] if months and s_i < len(months) else None,
        "dd_end": months[e_i] if months and e_i < len(months) else None,
        "calmar_excess": calmar(excess),
        "hit_rate": hit_rate(excess),
        "best_month": max(excess) if excess else float("nan"),
        "worst_month": min(excess) if excess else float("nan"),
    }

    if turnover:
        # Annualised two-way notional traded. The honest companion to any cost
        # assumption: a strategy claiming low costs on 400% turnover is not
        # claiming much.
        row["turnover_ann"] = stats.fmean(turnover) * MONTHS

    if benchmark:
        strat = {m: r for m, r in zip(months, excess)}
        a, b, shared = align(strat, benchmark)
        row["corr_benchmark"] = correlation(a, b)
        row["corr_n_months"] = len(shared)
        # Downside correlation: how it behaves specifically when equities fall,
        # which for a diversifier is the question that actually matters.
        down = [(x, y) for x, y in zip(a, b) if y < 0]
        if len(down) >= 6:
            row["corr_benchmark_down"] = correlation(
                [x for x, _ in down], [y for _, y in down]
            )
            row["mean_when_bench_down"] = stats.fmean([x for x, _ in down])
            row["bench_mean_when_down"] = stats.fmean([y for _, y in down])

    if bootstrap:
        samples, seed, *rest = bootstrap
        lo, hi = bootstrap_sharpe_ci(excess, samples, seed,
                                     block=rest[0] if rest else 12)
        row["sharpe_ci_lo"], row["sharpe_ci_hi"] = lo, hi

    return row


def fmt_pct(x, dp=2):
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x*100:.{dp}f}%"


def fmt_num(x, dp=2):
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.{dp}f}"
