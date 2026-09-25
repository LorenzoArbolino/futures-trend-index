"""
SQLite storage layer.

Why SQLite and not CSVs in a folder: it ships with Python, it enforces a primary
key (a re-run cannot silently duplicate rows), and anyone can open the database
in a SQLite browser without running this code.

Two rules this module enforces, both learned the hard way on this project:

  1. EVERY ROW CARRIES ITS SOURCE. The model treats a monthly-average price very
     differently from a month-end price (see panel.averaged_symbols), so the
     provenance of each row is data, not decoration.

  2. A SERIES IS REPLACED, NEVER PATCHED. Writing a series first deletes every
     existing row for it. An earlier version used INSERT OR REPLACE, and when the
     Bund source changed from ECB monthly averages (1990+) to Bundesbank daily
     (1997+), seven years of the old averaged rows survived underneath the new
     data and silently set the backtest start date.
"""

import sqlite3
from pathlib import Path

SCHEMA = """
-- Monthly prices / index levels (commodities, FX, benchmark equity).
CREATE TABLE IF NOT EXISTS prices (
    symbol TEXT NOT NULL,         -- e.g. 'wb_gold', 'bis_jpy', 'spx'
    date   TEXT NOT NULL,         -- 'YYYY-MM-01', the month the value belongs to
    close  REAL NOT NULL,
    source TEXT NOT NULL,         -- 'wb_pinksheet', 'bis_xru_eop', ...
    PRIMARY KEY (symbol, date)
);

-- Monthly yields and policy rates, annualised percent (e.g. 4.37).
CREATE TABLE IF NOT EXISTS yields (
    series    TEXT NOT NULL,      -- '3Mo', '10Y', 'DE_10Y', 'CBPOL_US', ...
    date      TEXT NOT NULL,
    yield_ann REAL NOT NULL,
    source    TEXT NOT NULL,
    PRIMARY KEY (series, date)
);

-- External benchmark returns (AQR factors), monthly, decimal.
CREATE TABLE IF NOT EXISTS benchmarks (
    series TEXT NOT NULL,
    date   TEXT NOT NULL,
    value  REAL NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY (series, date)
);

-- When was each series pulled, from where, and how many rows.
CREATE TABLE IF NOT EXISTS fetch_log (
    source     TEXT NOT NULL,
    symbol     TEXT,
    fetched_at TEXT NOT NULL,
    rows       INTEGER,
    note       TEXT
);
"""

_TABLES = {                       # table -> key column
    "prices": "symbol",
    "yields": "series",
    "benchmarks": "series",
}
_VALUE = {"prices": "close", "yields": "yield_ann", "benchmarks": "value"}


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    return conn


# ---------------------------------------------------------------------------
# writes
# ---------------------------------------------------------------------------
def replace_series(conn, table: str, key: str, source: str,
                   rows: list[tuple[str, float]]) -> int:
    """Replace one whole series. rows = [(month_key, value), ...]."""
    kcol, vcol = _TABLES[table], _VALUE[table]
    with conn:
        conn.execute(f"DELETE FROM {table} WHERE {kcol}=?", (key,))
        conn.executemany(
            f"INSERT INTO {table} ({kcol}, date, {vcol}, source) VALUES (?,?,?,?)",
            [(key, d, v, source) for d, v in rows],
        )
    return len(rows)


def log_fetch(conn, source, symbol, fetched_at, rows, note=""):
    with conn:
        conn.execute(
            "INSERT INTO fetch_log VALUES (?,?,?,?,?)",
            (source, symbol, fetched_at, rows, note),
        )


# ---------------------------------------------------------------------------
# reads
# ---------------------------------------------------------------------------
def load_series(conn, table: str, key: str) -> list[tuple[str, float]]:
    kcol, vcol = _TABLES[table], _VALUE[table]
    return conn.execute(
        f"SELECT date, {vcol} FROM {table} WHERE {kcol}=? ORDER BY date", (key,)
    ).fetchall()


def load_prices(conn, symbol):
    return load_series(conn, "prices", symbol)


def load_yields(conn, series):
    return load_series(conn, "yields", series)


def load_benchmark(conn, series):
    return load_series(conn, "benchmarks", series)


def load_rates(conn) -> dict[str, float]:
    """3-month T-bill, {month: annualised percent}: the collateral/funding rate."""
    return dict(load_yields(conn, "3Mo"))


def sources(conn, table: str) -> dict[str, set[str]]:
    """{key: {source, ...}} -- more than one source per key is a red flag."""
    kcol = _TABLES[table]
    out: dict[str, set[str]] = {}
    for k, s in conn.execute(f"SELECT DISTINCT {kcol}, source FROM {table}"):
        out.setdefault(k, set()).add(s)
    return out


def coverage(conn, table: str) -> list[tuple[str, str, str, int]]:
    """(key, first_month, last_month, n) per series."""
    kcol = _TABLES[table]
    return conn.execute(
        f"SELECT {kcol}, MIN(date), MAX(date), COUNT(*) FROM {table}"
        f" GROUP BY {kcol} ORDER BY {kcol}"
    ).fetchall()
