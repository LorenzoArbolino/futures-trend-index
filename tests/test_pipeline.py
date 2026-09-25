"""
End-to-end tests that need no downloaded data, so they run on a fresh clone and
in CI. A synthetic database with every candidate series is built in a temporary
file, and the whole context (universe selection, splice, carry, variants) is
computed from it exactly as `run.py` does.

    python -m unittest tests.test_pipeline -v
"""

import math
import os
import random
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config as C
from mf import metrics, model, panel as P, store


def _synthetic_db(path: Path, seed: int = 11, last: str = "2026-06-01"):
    """Random-walk prices and yields for every candidate, with the source
    names the real pipeline uses. There is no real trend in this data."""
    rng = random.Random(seed)
    conn = store.connect(path)
    months = P.month_range("1975-01-01", last)

    def walk(start, vol, drift=0.0):
        out, v = {}, start
        for m in months:
            v *= math.exp(rng.gauss(drift, vol))
            out[m] = v
        return out

    for sym in C.COMMODITIES:
        fut = walk(100.0, 0.07)
        store.replace_series(conn, "prices", sym, "pst_futures",
                             [(m, v) for m, v in fut.items() if m <= C.FUTURES_END])
        store.replace_series(conn, "yields", "CARRY_" + sym, "pst_futures",
                             [(m, rng.gauss(0, 5)) for m in months if m <= C.FUTURES_END])
        keys = sorted(fut)
        yf, v = {}, 100.0
        for a, b in zip(keys, keys[1:]):
            v *= fut[b] / fut[a] * (1 + rng.gauss(0.001, 0.005))
            if b >= "2000-01-01":
                yf[b] = v
        store.replace_series(conn, "prices", "yf_" + sym, "yahoo_front_month", sorted(yf.items()))
    # Rates: one random-walk short rate per area. Every bond in an area yields
    # exactly its area's funding rate, so bond carry is zero, and each currency's
    # spot drifts by minus its rate differential (uncovered interest parity), so
    # its carry-inclusive excess return is pure noise. Nothing here is predictable.
    area_of = {"2Y": "US", "5Y": "US", "10Y": "US", "30Y": "US", "DE_2Y": "EU",
               "DE_5Y": "EU", "DE_10Y": "EU", "GB_10Y": "GB", "JP_10Y": "JP",
               "CA_10Y": "CA"}
    rate = {}
    for area in ("US", "EU", "GB", "JP", "CA", "AU", "CH", "NZ", "SE", "NO"):
        r, level = {}, 3.0
        for m in months:
            level = max(-0.5, level + rng.gauss(0, 0.15))
            r[m] = level
        rate[area] = r
    for series in C.BONDS:
        store.replace_series(conn, "yields", series, "synthetic",
                             sorted(rate[area_of[series]].items()))
    store.replace_series(conn, "yields", "3Mo", "fed_h15", sorted(rate["US"].items()))
    cbpol = {"US": "US", "DE": "EU", "XM": "EU", "GB": "GB", "JP": "JP", "CA": "CA",
             "AU": "AU", "CH": "CH", "NZ": "NZ", "SE": "SE", "NO": "NO"}
    for code, area in cbpol.items():
        store.replace_series(conn, "yields", f"CBPOL_{code}", "bis_cbpol",
                             sorted(rate[area].items()))
    fx_area = {sym: cbpol[a] for a, sym in C.POLICY_RATE_AREAS.items()}
    fx_area[C.POLICY_RATE_EUR[2]] = "EU"
    for sym, _, _ in C.FX_PAIRS.values():
        px, v = {}, 1.0
        for prev, cur in zip(months, months[1:]):
            diff = (rate[fx_area[sym]][prev] - rate["US"][prev]) / 1200
            v *= math.exp(rng.gauss(0, 0.03)) * (1 - diff)
            px[cur] = v
        store.replace_series(conn, "prices", sym, "bis_xru_eop", sorted(px.items()))
    store.replace_series(conn, "prices", C.SPX_SYMBOL, "yahoo",
                         sorted(walk(100.0, 0.045, 0.006).items()))
    for name in list(C.AQR_TSMOM_SLEEVES.values()) + ["AQR_TSMOM"]:
        store.replace_series(conn, "benchmarks", name, "aqr_tsmom",
                             [(m, rng.gauss(0.005, 0.03)) for m in months])
    conn.close()


class TestPipelineEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        path = Path(cls.tmp.name) / "synthetic.sqlite"
        _synthetic_db(path)
        cls.conn = store.connect(path)
        cls.ctx = P.load_context(cls.conn)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        cls.tmp.cleanup()

    def test_context_builds_and_headline_hits_its_risk_target(self):
        base = self.ctx["base"]
        self.assertGreater(len(base), 300)
        vol = metrics.ann_vol(base.excess)
        self.assertLess(abs(vol - C.HEADLINE["target_vol"]), 0.04,
                        f"realised vol {vol:.3f} far from the target")

    def test_universe_rule_applies(self):
        kept = self.ctx["info"]["schedule"][self.ctx["months"][-1]]
        self.assertLess(len(kept), len(C.CANDIDATES))
        self.assertTrue(all(C.LIQUIDITY[s] >= C.SELECTION["min_volume"] for s in kept))

    def test_no_edge_on_trendless_data(self):
        """Random walks contain no trend: the index's Sharpe must be consistent
        with zero. A large positive value would mean information is leaking."""
        self.assertLess(abs(metrics.sharpe(self.ctx["base"].excess)), 0.6)

    def test_truncating_the_data_leaves_earlier_results_unchanged(self):
        """No look-ahead anywhere, universe selection included: cut the data at
        a date, re-select the universe and rerun the index on what is left;
        every month up to the cut must match the full run exactly."""
        ctx = self.ctx
        kw = P.ctx_kwargs(ctx)
        cut = "2005-06-01"
        months = [m for m in ctx["months"] if m <= cut]
        panel = {s: {m: v for m, v in px.items() if m <= cut} for s, px in ctx["panel"].items()}
        panel = {s: v for s, v in panel.items() if v}
        carry_sig = {s: {m: v for m, v in d.items() if m <= cut}
                     for s, d in ctx["carry_signal"].items()}
        schedule, _ = P.selection_schedule(panel, months)
        short = model.run_backtest(panel, months, ctx["sector_of"], ctx["rates"],
                                   **{**kw, "carry_signal": carry_sig,
                                      "universe_by_month": schedule})
        full = dict(zip(ctx["base"].months, ctx["base"].excess))
        self.assertGreater(len(short), 100)
        for m, x in zip(short.months, short.excess):
            self.assertAlmostEqual(x, full[m], places=12, msg=f"differs at {m}")


if __name__ == "__main__":
    unittest.main()
