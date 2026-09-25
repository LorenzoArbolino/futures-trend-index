"""
Data sources: download (with a raw on-disk cache) and parse every public series.

Each source has a `fetch_*` that returns the path of the cached raw file and a
`parse_*` that turns that file into {key: {month_key: value}}. The raw file is
never edited, so a parsing bug can be fixed and replayed without re-downloading.

The one property that matters most here is WHAT A MONTHLY VALUE REPRESENTS.

    source            what a monthly value is                  month-end?
    ----------------  ---------------------------------------  ----------
    WB Pink Sheet     average of the month's daily/weekly quotes   NO
    BIS XRU (".E")    end-of-period exchange rate                  yes
    Fed H.15          last daily yield in the month                yes
    Bundesbank BBSIS  last daily Bund yield in the month           yes
    BoE IADB          last daily gilt yield in the month           yes
    Japan MoF         last daily JGB yield in the month            yes
    Bank of Canada    last daily 10Y benchmark yield in the month  yes
    BIS CBPOL         policy rate, end of period                   yes
    AQR datasets      monthly returns, month-end to month-end      yes
    pysystemtrade     last daily futures excess-return index       yes
    Yahoo front month last daily close (splice after 2024-03)      yes

Monthly averages manufacture momentum (Working 1960): the average of month t
already reflects prices through the END of month t, so a return measured
average-to-average overlaps the information in a signal formed at the end of
month t. `config.AVERAGED_SOURCES` lists the sources above that are averages; the
model lags their signals by one extra month (see model.run_backtest).

Parsers for daily sources drop the current, incomplete month: its "month-end" is
really just today's value.

XLSX files are read with the standard library (zipfile + xml), since the project
installs no third-party packages.
"""

import csv
import datetime as dt
import io
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from mf.fetch import _http_get, _month_key


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _cached(dest: Path, url: str, max_age_days: int, force: bool,
            timeout: int = 90) -> Path:
    """Return `dest`, downloading `url` into it if missing, stale or forced."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and not force:
        age = (dt.date.today() - dt.date.fromtimestamp(dest.stat().st_mtime)).days
        if age < max_age_days:
            return dest
    dest.write_bytes(_http_get(url, timeout=timeout))
    return dest


def _current_month() -> str:
    return _month_key(dt.date.today())


def _last_per_month(daily: dict[str, float]) -> dict[str, float]:
    """{iso_date: value} -> {month_key: last value in month}, dropping the
    current incomplete month."""
    best: dict[str, tuple[str, float]] = {}
    for iso, v in daily.items():
        mk = iso[:7] + "-01"
        if mk not in best or iso > best[mk][0]:
            best[mk] = (iso, v)
    cur = _current_month()
    return {m: v for m, (_, v) in best.items() if m != cur}


_XLSX_NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def read_xlsx_sheet(path: Path, sheet_num: int = 1) -> list[list[str]]:
    """Rows of one XLSX sheet as lists of strings (shared strings resolved).

    Cells are positioned by their column reference, so a blank cell in the
    middle of a row does not shift every value after it one column left.
    Excel date serials are returned as-is; callers convert them.
    """
    def col_index(ref: str) -> int:
        n = 0
        for ch in ref:
            if ch.isalpha():
                n = n * 26 + (ord(ch.upper()) - 64)
            else:
                break
        return n - 1

    with zipfile.ZipFile(path) as z:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.findall("s:si", _XLSX_NS):
                shared.append("".join(t.text or "" for t in si.findall(".//s:t", _XLSX_NS)))
        sheet = ET.fromstring(z.read(f"xl/worksheets/sheet{sheet_num}.xml"))
        rows: list[list[str]] = []
        for row_el in sheet.findall(".//s:row", _XLSX_NS):
            cells: dict[int, str] = {}
            for c in row_el.findall("s:c", _XLSX_NS):
                v = c.find("s:v", _XLSX_NS)
                val = v.text if v is not None and v.text is not None else ""
                if c.get("t") == "s" and val:
                    val = shared[int(val)]
                ref = c.get("r")
                idx = col_index(ref) if ref else len(cells)
                cells[idx] = val
            width = max(cells) + 1 if cells else 0
            rows.append([cells.get(i, "") for i in range(width)])
        return rows


def _excel_serial_to_date(s: str) -> dt.date | None:
    try:
        return dt.date(1899, 12, 30) + dt.timedelta(days=int(float(s)))
    except (ValueError, OverflowError):
        return None


# ---------------------------------------------------------------------------
# 1. World Bank Pink Sheet -- monthly AVERAGE commodity prices
# ---------------------------------------------------------------------------
# The file lives at a URL that changes every year, and the old URL keeps serving
# a frozen copy (an earlier version of this project silently stopped at
# 2024-12 for that reason). So we read the current link off the landing page.
_WB_LANDING = "https://www.worldbank.org/en/research/commodity-markets"
_WB_FALLBACK = ("https://thedocs.worldbank.org/en/doc/74e8be41ceb20fa0da750cda2f6b9e4e"
                "-0050012026/related/CMO-Historical-Data-Monthly.xlsx")


def _wb_current_url() -> str:
    try:
        page = _http_get(_WB_LANDING, timeout=60).decode("utf-8", "replace")
        hits = re.findall(r'https://thedocs\.worldbank\.org[^"\']*'
                          r'CMO-Historical-Data-Monthly\.xlsx', page)
        if hits:
            return hits[0]
    except Exception:
        pass
    return _WB_FALLBACK


def fetch_wb_pinksheet(cache: Path, force=False) -> Path:
    dest = cache / "wb_pinksheet_monthly.xlsx"
    if dest.exists() and not force:
        age = (dt.date.today() - dt.date.fromtimestamp(dest.stat().st_mtime)).days
        if age < 30:
            return dest
    return _cached(dest, _wb_current_url(), 0, True, timeout=120)


def parse_wb_pinksheet(path: Path) -> dict[str, dict[str, float]]:
    """"Monthly Prices" sheet -> {commodity column name: {month: price}}.

    Layout: header row with commodity names 4 rows down, a units row, then
    data rows whose first cell is '1960M01'-style.
    """
    rows = read_xlsx_sheet(path, sheet_num=2)
    header_i = next(i for i, r in enumerate(rows[:12])
                    if len(r) > 5 and any(c.strip() == "Gold" for c in r))
    headers = [c.strip() for c in rows[header_i]]
    out: dict[str, dict[str, float]] = {}
    for r in rows[header_i + 2:]:
        m = re.fullmatch(r"(\d{4})M(\d{2})", (r[0] if r else "").strip())
        if not m:
            continue
        mk = f"{m.group(1)}-{m.group(2)}-01"
        for j in range(1, min(len(headers), len(r))):
            try:
                v = float(r[j])
            except ValueError:
                continue
            if headers[j] and v > 0:
                out.setdefault(headers[j], {})[mk] = v
    return out


# ---------------------------------------------------------------------------
# 2. Federal Reserve H.15 -- daily constant-maturity Treasury yields
# ---------------------------------------------------------------------------
_FED_H15_URL = (
    "https://www.federalreserve.gov/datadownload/Output.aspx?rel=H15"
    "&series=bf17364827e38702b42a58cf8eaa3f78&lastobs=&from=01/01/1962"
    "&to=12/31/2099&filetype=csv&label=include&layout=seriescolumn"
)
_H15_IDS = {
    "RIFLGFCM01_N.B": "1Mo", "RIFLGFCM03_N.B": "3Mo", "RIFLGFCM06_N.B": "6Mo",
    "RIFLGFCY01_N.B": "1Y", "RIFLGFCY02_N.B": "2Y", "RIFLGFCY03_N.B": "3Y",
    "RIFLGFCY05_N.B": "5Y", "RIFLGFCY07_N.B": "7Y", "RIFLGFCY10_N.B": "10Y",
    "RIFLGFCY20_N.B": "20Y", "RIFLGFCY30_N.B": "30Y",
}


def fetch_fed_h15(cache: Path, force=False) -> Path:
    return _cached(cache / "fed_h15_yields.csv", _FED_H15_URL, 7, force)


def parse_fed_h15(path: Path) -> dict[str, dict[str, float]]:
    """{maturity label: {month: last daily yield}}. Row 4 holds series IDs;
    daily rows start at row 6; 'ND' marks a non-trading day."""
    rows = list(csv.reader(io.StringIO(path.read_text(encoding="utf-8", errors="replace"))))
    labels = [_H15_IDS.get(s.strip().split("/")[-1], s.strip()) for s in rows[4][1:]]
    daily: dict[str, dict[str, float]] = {lab: {} for lab in labels}
    for r in rows[6:]:
        if not r or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", r[0].strip()):
            continue
        for j, lab in enumerate(labels, 1):
            try:
                daily[lab][r[0].strip()] = float(r[j])
            except (ValueError, IndexError):
                continue
    return {lab: _last_per_month(d) for lab, d in daily.items() if d}


# ---------------------------------------------------------------------------
# 3. BIS bilateral exchange rates -- END-OF-PERIOD, foreign currency per USD
# ---------------------------------------------------------------------------
# The trailing ".E" selects end-of-period. ".A" is the monthly average, which
# is what an earlier version used; that alone inflated the FX sleeve's Sharpe
# from roughly zero to 0.7.
_BIS_XRU_URL = ("https://stats.bis.org/api/v2/data/dataflow/BIS/WS_XRU/1.0/"
                "M..{ccys}.E?format=csv&detail=dataonly")


def fetch_bis_fx(currencies: list[str], cache: Path, force=False) -> Path:
    url = _BIS_XRU_URL.format(ccys="+".join(currencies))
    return _cached(cache / f"bis_xru_eop_{'-'.join(currencies)}.csv", url, 7, force)


def parse_bis_fx(path: Path, areas: dict[str, str]) -> dict[str, dict[str, float]]:
    """{label: {month: USD per unit of foreign currency}}.

    `areas` maps 'REF_AREA-CURRENCY' (e.g. 'JP-JPY') to an output label. BIS
    quotes every one of these as foreign currency per USD (JPY 150, GBP 0.79,
    EUR 0.92), so we INVERT: the result rises when the foreign currency
    strengthens, matching the CME futures quote.
    """
    want = {tuple(k.split("-")): v for k, v in areas.items()}
    out: dict[str, dict[str, float]] = {}
    for row in csv.DictReader(io.StringIO(path.read_text(encoding="utf-8", errors="replace"))):
        key = (row.get("REF_AREA", "").strip(), row.get("CURRENCY", "").strip())
        if key not in want or row.get("COLLECTION", "E").strip() != "E":
            continue
        try:
            v = float(row["OBS_VALUE"])
            tp = row["TIME_PERIOD"].strip()
        except (KeyError, ValueError):
            continue
        if v > 0:
            out.setdefault(want[key], {})[f"{tp[:4]}-{tp[5:7]}-01"] = invert_fx(v)
    return out


def invert_fx(foreign_per_usd: float) -> float:
    """Foreign-per-USD -> USD-per-foreign (price up = foreign currency up)."""
    return 1.0 / foreign_per_usd


# ---------------------------------------------------------------------------
# 4. BIS central bank policy rates (FX carry)
# ---------------------------------------------------------------------------
_BIS_CBPOL_URL = ("https://stats.bis.org/api/v2/data/dataflow/BIS/WS_CBPOL/1.0/"
                  "M.{areas}?format=csv&detail=dataonly")


def fetch_bis_policy_rates(areas: list[str], cache: Path, force=False) -> Path:
    url = _BIS_CBPOL_URL.format(areas="+".join(areas))
    return _cached(cache / f"bis_cbpol_{'-'.join(areas)}.csv", url, 7, force)


def parse_bis_policy_rates(path: Path) -> dict[str, dict[str, float]]:
    """{area code: {month: policy rate, annualised percent}}."""
    out: dict[str, dict[str, float]] = {}
    for row in csv.DictReader(io.StringIO(path.read_text(encoding="utf-8", errors="replace"))):
        try:
            tp, v = row["TIME_PERIOD"].strip(), float(row["OBS_VALUE"])
        except (KeyError, ValueError):
            continue
        out.setdefault(row["REF_AREA"].strip(), {})[f"{tp[:4]}-{tp[5:7]}-01"] = v
    return out


# ---------------------------------------------------------------------------
# 5. Bundesbank -- daily German government yields (par yields, annual coupons)
# ---------------------------------------------------------------------------
_BUBA_URL = ("https://api.statistiken.bundesbank.de/rest/data/BBSIS/"
             "D.I.ZAR.ZI.EUR.S1311.B.A604.R{mat:02d}XX.R.A.A._Z._Z.A?format=csv")


def fetch_bundesbank(maturity: int, cache: Path, force=False) -> Path:
    """Residual maturity in years: 2 (Schatz), 5 (Bobl) or 10 (Bund)."""
    return _cached(cache / f"bundesbank_bund_{maturity}y_daily.csv",
                   _BUBA_URL.format(mat=maturity), 7, force)


def parse_bundesbank(path: Path) -> dict[str, float]:
    """{month: last daily yield}. Metadata rows precede the data; '.' marks a
    non-trading day. The API serves the German layout (semicolons, decimal
    commas); a browser download of the same URL is the English layout (commas,
    decimal points). Both are accepted."""
    daily = {}
    for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        german = ";" in line
        parts = line.split(";" if german else ",")
        if len(parts) < 2 or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", parts[0].strip('" ')):
            continue
        val = parts[1].strip('" ')
        try:
            daily[parts[0].strip('" ')] = float(val.replace(",", ".") if german else val)
        except ValueError:
            continue
    return _last_per_month(daily)


# ---------------------------------------------------------------------------
# 6. Bank of England -- daily 10Y nominal ZERO-COUPON gilt yield
# ---------------------------------------------------------------------------
_BOE_URL = ("https://www.bankofengland.co.uk/boeapps/database/"
            "_iadb-FromShowColumns.asp?csv.x=yes&Datefrom=01/Jan/1970"
            "&Dateto=01/Jan/2030&SeriesCodes=IUDMNZC&UsingCodes=Y&CSVF=TN")


def fetch_boe_gilt(cache: Path, force=False) -> Path:
    return _cached(cache / "boe_gilt_10y_daily.csv", _BOE_URL, 7, force, timeout=60)


def parse_boe_gilt(path: Path) -> dict[str, float]:
    """{month: last daily yield}. Dates look like '04 Jan 1982'."""
    daily = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[1:]:
        parts = line.split(",")
        if len(parts) < 2:
            continue
        try:
            d = dt.datetime.strptime(parts[0].strip(), "%d %b %Y").date()
            daily[d.isoformat()] = float(parts[1])
        except ValueError:
            continue
    return _last_per_month(daily)


# ---------------------------------------------------------------------------
# 6b. Japan Ministry of Finance -- daily JGB yields (par), since 1974
# ---------------------------------------------------------------------------
_MOF_URL = ("https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/"
            "historical/jgbcme_all.csv")


def fetch_mof_jgb(cache: Path, force=False) -> Path:
    return _cached(cache / "mof_jgb_daily.csv", _MOF_URL, 7, force)


def parse_mof_jgb(path: Path, tenor: str = "10Y") -> dict[str, float]:
    """{month: last daily yield} for one tenor. Dates 'YYYY/M/D'; '-' = none."""
    rows = list(csv.reader(io.StringIO(path.read_text(encoding="utf-8", errors="replace"))))
    hdr = next(i for i, r in enumerate(rows) if r and r[0].strip() == "Date")
    col = [c.strip() for c in rows[hdr]].index(tenor)
    daily = {}
    for r in rows[hdr + 1:]:
        try:
            y, m, d = (int(x) for x in r[0].split("/"))
            daily[f"{y:04d}-{m:02d}-{d:02d}"] = float(r[col])
        except (ValueError, IndexError):
            continue
    return _last_per_month(daily)


# ---------------------------------------------------------------------------
# 6c. Bank of Canada -- daily 10Y benchmark bond yield, since 2001
# ---------------------------------------------------------------------------
_BOC_URL = ("https://www.bankofcanada.ca/valet/observations/BD.CDN.10YR.DQ.YLD/csv"
            "?start_date=1900-01-01")


def fetch_boc_10y(cache: Path, force=False) -> Path:
    return _cached(cache / "boc_cgb_10y_daily.csv", _BOC_URL, 7, force)


def parse_boc_10y(path: Path) -> dict[str, float]:
    """{month: last daily yield}. Valet CSV: metadata, then '"date","value"'."""
    daily = {}
    for r in csv.reader(io.StringIO(path.read_text(encoding="utf-8", errors="replace"))):
        if len(r) >= 2 and re.fullmatch(r"\d{4}-\d{2}-\d{2}", r[0].strip()):
            try:
                daily[r[0].strip()] = float(r[1])
            except ValueError:
                continue
    return _last_per_month(daily)


# ---------------------------------------------------------------------------
# 7. AQR datasets (external benchmarks)
# ---------------------------------------------------------------------------
_AQR = "https://www.aqr.com/-/media/AQR/Documents/Insights/Data-Sets/"
_AQR_TSMOM_URL = _AQR + "Time-Series-Momentum-Factors-Monthly.xlsx"


def fetch_aqr_tsmom(cache: Path, force=False) -> Path:
    return _cached(cache / "aqr_tsmom_monthly.xlsx", _AQR_TSMOM_URL, 30, force)


def _parse_aqr_sheet(path: Path, wanted: dict[str, str]) -> dict[str, dict[str, float]]:
    """Generic AQR factor sheet: find the header row containing the wanted
    column names, then read (date, value) rows beneath it. Dates are either
    Excel serials or 'MM/DD/YYYY'."""
    rows = read_xlsx_sheet(path, 1)
    hi = next(i for i, r in enumerate(rows)
              if sum(1 for c in r if c.strip() in wanted) >= 1 and i > 3)
    cols = {j: wanted[c.strip()] for j, c in enumerate(rows[hi]) if c.strip() in wanted}
    out: dict[str, dict[str, float]] = {v: {} for v in cols.values()}
    for r in rows[hi + 1:]:
        if not r or not r[0].strip():
            continue
        raw = r[0].strip()
        d = (dt.datetime.strptime(raw, "%m/%d/%Y").date() if "/" in raw
             else _excel_serial_to_date(raw))
        if d is None:
            continue
        for j, lab in cols.items():
            try:
                out[lab][_month_key(d)] = float(r[j])
            except (ValueError, IndexError):
                continue
    return out


def parse_aqr_tsmom(path: Path) -> dict[str, dict[str, float]]:
    """AQR Time Series Momentum (Moskowitz, Ooi & Pedersen 2012): 12-month
    TSMOM, 1-month hold, volatility-scaled, excess returns, GROSS of costs."""
    return _parse_aqr_sheet(path, {
        "TSMOM": "AQR_TSMOM", "TSMOM^CM": "AQR_TSMOM_CM", "TSMOM^EQ": "AQR_TSMOM_EQ",
        "TSMOM^FI": "AQR_TSMOM_FI", "TSMOM^FX": "AQR_TSMOM_FX",
    })


# ---------------------------------------------------------------------------
# 8. pysystemtrade -- daily back-adjusted commodity futures (to 2024-03)
# ---------------------------------------------------------------------------
# Rob Carver's open-source project ships, per market, a back-adjusted daily
# series (additive "Panama" adjustment, so early prices can be negative) and a
# "multiple prices" file with the price of the contract actually held. The
# exact daily return of the held contract is
#     r_d = (A_d - A_{d-1}) / (A_{d-1} + P_d - A_d)
# where A is the adjusted price and P the held contract's price: A_{d-1} shifted
# into the current contract's terms. It includes the roll yield, so it is a
# true futures excess return. The data stopped updating in 2024; cached copies
# never expire.

def fetch_pst(kind: str, code: str, base: str, cache: Path, force=False) -> Path:
    """kind: 'adjusted_prices_csv' or 'multiple_prices_csv'."""
    return _cached(cache / f"pst_{kind.split('_')[0]}_{code}.csv",
                   f"{base}/{kind}/{code}.csv", 36500, force, timeout=180)


def parse_pst_carry(mult_path: Path) -> dict[str, float]:
    """{iso date: annualised roll yield in %} from the held contract and the
    adjacent "carry" contract: (P_near / P_far - 1) x 12 / months apart.
    Positive = backwardation (a long position earns the roll)."""
    out = {}
    for r in csv.DictReader(io.StringIO(mult_path.read_text(encoding="utf-8", errors="replace"))):
        try:
            p, c = float(r["PRICE"]), float(r["CARRY"])
            pc, cc = r["PRICE_CONTRACT"].strip()[:6], r["CARRY_CONTRACT"].strip()[:6]
        except (KeyError, ValueError):
            continue
        gap = (int(pc[:4]) - int(cc[:4])) * 12 + int(pc[4:6]) - int(cc[4:6])
        if gap == 0 or p <= 0 or c <= 0:
            continue
        near, far = (c, p) if gap > 0 else (p, c)
        out[r["DATETIME"][:10]] = (near / far - 1.0) * 12.0 / abs(gap) * 100.0
    return out


def parse_pst_daily_index(adj_path: Path, mult_path: Path) -> dict[str, float]:
    """{iso date: excess-return index level}, starting at 1.0."""
    def rows(path):
        return csv.DictReader(io.StringIO(path.read_text(encoding="utf-8", errors="replace")))
    adj = {}
    for r in rows(adj_path):
        try:
            adj[r["DATETIME"][:10]] = float(r["price"])
        except (KeyError, ValueError):
            continue
    held = {}
    for r in rows(mult_path):
        try:
            held[r["DATETIME"][:10]] = float(r["PRICE"])
        except (KeyError, ValueError):
            continue
    days = sorted(d for d in adj if d in held)
    idx, level = {}, 1.0
    if days:
        idx[days[0]] = level
    for d0, d1 in zip(days, days[1:]):
        base_level = adj[d0] + (held[d1] - adj[d1])
        if base_level > 0:
            level *= 1.0 + (adj[d1] - adj[d0]) / base_level
        idx[d1] = level
    return idx
