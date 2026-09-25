"""
Data-contract and model-correction tests. Run with:

    python -m unittest discover -s tests -v

Each test calls the production function it protects, never a re-implementation
of it inside the test. The last class checks the built database itself and is
skipped until `python run.py fetch` has run.
"""

import datetime as dt
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config as C
from mf import metrics, model, panel as P, research, sources as S, store


def months_from(year: int, n: int) -> list[str]:
    return P.month_range(f"{year:04d}-01-01", f"{year + n // 12 + 1:04d}-12-01")[:n]


def memdb() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(store.SCHEMA)
    return conn


class TestBondModel(unittest.TestCase):
    def test_par_duration_known_value(self):
        # 10-year par bond, 5% yield, semi-annual coupons: modified duration 7.7946.
        self.assertAlmostEqual(model._par_modified_duration(0.05, 10.0), 7.7946, places=4)

    def test_duration_limits(self):
        self.assertAlmostEqual(model._par_modified_duration(0.0, 10.0), 10.0)
        # negative yields are valid: duration slightly ABOVE maturity-limit value
        self.assertGreater(model._par_modified_duration(-0.005, 10.0), 10.0 * 0.99)

    def test_flat_yield_return_is_carry(self):
        months = months_from(2000, 24)
        rets = model.bond_returns_from_yields({m: 5.0 for m in months}, months, 10.0)
        for r in rets.values():
            self.assertAlmostEqual(r, 0.05 / 12, places=12)

    def test_negative_yields_are_not_skipped(self):
        months = months_from(2016, 4)
        ylds = dict(zip(months, [0.2, -0.1, -0.3, -0.2]))
        rets = model.bond_returns_from_yields(ylds, months, 10.0)
        self.assertEqual(len(rets), 3)
        self.assertGreater(rets[months[2]], 0)     # yields fell -> price up

    def test_zero_coupon_duration_exceeds_par(self):
        months = months_from(2000, 2)
        ylds = {months[0]: 4.0, months[1]: 5.0}
        par = model.bond_returns_from_yields(ylds, months, 10.0)[months[1]]
        zc = model.bond_returns_from_yields(ylds, months, 10.0, zero_coupon=True)[months[1]]
        self.assertLess(zc, par)


class TestFuturesParsing(unittest.TestCase):
    """pysystemtrade files: exact returns across a roll, and the carry sign."""

    ADJ = ("DATETIME,price\n2020-01-30 23:00:00,98\n2020-01-31 23:00:00,99\n"
           "2020-02-03 23:00:00,104\n")
    MULT = ("DATETIME,CARRY,CARRY_CONTRACT,PRICE,PRICE_CONTRACT,FORWARD,FORWARD_CONTRACT\n"
            "2020-01-30 23:00:00,51,20200300,50,20200600,,20200900\n"
            "2020-01-31 23:00:00,51,20200300,50,20200600,,20200900\n"
            "2020-02-03 23:00:00,56,20200600,55,20200900,,20201200\n")

    def _files(self, d):
        a, m = Path(d) / "adj.csv", Path(d) / "mult.csv"
        a.write_text(self.ADJ, encoding="utf-8")
        m.write_text(self.MULT, encoding="utf-8")
        return a, m

    def test_daily_index_uses_held_contract_price(self):
        with tempfile.TemporaryDirectory() as d:
            idx = S.parse_pst_daily_index(*self._files(d))
        # day 2: adjusted +1 on a held price of 50 -> A_{d-1} + (P_d - A_d) = 98 + (50 - 99) = 49
        self.assertAlmostEqual(idx["2020-01-31"], 1 + 1 / 49)
        # roll day: +5 on the new contract, base 99 + (55 - 104) = 50
        self.assertAlmostEqual(idx["2020-02-03"] / idx["2020-01-31"], 1 + 5 / 50)

    def test_carry_is_positive_in_backwardation(self):
        with tempfile.TemporaryDirectory() as d:
            _, m = self._files(d)
            carry = S.parse_pst_carry(m)
        # nearer contract (51) above the held one (50), 3 months apart: backwardation
        self.assertAlmostEqual(carry["2020-01-30"], (51 / 50 - 1) * 12 / 3 * 100)
        self.assertGreater(carry["2020-01-30"], 0)


class TestVolFloor(unittest.TestCase):
    def test_sector_floor_limits_leverage_of_a_calm_market(self):
        import random
        rng = random.Random(10)
        months = months_from(2000, 60)
        calm = {m: 100 * (1.0005 ** i) * (1 + rng.gauss(0, 0.001)) for i, m in enumerate(months)}
        wild = {m: 100 * (1.01 ** i) * (1 + rng.gauss(0, 0.03)) for i, m in enumerate(months)}
        panel = {"A": calm, "B": wild}
        sec = {"A": "bond", "B": "bond"}
        kw = dict(lookback=12, cost_bps=0, weight_scheme="inverse_vol", vol_window=24)
        plain = model.run_backtest(panel, months, sec, {}, **kw)
        floored = model.run_backtest(panel, months, sec, {}, vol_floor=0.5, **kw)
        m = plain.months[-1]
        share = lambda r: abs(r.positions["A"][m]) / (abs(r.positions["A"][m]) + abs(r.positions["B"][m]))  # noqa: E731
        self.assertGreater(share(plain), 0.9)       # without a floor the calm market dominates
        self.assertLess(share(floored), share(plain))


class TestFxParsing(unittest.TestCase):
    CSV = ("FREQ,REF_AREA,CURRENCY,COLLECTION,TIME_PERIOD,OBS_VALUE\n"
           "M,JP,JPY,E,2020-01,150\nM,JP,JPY,E,2020-02,140\n"
           "M,JP,JPY,A,2020-01,999\n")

    def test_end_of_period_rows_only_and_inverted(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.csv"
            p.write_text(self.CSV, encoding="utf-8")
            fx = S.parse_bis_fx(p, {"JP-JPY": "fx_jpy"})["fx_jpy"]
        self.assertAlmostEqual(fx["2020-01-01"], 1 / 150)   # the 'A' row is ignored
        months = ["2020-01-01", "2020-02-01"]
        r = model.monthly_returns(fx, months)["2020-02-01"]
        self.assertGreater(r, 0, "fewer yen per dollar must be a positive yen return")


class TestBundesbankParsing(unittest.TestCase):
    def test_german_and_english_layouts_parse_identically(self):
        german = ('"";BBSIS.X;BBSIS.X_FLAGS\nDezimalstellen;2;\n'
                  "2020-01-30;-0,40;\n2020-01-31;-0,43;\n2020-02-01;.;Kein Wert\n")
        english = ('"",BBSIS.X,BBSIS.X_FLAGS\nDecimals,2,\n'
                   "2020-01-30,-0.40,\n2020-01-31,-0.43,\n2020-02-01,.,No value\n")
        out = []
        with tempfile.TemporaryDirectory() as d:
            for i, text in enumerate((german, english)):
                p = Path(d) / f"{i}.csv"
                p.write_text(text, encoding="utf-8")
                out.append(S.parse_bundesbank(p))
        self.assertEqual(out[0], {"2020-01-01": -0.43})
        self.assertEqual(out[0], out[1])


class TestCarry(unittest.TestCase):
    def _db(self):
        conn = memdb()
        months = months_from(2020, 3)
        rates = {"CBPOL_US": [2.0, 3.0, 3.0], "CBPOL_AU": [5.0, 5.0, 5.0]}
        for key, vals in rates.items():
            store.replace_series(conn, "yields", key, "test", list(zip(months, vals)))
        for key in ("CBPOL_CA", "CBPOL_CH", "CBPOL_GB", "CBPOL_JP", "CBPOL_NZ",
                    "CBPOL_DE", "CBPOL_XM"):
            store.replace_series(conn, "yields", key, "test", [(m, 1.0) for m in months])
        return conn, months

    def test_fx_carry_uses_start_of_month_differential(self):
        conn, months = self._db()
        aud = P.fx_carry(conn, months)["fx_aud"]
        # month 2 uses month-1 rates (5 - 2), month 3 uses month-2 rates (5 - 3)
        self.assertAlmostEqual(aud[months[1]], 0.03 / 12)
        self.assertAlmostEqual(aud[months[2]], 0.02 / 12)
        self.assertNotIn(months[0], aud)

    def test_low_yielder_has_negative_carry(self):
        conn, months = self._db()
        self.assertLess(P.fx_carry(conn, months)["fx_jpy"][months[1]], 0)

    def test_bond_funding_deducts_prior_month_tbill(self):
        months = months_from(2020, 3)
        funding = {k: {months[0]: 6.0, months[1]: 1.0} for k in C.BONDS}
        funding["DE_10Y"] = {months[0]: -0.5, months[1]: -0.5}
        adj = P.bond_funding(funding, months)
        self.assertAlmostEqual(adj["ust_10y"][months[1]], -((1.06) ** (1 / 12) - 1))
        self.assertAlmostEqual(adj["ust_10y"][months[2]], -((1.01) ** (1 / 12) - 1))
        # a Bund is funded at the (here negative) euro rate, not the US one
        self.assertGreater(adj["de_10y"][months[1]], 0)

    def test_funding_rates_use_local_rate_with_precedence(self):
        conn = memdb()
        months = months_from(1998, 14)
        store.replace_series(conn, "yields", "CBPOL_DE", "t", [(m, 3.0) for m in months[:12]])
        store.replace_series(conn, "yields", "CBPOL_XM", "t", [(m, 2.0) for m in months[12:]])
        store.replace_series(conn, "yields", "3Mo", "t", [(m, 5.0) for m in months])
        f = P.funding_rates(conn)
        self.assertEqual(f["DE_10Y"][months[0]], 3.0)
        self.assertEqual(f["DE_10Y"][months[13]], 2.0)
        self.assertEqual(f["10Y"][months[0]], 5.0)


class TestProvenance(unittest.TestCase):
    def test_replace_series_leaves_no_stale_rows(self):
        conn = memdb()
        store.replace_series(conn, "yields", "DE_10Y", "ecb_avg",
                             [("1990-01-01", 7.0), ("1998-01-01", 5.0)])
        store.replace_series(conn, "yields", "DE_10Y", "bundesbank", [("1998-01-01", 5.1)])
        self.assertEqual(store.load_yields(conn, "DE_10Y"), [("1998-01-01", 5.1)])
        self.assertEqual(store.sources(conn, "yields")["DE_10Y"], {"bundesbank"})

    def test_averaged_symbols_follow_recorded_source(self):
        conn = memdb()
        store.replace_series(conn, "prices", "wb_gold", "wb_pinksheet", [("2000-01-01", 1.0)])
        store.replace_series(conn, "prices", "fx_eur", "bis_xru_eop", [("2000-01-01", 1.0)])
        self.assertEqual(P.averaged_symbols(conn), {"wb_gold"})

    def test_daily_parsers_drop_the_current_month(self):
        today = dt.date.today()
        daily = {today.replace(day=1).isoformat(): 1.0, "2000-01-31": 2.0}
        out = S._last_per_month(daily)
        self.assertEqual(out, {"2000-01-01": 2.0})


class TestAveragedLag(unittest.TestCase):
    def test_lagged_position_equals_prior_months_signal(self):
        months = months_from(2000, 36)
        px = {m: 100 + (i if i < 18 else 36 - i) * 2.0 for i, m in enumerate(months)}
        kw = dict(lookback=12, cost_bps=0, weight_scheme="equal")
        plain = model.run_backtest({"x": px}, months, {"x": "commodity"}, {}, **kw)
        lag = model.run_backtest({"x": px}, months, {"x": "commodity"}, {},
                                 averaged_symbols={"x"}, **kw)
        pp, pl = plain.positions["x"], lag.positions["x"]
        self.assertTrue(pl)
        for m, w in pl.items():
            prev = months[months.index(m) - 1]
            self.assertEqual(w, pp[prev], f"{m}: lagged position != previous plain one")

    def test_no_zero_padding_before_first_lagged_signal(self):
        months = months_from(2000, 30)
        px = {m: 100.0 + i for i, m in enumerate(months)}
        lag = model.run_backtest({"x": px}, months, {"x": "commodity"}, {},
                                 lookback=12, cost_bps=0, averaged_symbols={"x"})
        self.assertTrue(all(abs(w) == 1.0 for w in lag.positions["x"].values()))


class TestSignalsAndSizing(unittest.TestCase):
    def test_tsmom_is_sign_of_trailing_return(self):
        months = months_from(2000, 15)
        px = {m: 100.0 + i for i, m in enumerate(months)}
        px[months[14]] = 50.0                      # below its level 12 months ago
        sig = model.tsmom_signals(px, months, 12)
        self.assertNotIn(months[11], sig)          # needs 12 months of history
        self.assertEqual(sig[months[12]], 1)
        self.assertEqual(sig[months[14]], -1)

    def test_excess_index_compounds_returns(self):
        months = months_from(2000, 3)
        idx = model.excess_index({months[1]: 0.10, months[2]: -0.5}, months)
        self.assertAlmostEqual(idx[months[2]], 1.1 * 0.5)

    def _two_markets(self, scheme, sectors):
        months = months_from(2000, 60)
        # A zig-zags twice as hard as B; both trend up so both are long.
        a = {m: 100 * (1.01 ** i) * (1.04 if i % 2 else 0.96) for i, m in enumerate(months)}
        b = {m: 100 * (1.01 ** i) * (1.02 if i % 2 else 0.98) for i, m in enumerate(months)}
        res = model.run_backtest({"A": a, "B": b}, months, sectors, {}, lookback=12,
                                 cost_bps=0, weight_scheme=scheme, vol_window=24)
        m = res.months[-1]
        return res.positions["A"][m], res.positions["B"][m]

    def test_inverse_vol_gives_equal_risk(self):
        wa, wb = self._two_markets("inverse_vol", {"A": "commodity", "B": "commodity"})
        self.assertAlmostEqual(abs(wa) + abs(wb), 1.0)
        self.assertAlmostEqual(abs(wb) / abs(wa), 2.0, delta=0.15)

    def test_sector_inverse_vol_splits_risk_by_sector(self):
        wa, wb = self._two_markets("sector_inverse_vol", {"A": "commodity", "B": "bond"})
        self.assertAlmostEqual(abs(wb) / abs(wa), 2.0, delta=0.15)

    def test_roll_cost_and_era_multiplier(self):
        months = months_from(2000, 30)
        px = {m: 100.0 + i for i, m in enumerate(months)}   # always long
        res = model.run_backtest({"A": px}, months, {"A": "commodity"}, {},
                                 lookback=12, cost_bps=0,
                                 roll_cost_by_market={"A": 12.0},
                                 cost_eras=[("1900-01-01", 3.0), ("2001-06-01", 1.0)])
        # no trading after the first month: cost is the roll charge alone
        for m, c in zip(res.months[1:], res.cost[1:]):
            expect = 12.0 / 10_000 * (3.0 if m < "2001-06-01" else 1.0)
            self.assertAlmostEqual(c, expect, places=12)


class TestBootstrap(unittest.TestCase):
    def test_block_bootstrap_is_reproducible_and_brackets_estimate(self):
        import random
        rng = random.Random(1)
        x = [rng.gauss(0.005, 0.03) for _ in range(300)]
        a = metrics.bootstrap_sharpe_ci(x, 500, 7, block=12)
        b = metrics.bootstrap_sharpe_ci(x, 500, 7, block=12)
        self.assertEqual(a, b)
        self.assertLess(a[0], metrics.sharpe(x))
        self.assertGreater(a[1], metrics.sharpe(x))


class TestFundamentalLaw(unittest.TestCase):
    def test_law_matches_realised_sharpe_on_synthetic_streams(self):
        """Equal-vol streams with a common factor: S_bar x DM must match the
        realised Sharpe of their equal-weight average."""
        import random
        rng = random.Random(3)
        months = months_from(1950, 600)
        streams = {f"m{k}": {} for k in range(20)}
        for m in months:
            common = rng.gauss(0, 1)
            for s in streams:
                streams[s][m] = 0.08 + 0.4 * common + 0.9165 * rng.gauss(0, 1)
        w = research._window_stats(streams, months)
        self.assertAlmostEqual(w["predicted"], w["realised"], delta=0.03)
        self.assertAlmostEqual(w["rho_bar"], 0.16, delta=0.03)


class TestSpliceAndSelection(unittest.TestCase):
    def test_splice_corrects_average_roll_gap(self):
        months = P.month_range("2004-12-01", "2024-09-01")
        import random
        rng = random.Random(4)
        fut, yf, lf, ly = {}, {}, 100.0, 100.0
        for m in months:
            r = rng.gauss(0, 0.05)
            lf *= 1 + r
            ly *= 1 + r + 0.01            # Yahoo runs 1% a month hot (roll gap)
            if m <= C.FUTURES_END:
                fut[m] = lf
            yf[m] = ly
        out, info = P.splice_commodity(fut, yf)
        self.assertTrue(info["spliced"])
        # Yahoo = futures + 1%/month exactly: regression recovers a = -1%, b = 1
        self.assertAlmostEqual(info["beta"], 1.0, places=2)
        self.assertAlmostEqual(info["alpha"], -0.01, places=3)
        after = [m for m in sorted(out) if m > C.FUTURES_END]
        self.assertEqual(after[0], "2024-04-01")
        r_out = out[after[0]] / out[C.FUTURES_END] - 1
        r_yf = yf[after[0]] / yf[C.FUTURES_END] - 1
        self.assertAlmostEqual(r_out, info["alpha"] + info["beta"] * r_yf, places=12)

    def test_splice_corrects_volatility_difference(self):
        """A front month twice as volatile as the futures held: slope ~0.5."""
        import random
        rng = random.Random(8)
        months = P.month_range("2004-12-01", "2024-09-01")
        fut, yf, lf, ly = {}, {}, 100.0, 100.0
        for m in months:
            r = rng.gauss(0, 0.04)
            lf *= 1 + r
            ly *= 1 + 2 * r
            if m <= C.FUTURES_END:
                fut[m] = lf
            yf[m] = ly
        _, info = P.splice_commodity(fut, yf)
        self.assertAlmostEqual(info["beta"], 0.5, delta=0.02)

    def test_selection_schedule_is_point_in_time(self):
        """Truncating the data after a date must not change any earlier review."""
        import random
        rng = random.Random(9)
        months = P.month_range("1990-01-01", "2010-12-01")

        def lvl(rs):
            out, v = {}, 100.0
            for m, r in zip(months, rs):
                v *= 1 + r
                out[m] = v
            return out
        f = [rng.gauss(0, 0.03) for _ in months]
        cand = {"ust_10y": lvl(f), "ust_5y": lvl([x + rng.gauss(0, 0.01) for x in f]),
                "cm_wti": lvl([rng.gauss(0, 0.08) for _ in months]),
                "cm_corn": lvl([rng.gauss(0, 0.06) for _ in months])}
        full, _ = P.selection_schedule(cand, months)
        cut = "2001-06-01"
        short_cand = {s: {m: v for m, v in px.items() if m <= cut} for s, px in cand.items()}
        short, _ = P.selection_schedule(short_cand, [m for m in months if m <= cut])
        for m in short:
            self.assertEqual(short[m], full[m], m)
        self.assertNotIn("ust_5y", full[months[-1]])      # near copy of the 10Y

    def test_splice_refused_when_sources_disagree(self):
        import random
        rng = random.Random(5)
        months = P.month_range("2004-12-01", "2024-09-01")
        fut = {m: 100 + rng.random() for m in months if m <= C.FUTURES_END}
        yf = {m: 100 + rng.random() for m in months}
        out, info = P.splice_commodity(fut, yf)
        self.assertFalse(info["spliced"])
        self.assertEqual(max(out), C.FUTURES_END)

    def test_selection_drops_redundant_and_illiquid_markets(self):
        import random
        rng = random.Random(6)
        months = P.month_range("2000-01-01", "2010-12-01")
        base = [rng.gauss(0, 0.03) for _ in months]
        other = [rng.gauss(0, 0.03) for _ in months]

        def level(rs):
            out, v = {}, 100.0
            for m, r in zip(months, rs):
                v *= 1 + r
                out[m] = v
            return out
        panel = {"ust_10y": level(base),                                   # most liquid
                 "ust_5y": level([b + rng.gauss(0, 0.005) for b in base]),  # near copy
                 "cm_wti": level(other),                                   # independent
                 "fx_sek": level([rng.gauss(0, 0.03) for _ in months])}    # illiquid
        kept, rows = P.select_universe(panel, months)
        self.assertEqual(set(kept), {"ust_10y", "cm_wti"})


@unittest.skipUnless(C.DB_PATH.exists(), "database not built yet (python run.py fetch)")
class TestBuiltDatabase(unittest.TestCase):
    """The data contract, checked on the real database."""

    @classmethod
    def setUpClass(cls):
        cls.conn = store.connect(C.DB_PATH)
        cls.panel, cls.months, cls.holes, cls.info = P.build_panel(cls.conn, universe="all")

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def test_every_series_has_exactly_one_source(self):
        for table in ("prices", "yields", "benchmarks"):
            for key, srcs in store.sources(self.conn, table).items():
                self.assertEqual(len(srcs), 1, f"{table}.{key} mixes {srcs}")

    def test_no_index_market_uses_averaged_data(self):
        self.assertEqual(P.averaged_symbols(self.conn) & set(self.panel), set())

    def test_month_end_fx_shows_no_averaging_autocorrelation(self):
        """Averaging a random walk gives lag-1 autocorrelation of about +0.25;
        month-end FX must sit well below it."""
        for s, series in self.panel.items():
            if C.CANDIDATES[s][1] != "fx":
                continue
            r = model.monthly_returns(series, self.months)
            x = [r[m] for m in self.months if m in r]
            self.assertLess(metrics.correlation(x[:-1], x[1:]), 0.15, s)

    def test_futures_and_splice_quality(self):
        for s, v in self.info["splice"].items():
            if v["spliced"]:
                self.assertGreaterEqual(v["fidelity"], C.SPLICE_MIN_FIDELITY, s)

    def test_no_holes_inside_any_market_history(self):
        self.assertEqual(self.holes, {})
