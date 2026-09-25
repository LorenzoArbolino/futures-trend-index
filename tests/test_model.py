"""
Tests for the model. Run with:

    python -m unittest discover -s tests -v

Why a backtest has tests at all -- this is unusual and deliberate. A backtest
cannot be checked by looking at its output, because a WRONG backtest produces a
better-looking equity curve than a right one. There is no error message when you
accidentally let the model see tomorrow's price; there is just a Sharpe ratio of
2.5 and a nice story. So the properties that must hold are asserted mechanically.

The tests below are ordered by how much damage the bug they catch would do:

  1. look-ahead     the model must not react to data that did not exist yet
  2. timing         the signal from month t must drive month t+1, not month t
  3. reconciliation the reported portfolio return must equal its own components
  4. return maths   compounded returns must match the underlying price change
  5. plumbing       the current incomplete month must never enter the data
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import datetime as dt

from mf import fetch, metrics, model


def months_from(start_year: int, n: int) -> list[str]:
    out, y, m = [], start_year, 1
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}-01")
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return out


class TestNoLookAhead(unittest.TestCase):
    """The signal at month t must depend ONLY on months <= t."""

    def test_future_prices_cannot_change_past_signals(self):
        months = months_from(2001, 36)
        base = {m: 100.0 + i for i, m in enumerate(months)}       # steady uptrend

        # Same history, but the final 6 months are violently different.
        tampered = dict(base)
        for m in months[-6:]:
            tampered[m] = 1.0                                     # crash

        sig_base = model.trend_signals(base, months, lookback=12)
        sig_tamp = model.trend_signals(tampered, months, lookback=12)

        # Every signal dated at or before the first tampered month must be
        # untouched. If altering the future changes them, the model is peeking.
        cutoff = months[-7]
        for m in months:
            if m <= cutoff and m in sig_base:
                self.assertEqual(
                    sig_base[m], sig_tamp[m],
                    f"signal for {m} changed when only FUTURE prices were "
                    f"altered -- look-ahead bias",
                )

    def test_signal_uses_current_month_inclusive(self):
        """The window must END at month t, and include it."""
        months = months_from(2001, 13)
        series = {m: 100.0 for m in months}
        series[months[12]] = 500.0          # spike in the final month only
        sig = model.trend_signals(series, months, lookback=12)
        # Month 12 (index 12) sees the spike and must be long.
        self.assertEqual(sig[months[12]], 1)
        # Month 11 (index 11) must NOT see it.
        self.assertEqual(sig[months[11]], -1)


class TestTiming(unittest.TestCase):
    """A signal formed at month t must be paid the return of month t+1."""

    def test_backtest_pays_next_month_return(self):
        # Flat at 100 for 12 months, then doubles, then halves back.
        #   At month index 11: window is all 100 -> SMA 100, price 100.
        #   100 > 100 is False -> signal is SHORT (-1).
        #   The return earned in month 12 is +100%.
        #   Correct behaviour: the SHORT loses ~100%.
        #   Buggy behaviour (using month 12's own signal, which would be LONG):
        #   a +100% gain. The sign alone distinguishes them.
        months = months_from(2001, 14)
        prices = {m: 100.0 for m in months}
        prices[months[12]] = 200.0
        prices[months[13]] = 100.0

        panel = {"X=F": prices}
        res = model.run_backtest(
            panel, months, {"X=F": "commodity"}, rates={},
            lookback=12, cost_bps=0.0, weight_scheme="equal",
        )
        self.assertGreater(len(res), 0, "backtest produced no months")
        first_month = res.months[0]
        self.assertEqual(first_month, months[12])
        self.assertAlmostEqual(
            res.excess[0], -1.0, places=6,
            msg="the month-11 short should lose 100% on the month-12 doubling; "
                "a positive value here means the signal was taken from the same "
                "month as the return",
        )

    def test_no_signal_before_full_window(self):
        months = months_from(2001, 20)
        series = {m: 100.0 + i for i, m in enumerate(months)}
        sig = model.trend_signals(series, months, lookback=12)
        for m in months[:11]:
            self.assertNotIn(m, sig, f"{m} has a signal on an incomplete window")
        self.assertIn(months[11], sig)


class TestReconciliation(unittest.TestCase):
    """The reported portfolio return must equal the sum of its parts."""

    def test_excess_equals_gross_minus_cost(self):
        months = months_from(2001, 60)
        # Two markets with different, deterministic zig-zags.
        a = {m: 100.0 * (1.03 ** i) * (1.1 if i % 5 == 0 else 1.0)
             for i, m in enumerate(months)}
        b = {m: 50.0 * (1.01 ** i) * (0.9 if i % 7 == 0 else 1.0)
             for i, m in enumerate(months)}
        panel = {"A=F": a, "B=F": b}
        res = model.run_backtest(
            panel, months, {"A=F": "commodity", "B=F": "bond"}, rates={},
            lookback=12, cost_bps=10.0,
        )
        self.assertGreater(len(res), 0)
        for i in range(len(res)):
            self.assertAlmostEqual(
                res.excess[i], res.gross[i] - res.cost[i], places=12,
                msg=f"excess != gross - cost at {res.months[i]}",
            )

    def test_gross_equals_weighted_market_returns(self):
        months = months_from(2001, 40)
        a = {m: 100.0 + 2 * i for i, m in enumerate(months)}
        b = {m: 200.0 - 1.5 * i for i, m in enumerate(months)}
        panel = {"A=F": a, "B=F": b}
        sector = {"A=F": "commodity", "B=F": "bond"}
        res = model.run_backtest(
            panel, months, sector, rates={}, lookback=12, cost_bps=0.0
        )
        rets = {s: model.monthly_returns(panel[s], months) for s in panel}
        for i, rm in enumerate(res.months):
            expected = sum(
                w * rets[s][rm] for s, w in
                ((s, res.positions[s][rm]) for s in panel if rm in res.positions[s])
            )
            self.assertAlmostEqual(
                res.gross[i], expected, places=12,
                msg=f"gross return at {rm} does not match its own positions",
            )

    def test_higher_cost_never_helps(self):
        months = months_from(2001, 80)
        series = {m: 100.0 * (1.02 ** i) * (0.95 if i % 4 == 0 else 1.0)
                  for i, m in enumerate(months)}
        panel = {"A=F": series}
        prev = None
        for bps in (0.0, 10.0, 50.0, 200.0):
            res = model.run_backtest(
                panel, months, {"A=F": "commodity"}, rates={},
                lookback=12, cost_bps=bps,
            )
            total = sum(res.excess)
            if prev is not None:
                self.assertLessEqual(
                    total, prev + 1e-12,
                    "increasing transaction costs improved returns",
                )
            prev = total

    def test_collateral_adds_exactly_the_tbill_accrual(self):
        months = months_from(2001, 30)
        series = {m: 100.0 + i for i, m in enumerate(months)}
        rates = {m: 6.0 for m in months}          # flat 6% annual
        res = model.run_backtest(
            {"A=F": series}, months, {"A=F": "commodity"}, rates=rates,
            lookback=12, cost_bps=0.0,
        )
        expected = (1.06 ** (1 / 12)) - 1
        for i in range(len(res)):
            self.assertAlmostEqual(
                res.total[i] - res.excess[i], expected, places=12,
                msg="total return is not excess + one month of T-bill accrual",
            )


class TestReturnMaths(unittest.TestCase):
    def test_returns_telescope_to_price_change(self):
        """With no gaps, compounding the returns must reproduce P_last/P_first.

        This is the invariant that caught our Live Cattle data hole: when it
        fails, a month is being skipped somewhere.
        """
        months = months_from(2001, 50)
        series = {m: 100.0 * (1.01 ** i) * (1.2 if i % 6 == 0 else 1.0)
                  for i, m in enumerate(months)}
        rets = model.monthly_returns(series, months)
        prod = 1.0
        for m in months:
            if m in rets:
                prod *= (1 + rets[m])
        self.assertAlmostEqual(
            prod, series[months[-1]] / series[months[0]], places=10
        )

    def test_gap_breaks_telescoping_and_is_not_bridged(self):
        """A missing month must NOT produce a two-month return labelled one month."""
        months = months_from(2001, 24)
        series = {m: 100.0 + i for i, m in enumerate(months)}
        missing = months[10]
        del series[missing]
        rets = model.monthly_returns(series, months)
        self.assertNotIn(missing, rets, "return computed for a month with no price")
        self.assertNotIn(
            months[11], rets,
            "a return was computed across the gap -- that is a two-month return "
            "wearing a one-month label",
        )

    def test_cagr_and_drawdown_known_values(self):
        # +100% then -50% returns exactly to par: CAGR 0, max drawdown -50%.
        r = [1.0, -0.5]
        self.assertAlmostEqual(metrics.cagr(r), 0.0, places=12)
        mdd, _, _ = metrics.max_drawdown(r)
        self.assertAlmostEqual(mdd, -0.5, places=12)

    def test_sharpe_of_constant_series_is_undefined_not_infinite(self):
        import math
        self.assertTrue(math.isnan(metrics.sharpe([0.01] * 24)))


class TestAggregation(unittest.TestCase):
    def test_current_month_is_dropped(self):
        """The in-progress month must never reach the monthly series."""
        today = dt.date.today()
        daily = []
        # a complete prior month
        prev_y, prev_m = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
        for day in range(1, 26):
            daily.append((dt.date(prev_y, prev_m, day).isoformat(), 100.0 + day))
        # plus some days in the CURRENT month
        for day in range(1, min(today.day, 20) + 1):
            daily.append((dt.date(today.year, today.month, day).isoformat(), 999.0))

        out = fetch.aggregate_monthly(daily, min_days=5)
        got_months = [m for m, _, _, _ in out]
        current_key = f"{today.year:04d}-{today.month:02d}-01"
        self.assertNotIn(
            current_key, got_months,
            "the incomplete current month leaked into the monthly series",
        )
        self.assertIn(f"{prev_y:04d}-{prev_m:02d}-01", got_months)

    def test_monthly_close_is_last_daily_close(self):
        daily = [(f"2005-03-{d:02d}", 10.0 + d) for d in range(1, 21)]
        out = fetch.aggregate_monthly(daily, min_days=5)
        self.assertEqual(len(out), 1)
        month, close, n_days, last_day = out[0]
        self.assertEqual(month, "2005-03-01")
        self.assertEqual(close, 30.0)            # day 20 -> 10 + 20
        self.assertEqual(n_days, 20)
        self.assertEqual(last_day, "2005-03-20")

    def test_thin_months_are_dropped(self):
        daily = [(f"2005-03-{d:02d}", 10.0) for d in range(1, 4)]   # 3 days only
        self.assertEqual(fetch.aggregate_monthly(daily, min_days=10), [])


class TestWeights(unittest.TestCase):
    def test_equal_weights_sum_to_one(self):
        w = model.equal_weights(["A", "B", "C", "D"])
        self.assertAlmostEqual(sum(w.values()), 1.0, places=12)
        self.assertEqual(len(set(w.values())), 1)

    def test_cluster_weights_split_risk_sector_cluster_market(self):
        # two sectors; commodity has clusters {energy: A, B} and {metals: C}
        active = ["A", "B", "C", "D"]
        sector_of = {"A": "commodity", "B": "commodity", "C": "commodity", "D": "bond"}
        cluster_of = {"A": "energy", "B": "energy", "C": "metals", "D": "us"}
        inv = {s: 1.0 for s in active}            # equal vols -> weights = risk
        w = model.cluster_risk_weights(active, inv, sector_of, cluster_of)
        self.assertAlmostEqual(sum(w.values()), 1.0, places=12)
        self.assertAlmostEqual(w["D"], 0.5)        # bond sector: half the risk
        self.assertAlmostEqual(w["C"], 0.25)       # metals: half the commodity half
        self.assertAlmostEqual(w["A"], 0.125)      # energy split between two

    def test_vol_target_scales_to_ex_ante_vol(self):
        import random
        rng = random.Random(0)
        months = months_from(2000, 40)
        rets = {"A": {m: rng.gauss(0, 0.02) for m in months}}
        sd = __import__("statistics").stdev([rets["A"][m] for m in months[4:40]])
        out = model.scale_to_target_vol({"A": 0.5}, rets, months, 39, 36, 0.10, 100.0)
        self.assertAlmostEqual(abs(out["A"]) * sd * 12 ** 0.5, 0.10, places=6)
        capped = model.scale_to_target_vol({"A": 0.5}, rets, months, 39, 36, 0.10, 1.0)
        self.assertAlmostEqual(abs(capped["A"]), 1.0)   # gross capped at 1x


if __name__ == "__main__":
    unittest.main(verbosity=2)
