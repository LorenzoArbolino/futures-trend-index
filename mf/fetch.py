"""
Shared HTTP helper plus the Yahoo daily fetcher (used for the S&P 500 benchmark).

Hard-won details about the Yahoo chart API:

  (a) A browser User-Agent is mandatory; the default Python one gets HTTP 429.
  (b) Never use range=max: past a size cap Yahoo DOWNSAMPLES silently (monthly
      bars turned quarterly). Always pass explicit period1/period2.
  (c) Yahoo's own monthly bars omit whole months for some symbols, so we fetch
      DAILY and aggregate to month-end ourselves (`aggregate_monthly`).
  (d) The current, incomplete month is always dropped: its "close" is just the
      price right now.

Raw responses are cached under data/cache/ and reused on re-runs.
"""

import datetime as dt
import json
import time
import urllib.error
import urllib.parse
import urllib.request

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}"

def _http_get(url: str, timeout: int = 45, retries: int = 3) -> bytes:
    """GET with a browser UA and exponential backoff.

    Free endpoints fail intermittently; a bare urlopen turns a transient blip
    into a failed run. The backoff is also simply polite.
    """
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
            last = exc
            if attempt < retries - 1:
                time.sleep(1.5 * (2 ** attempt))
    raise RuntimeError(f"GET failed after {retries} attempts: {url}\n  {last}")


def _month_key(d: dt.date) -> str:
    """Canonical month label: first day of the month the observation belongs to.

    Using a fixed day-of-month means two sources that disagree about whether a
    month 'ends' on the 30th or 31st still line up -- the kind of off-by-one that
    otherwise silently drops months when joining series.
    """
    return f"{d.year:04d}-{d.month:02d}-01"


# ---------------------------------------------------------------------------
# Yahoo daily prices
# ---------------------------------------------------------------------------
def fetch_yahoo_daily(symbol: str, start: str, cache_dir, force=False):
    """Daily closes for one symbol as ([(iso_date, close)...], from_cache).

    `start` is 'YYYY-MM-DD'. We request a little before the intended backtest
    start so the first moving-average window is complete rather than truncated.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    safe = symbol.replace("=", "_").replace("^", "idx_")
    cache_file = cache_dir / f"yahoo_daily_{safe}.json"

    if cache_file.exists() and not force:
        age = (dt.date.today() - dt.date.fromtimestamp(cache_file.stat().st_mtime)).days
        if age < 1:
            raw = json.loads(cache_file.read_text(encoding="utf-8"))
            return _parse_yahoo_daily(raw, symbol), True

    p1 = int(dt.datetime.fromisoformat(start).replace(tzinfo=dt.timezone.utc).timestamp())
    p2 = int(time.time())
    url = YAHOO_CHART.format(sym=urllib.parse.quote(symbol, safe="")) + (
        f"?period1={p1}&period2={p2}&interval=1d"
    )
    raw = json.loads(_http_get(url))
    cache_file.write_text(json.dumps(raw), encoding="utf-8")
    return _parse_yahoo_daily(raw, symbol), False


def _parse_yahoo_daily(raw: dict, symbol: str) -> list[tuple[str, float]]:
    chart = raw.get("chart") or {}
    if chart.get("error"):
        raise RuntimeError(f"{symbol}: Yahoo error {chart['error']}")
    results = chart.get("result") or []
    if not results:
        raise RuntimeError(f"{symbol}: no result block in response")
    res = results[0]
    stamps = res.get("timestamp") or []
    quote = (res.get("indicators", {}).get("quote") or [{}])[0]
    closes = quote.get("close") or []
    if not stamps or not closes:
        raise RuntimeError(f"{symbol}: response contained no price series")

    # Dates in the EXCHANGE's timezone, not the machine's: futures bars are
    # stamped around midnight exchange time, so converting with the local
    # timezone would move every bar to the previous day on a US-based machine
    # (and turn the first trading day of a month into last month's close).
    offset = int((res.get("meta") or {}).get("gmtoffset") or 0)
    out = {}
    for ts, close in zip(stamps, closes):
        if close is None:       # gaps are skipped, never forward-filled
            continue
        day = dt.datetime.fromtimestamp(ts + offset, tz=dt.timezone.utc).date()
        out[day.isoformat()] = float(close)
    return sorted(out.items())


# ---------------------------------------------------------------------------
# daily -> month-end aggregation
# ---------------------------------------------------------------------------
def aggregate_monthly(
    daily: list[tuple[str, float]], min_days: int = 5
) -> list[tuple[str, float, int, str]]:
    """Collapse daily closes to one observation per month.

    Returns [(month_key, close, n_days, last_day), ...].

    The monthly close is the LAST daily close in the month, which is the correct
    definition of a month-end price. We also return the number of trading days
    and the actual date of that close so the sanity report can distinguish a
    genuine month-end from a data gap that merely looks like one.

    Months with fewer than `min_days` observations are dropped: with a handful of
    days there is no reliable month-end, and a bad month-end price produces two
    wrong returns (into the month and out of it).

    The current, incomplete month is always dropped -- see (d) in the module
    docstring.
    """
    today = dt.date.today()
    buckets: dict[str, list[tuple[str, float]]] = {}
    for iso, close in daily:
        d = dt.date.fromisoformat(iso)
        if d.year == today.year and d.month == today.month:
            continue
        buckets.setdefault(_month_key(d), []).append((iso, close))

    out = []
    for month, obs in sorted(buckets.items()):
        if len(obs) < min_days:
            continue
        obs.sort()
        last_day, close = obs[-1]
        out.append((month, close, len(obs), last_day))
    return out

