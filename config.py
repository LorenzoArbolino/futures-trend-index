"""
Single source of truth for the universe and every model parameter.

Design rule: if a number could reasonably be argued about, it lives HERE and not
inside the code, so a reviewer can audit every assumption on one screen and a
variant is a one-line change.

Nothing in this file does any work. It is pure declaration.
"""

from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
CACHE = DATA / "cache"            # raw, untouched downloads
DB_PATH = DATA / "mf.sqlite"      # normalised store, rebuildable with `run.py fetch`
OUTPUT = ROOT / "output"

# ---------------------------------------------------------------------------
# Candidate markets
# ---------------------------------------------------------------------------
# Non-equity only: the index is meant to sit on top of an equity portfolio.
# Candidates are liquid futures with free data. The traded universe is picked
# from them by the outcome-blind rule in SELECTION below.
#
# Commodities: daily back-adjusted futures from the open-source pysystemtrade
# project (github.com/pst-group/pysystemtrade, data to 2024-03), turned into
# month-end excess returns. After FUTURES_END the series continue on Yahoo
# front-month month-end closes, corrected for their average roll gap (see
# SPLICE below).
#   symbol -> (display name, pysystemtrade code, Yahoo code, avg daily volume in
#              thousands of contracts -- an approximate liquidity rank)
COMMODITIES = {
    "cm_wti":      ("WTI crude",       "CRUDE_W",   "CL=F", 1000),
    "cm_brent":    ("Brent crude",     "BRENT_W",   "BZ=F", 800),
    "cm_natgas":   ("Natural gas",     "GAS_US",    "NG=F", 400),
    "cm_corn":     ("Corn",            "CORN",      "ZC=F", 350),
    "cm_gold":     ("Gold",            "GOLD",      "GC=F", 250),
    "cm_soybeans": ("Soybeans",        "SOYBEAN",   "ZS=F", 250),
    "cm_heatoil":  ("Heating oil",     "HEATOIL",   "HO=F", 150),
    "cm_gasoline": ("RBOB gasoline",   "GASOILINE", "RB=F", 150),
    "cm_sugar":    ("Sugar",           "SUGAR11",   "SB=F", 150),
    "cm_wheat":    ("Wheat (SRW)",     "WHEAT",     "ZW=F", 120),
    "cm_soyoil":   ("Soybean oil",     "SOYOIL",    "ZL=F", 120),
    "cm_soymeal":  ("Soybean meal",    "SOYMEAL",   "ZM=F", 120),
    "cm_copper":   ("Copper",          "COPPER",    "HG=F", 100),
    "cm_silver":   ("Silver",          "SILVER",    "SI=F", 70),
    "cm_cattle":   ("Live cattle",     "LIVECOW",   "LE=F", 50),
    "cm_hogs":     ("Lean hogs",       "LEANHOG",   "HE=F", 50),
    "cm_cotton":   ("Cotton",          "COTTON2",   "CT=F", 40),
    "cm_kcwheat":  ("Wheat (HRW)",     "REDWHEAT",  "KE=F", 40),
    "cm_coffee":   ("Coffee",          "COFFEE",    "KC=F", 30),
    "cm_cocoa":    ("Cocoa",           "COCOA",     "CC=F", 30),
    "cm_platinum": ("Platinum",        "PLAT",      "PL=F", 20),
}

# Government bonds, built from month-end yields with a duration model (their
# monthly returns correlate 0.93-0.96 with the front-month futures).
#   yield series -> (symbol, display name, maturity in years, volume rank)
BONDS = {
    "10Y":    ("ust_10y",  "US 10Y",     10.0, 1500),
    "5Y":     ("ust_5y",   "US 5Y",      5.0,  1000),
    "DE_10Y": ("de_10y",   "Bund 10Y",   10.0, 700),
    "2Y":     ("ust_2y",   "US 2Y",      2.0,  600),
    "DE_5Y":  ("de_5y",    "Bobl 5Y",    5.0,  500),
    "DE_2Y":  ("de_2y",    "Schatz 2Y",  2.0,  400),
    "30Y":    ("ust_30y",  "US 30Y",     30.0, 350),
    "GB_10Y": ("gilt_10y", "Gilt 10Y",   10.0, 150),
    "CA_10Y": ("cgb_10y",  "Canada 10Y", 10.0, 150),
    "JP_10Y": ("jgb_10y",  "JGB 10Y",    10.0, 40),
}
# BoE IUDMNZC is a zero-coupon yield; the others are par yields.
ZERO_COUPON_BONDS = {"GB_10Y"}

# Currencies vs USD: BIS end-of-period rates, inverted so that up = foreign
# currency strengthens (the CME convention), plus the policy-rate carry.
#   'REF_AREA-CURRENCY' -> (symbol, display name, volume rank)
FX_PAIRS = {
    "XM-EUR": ("fx_eur", "Euro",               200),
    "JP-JPY": ("fx_jpy", "Japanese yen",       150),
    "GB-GBP": ("fx_gbp", "British pound",      100),
    "AU-AUD": ("fx_aud", "Australian dollar",  100),
    "CA-CAD": ("fx_cad", "Canadian dollar",    80),
    "CH-CHF": ("fx_chf", "Swiss franc",        30),
    "NZ-NZD": ("fx_nzd", "New Zealand dollar", 20),
    "SE-SEK": ("fx_sek", "Swedish krona",      3),
    "NO-NOK": ("fx_nok", "Norwegian krone",    3),
}
FX_CURRENCIES = [k.split("-")[1] for k in FX_PAIRS]

# symbol -> (display name, sector); symbol -> liquidity rank
CANDIDATES: dict[str, tuple[str, str]] = {}
LIQUIDITY: dict[str, float] = {}
for _sym, (_name, _pst, _yf, _vol) in COMMODITIES.items():
    CANDIDATES[_sym], LIQUIDITY[_sym] = (_name, "commodity"), _vol
for _sym, _name, _mat, _vol in BONDS.values():
    CANDIDATES[_sym], LIQUIDITY[_sym] = (_name, "bond"), _vol
for _sym, _name, _vol in FX_PAIRS.values():
    CANDIDATES[_sym], LIQUIDITY[_sym] = (_name, "fx"), _vol
SECTORS = ("commodity", "bond", "fx")

# ---------------------------------------------------------------------------
# Universe selection (outcome-blind)
# ---------------------------------------------------------------------------
# Most CTAs trade a selective list, and our own breadth study shows why: highly
# correlated markets add cost and complexity but almost no independent bets.
# Rule, applied to the candidates in order of liquidity:
#   1. keep a market only if its liquidity is at least MIN_VOLUME (thousand
#      contracts a day);
#   2. keep it only if its data reaches the present (an index that silently
#      loses a market in 2024 is worse than one that never held it; this
#      excludes livestock, whose Yahoo series track the futures too poorly to
#      splice);
#   3. keep it only if the correlation of its monthly RETURNS (not its trend
#      P&L -- performance never enters) with every market already kept is
#      below MAX_CORRELATION. At 0.70 a kept market already explains about half
#      of the candidate's variance (0.70^2 = 0.49): it adds little breadth.
# So WTI is kept and Brent dropped, the US 10Y is kept and the 5Y dropped, and so
# on. Correlations are measured over the backtest window.
SELECTION = dict(min_volume=15, max_correlation=0.70,
                 mode="annual", lookback=None, min_history=36, incumbent_margin=0.10)

# Economic clusters for cluster risk budgeting (fixed by economic grouping,
# not by any backtest result).
CLUSTERS = {
    "cm_wti": "energy", "cm_brent": "energy", "cm_natgas": "energy",
    "cm_heatoil": "energy", "cm_gasoline": "energy",
    "cm_gold": "metals", "cm_silver": "metals", "cm_platinum": "metals",
    "cm_copper": "metals",
    "cm_corn": "grains", "cm_soybeans": "grains", "cm_soyoil": "grains",
    "cm_soymeal": "grains", "cm_wheat": "grains", "cm_kcwheat": "grains",
    "cm_sugar": "softs", "cm_cotton": "softs", "cm_coffee": "softs",
    "cm_cocoa": "softs",
    "cm_cattle": "livestock", "cm_hogs": "livestock",
    "ust_2y": "us", "ust_5y": "us", "ust_10y": "us", "ust_30y": "us",
    "de_2y": "europe", "de_5y": "europe", "de_10y": "europe", "gilt_10y": "europe",
    "jgb_10y": "japan", "cgb_10y": "canada",
    "fx_eur": "europe", "fx_gbp": "europe", "fx_chf": "europe",
    "fx_sek": "europe", "fx_nok": "europe",
    "fx_aud": "commodity_bloc", "fx_cad": "commodity_bloc", "fx_nzd": "commodity_bloc",
    "fx_jpy": "yen",
}

# ---------------------------------------------------------------------------
# Commodity data: futures, then a Yahoo splice
# ---------------------------------------------------------------------------
# pysystemtrade's data stops here. Later months use Yahoo front-month
# month-end closes. Front-month series jump at each contract roll, so each
# market's Yahoo returns are corrected by their average gap to the true futures
# returns over the overlap SPLICE_CALIBRATION, and a market whose Yahoo returns
# track the futures poorly (correlation below SPLICE_MIN_FIDELITY; in practice
# the livestock contracts) stops at FUTURES_END instead of continuing on bad data.
FUTURES_END = "2024-03-01"
SPLICE_CALIBRATION = ("2005-01-01", "2024-03-01")
SPLICE_HOLDOUT = "2019-01-01"     # fit before, measure tracking error after
SPLICE_MIN_FIDELITY = 0.85
PST_BASE = ("https://raw.githubusercontent.com/pst-group/pysystemtrade/master/"
            "data/futures")

# Backtest window: starts at BACKTEST_START (the start of AQR's TSMOM series)
# and ends at the last month with data. A market ENTERS when its data begins
# (for FX: price and both policy rates; for commodities: the futures series),
# so the universe is point-in-time. It leaves only if its data stops.
BACKTEST_START = "1985-01-01"

# ---------------------------------------------------------------------------
# Signal and sizing
# ---------------------------------------------------------------------------
# The index, in one line each:
#   universe  re-selected every January from point-in-time data (SELECTION)
#   signal    average of the signs of the 1-, 3- and 12-month excess returns,
#             blended 75/25 with a carry signal
#   sizing    equal risk per sector, per economic cluster inside the sector
#             (CLUSTERS) and per market inside the cluster; no market's risk
#             estimate below half its sector's median (a volatility floor)
#   risk      positions scaled every month toward 15% EX-ANTE annual volatility
#             from the trailing covariance of the markets held, with gross
#             notional capped at 8x capital (the cap binds in calm periods, so
#             realised volatility runs below 15% then)
# The choices come from the trend-following literature and first principles
# (README, "Why it is built this way"); research.md reports each one before and
# after 2008 with paired confidence intervals. The design was iterated on the
# full sample, so 2008+ is a pseudo out-of-sample check, not a clean holdout.
# Carry is capped at 25% so the index stays a trend strategy.
HEADLINE = dict(signal_type="tsmom", signal_on="excess", multi_lookbacks=(1, 3, 12),
                weight_scheme="cluster_inverse_vol", carry_weight=0.25,
                target_vol=0.15, max_leverage=8.0, vol_floor=0.5)
LOOKBACK_MONTHS = 12
LOOKBACK_GRID = (1, 3, 6, 9, 12, 18, 24)
VOL_WINDOW_MONTHS = 36      # trailing window for the volatility estimates
DESIGN_SPLIT = "2008-01-01"   # design choices use data before this month only

# ---------------------------------------------------------------------------
# Carry: spot and yields -> futures-equivalent returns
# ---------------------------------------------------------------------------
# FX: covered interest parity. A long foreign-currency future earns roughly the
# spot move plus (r_foreign - r_usd)/12 per month; policy rates from BIS.
POLICY_RATE_AREAS = {"AU": "fx_aud", "CA": "fx_cad", "CH": "fx_chf",
                     "GB": "fx_gbp", "JP": "fx_jpy", "NZ": "fx_nzd",
                     "SE": "fx_sek", "NO": "fx_nok"}
POLICY_RATE_EUR = ("DE", "XM", "fx_eur")   # Bundesbank pre-1999, ECB after

# Bonds: the duration model gives a TOTAL return (coupon + price). A bond
# future earns that minus its LOCAL short rate (a Bund future is financed in
# euros). US bonds use the 3-month T-bill; the others their central bank's
# policy rate, BIS codes in order of precedence.
BOND_FUNDING = {
    "2Y": ["TBILL"], "5Y": ["TBILL"], "10Y": ["TBILL"], "30Y": ["TBILL"],
    "DE_2Y": ["XM", "DE"], "DE_5Y": ["XM", "DE"], "DE_10Y": ["XM", "DE"],
    "GB_10Y": ["GB"], "JP_10Y": ["JP"], "CA_10Y": ["CA"],
}

# Commodities need no carry adjustment: futures returns already contain the
# roll yield.

# Monthly-AVERAGE sources. None feeds the index any more; the machinery (signal
# lagged one month, derived from each row's recorded source) stays for the
# research study on averaging.
AVERAGED_SOURCES = {"wb_pinksheet"}

# ---------------------------------------------------------------------------
# Costs
# ---------------------------------------------------------------------------
# Execution cost in bps of NOTIONAL traded (half spread + commission +
# slippage), today's electronic markets, about one tick per trade. Rolling a
# position costs about one trade per roll, charged monthly on the absolute
# position as bps x rolls / 12. Costs are scaled up for earlier decades.
#   symbol -> (bps per unit of notional traded, contract rolls per year)
COSTS = {
    "cm_wti": (2, 12), "cm_brent": (2, 12), "cm_natgas": (5, 12),
    "cm_heatoil": (2, 12), "cm_gasoline": (2, 12),
    "cm_gold": (1.5, 6), "cm_silver": (3, 5), "cm_platinum": (5, 4),
    "cm_copper": (3, 5),
    "cm_corn": (4, 5), "cm_soybeans": (3, 7), "cm_soyoil": (4, 8),
    "cm_soymeal": (4, 8), "cm_wheat": (5, 5), "cm_kcwheat": (6, 5),
    "cm_sugar": (5, 4), "cm_cotton": (5, 5), "cm_coffee": (5, 5), "cm_cocoa": (5, 5),
    "cm_cattle": (3, 6), "cm_hogs": (4, 7),
    "ust_2y": (0.5, 4), "ust_5y": (0.8, 4), "ust_10y": (1, 4), "ust_30y": (1.5, 4),
    "de_2y": (0.5, 4), "de_5y": (0.8, 4), "de_10y": (1, 4),
    "gilt_10y": (1.5, 4), "jgb_10y": (1, 4), "cgb_10y": (1.5, 4),
    "fx_eur": (1, 4), "fx_jpy": (1, 4), "fx_gbp": (1.5, 4), "fx_chf": (2, 4),
    "fx_cad": (1.5, 4), "fx_aud": (1.5, 4), "fx_nzd": (3, 4),
    "fx_sek": (5, 4), "fx_nok": (5, 4),
}
COST_BY_MARKET = {s: bps for s, (bps, _) in COSTS.items()}
ROLL_COST_BY_MARKET = {s: bps * rolls / 12.0 for s, (bps, rolls) in COSTS.items()}
COST_ERAS = [("1900-01-01", 3.0), ("1995-01-01", 2.0), ("2005-01-01", 1.0)]
COST_BPS_FALLBACK = 5.0
# Typical exchange initial margin as a fraction of notional, by sector (rough,
# current levels), used only to report margin-to-equity.
MARGIN_RATE = {"bond": 0.02, "fx": 0.04, "commodity": 0.08}
ANNUAL_OVERHEAD_BPS = 0     # account-level fees are not a property of the strategy

# ---------------------------------------------------------------------------
# Benchmarks and reporting
# ---------------------------------------------------------------------------
# S&P 500 total-return index from Yahoo: correlation reference and the equity
# leg of the stacked portfolio.
SPX_YAHOO = "^SP500TR"      # total return, dividends reinvested (from 1988)
SPX_SYMBOL = "spx"
SPX_FETCH_FROM = "1987-12-01"
MIN_TRADING_DAYS = 10       # fewer daily closes than this => no month-end close

# Return stacking: 100% equities + 100% of the index as a futures overlay. The
# overlay's financing is inside the futures prices; this spread (bps a year on
# the overlay) covers margin and operational drag.
STACK_FINANCING_SPREAD_BPS = 20
STACK_OVERLAY_VOLS = (0.0, 0.05, 0.10, 0.15, 0.20, 0.25)

# External benchmark: AQR Time Series Momentum (Moskowitz, Ooi & Pedersen 2012).
AQR_TSMOM_SLEEVES = {"commodity": "AQR_TSMOM_CM", "bond": "AQR_TSMOM_FI",
                     "fx": "AQR_TSMOM_FX"}

SUBPERIOD_SPLIT = "2008-01-01"

BOOTSTRAP_SAMPLES = 2000
BOOTSTRAP_BLOCK = 12
BOOTSTRAP_SEED = 20260922
MONTHS_PER_YEAR = 12

# ---------------------------------------------------------------------------
# Research (mf/research.py)
# ---------------------------------------------------------------------------
# World Bank Pink Sheet monthly AVERAGE prices: no longer part of the index;
# used only to show how poorly averaged spot data tracks futures.
#   Pink Sheet column -> commodity symbol it is compared with
WB_RESEARCH = {
    "Crude oil, WTI": "cm_wti", "Crude oil, Brent": "cm_brent",
    "Natural gas, US": "cm_natgas", "Gold": "cm_gold", "Silver": "cm_silver",
    "Platinum": "cm_platinum", "Copper": "cm_copper", "Maize": "cm_corn",
    "Wheat, US SRW": "cm_wheat", "Wheat, US HRW": "cm_kcwheat",
    "Soybeans": "cm_soybeans", "Soybean oil": "cm_soyoil",
    "Soybean meal": "cm_soymeal", "Sugar, world": "cm_sugar",
    "Cotton, A Index": "cm_cotton", "Coffee, Arabica": "cm_coffee",
    "Cocoa": "cm_cocoa",
}
