"""
Static HTML report.

Charts are hand-written inline SVG. There is no plotting library here, for the
same reason there is no pandas: the project is stdlib-only by design. That turns
out to be a feature -- the report is a single self-contained .html file with no
CDN calls, no build step and no dependencies, which anyone can open or email.

Visual conventions follow a design-system-agnostic method:
  - Categorical hues are taken IN FIXED ORDER from the reference palette and are
    never cycled or reassigned by rank, so a series keeps its colour when the
    chart changes.
  - Only the first three categorical slots are used. That subset is documented as
    clearing the colour-vision-deficiency separation gates on all pairs in both
    light and dark mode, so no palette re-validation was required.
  - Diverging encoding (blue<->red with a neutral grey midpoint) is used ONLY where
    the value has a genuine polarity, i.e. positive vs negative Sharpe.
  - One y-axis per plot, always. Never two scales on one chart: the alignment
    between them is arbitrary and invents a correlation the data does not contain.
  - Text always wears ink tokens, never a series colour; the coloured mark beside
    a label carries the identity.
  - Every chart has a table-view twin, so no value is reachable only by hovering.
  - Dark mode is a selected set of steps for the dark surface, not an inverted
    copy of the light one.
"""

import datetime as dt
import html
import math

import config as C
from mf import metrics, model, panel as P, store

# --- palette: reference instance, first three categorical slots only ---------
LIGHT = {
    "surface": "#fcfcfb", "plane": "#f9f9f7",
    "ink": "#0b0b0b", "ink2": "#52514e", "muted": "#898781",
    "grid": "#e1e0d9", "axis": "#c3c2b7", "border": "rgba(11,11,11,0.10)",
    "s1": "#2a78d6", "s2": "#eb6834", "s3": "#1baf7a", "s4": "#8b5cf6",
    "pos": "#2a78d6", "neg": "#e34948", "mid": "#f0efec",
}
DARK = {
    "surface": "#1a1a19", "plane": "#0d0d0d",
    "ink": "#ffffff", "ink2": "#c3c2b7", "muted": "#898781",
    "grid": "#2c2c2a", "axis": "#383835", "border": "rgba(255,255,255,0.10)",
    "s1": "#3987e5", "s2": "#d95926", "s3": "#199e70", "s4": "#a78bfa",
    "pos": "#3987e5", "neg": "#e66767", "mid": "#383835",
}


def esc(s):
    return html.escape(str(s), quote=True)


def nice_ticks(lo: float, hi: float, target: int = 5):
    """Round tick values covering [lo, hi]."""
    if hi <= lo:
        hi = lo + 1.0
    raw = (hi - lo) / max(1, target)
    mag = 10 ** math.floor(math.log10(raw)) if raw > 0 else 1
    for mult in (1, 2, 2.5, 5, 10):
        step = mag * mult
        if step >= raw:
            break
    start = math.floor(lo / step) * step
    ticks, v = [], start
    while v <= hi + step * 1e-9:
        ticks.append(round(v, 10))
        v += step
    return ticks


def fmt_x(v, dp=2):
    return "n/a" if v is None or (isinstance(v, float) and math.isnan(v)) else f"{v:.{dp}f}"


def fmt_p(v, dp=1):
    return "n/a" if v is None or (isinstance(v, float) and math.isnan(v)) else f"{v*100:.{dp}f}%"


# ---------------------------------------------------------------------------
# line chart
# ---------------------------------------------------------------------------
def line_chart(chart_id, months, series, y_label, note="", height=330,
               width=900, log=False):
    """Multi-series line chart. `series` = [(name, slot, [values]), ...].

    Marks are 2px lines with no point markers (300 monthly points would be
    unreadable); the hover layer supplies per-point values, and the endpoint of
    each series is direct-labelled so identity never rests on colour alone.
    """
    ml, mr, mt, mb = 66, 118, 18, 46
    pw, ph = width - ml - mr, height - mt - mb

    vals = [v for _, _, ys in series for v in ys if v is not None]
    lo, hi = min(vals), max(vals)
    if log:
        lo = max(lo, 1e-6)
        tlo, thi = math.log10(lo), math.log10(hi)
        pad = (thi - tlo) * 0.06
        tlo, thi = tlo - pad, thi + pad

        def ypx(v):
            v = max(v, 1e-6)
            return mt + ph - (math.log10(v) - tlo) / (thi - tlo) * ph
        _log_ticks = (0.1, 0.25, 0.5, 1, 2, 4, 8, 16, 32, 64,
                      128, 256, 512, 1024, 2048, 4096)
        tick_vals = [t for t in _log_ticks if lo * 0.9 <= t <= hi * 1.1]
        if len(tick_vals) < 3:
            tick_vals = nice_ticks(lo, hi, 5)
    else:
        pad = (hi - lo) * 0.08 or 0.1
        tlo, thi = lo - pad, hi + pad

        def ypx(v):
            return mt + ph - (v - tlo) / (thi - tlo) * ph
        tick_vals = nice_ticks(tlo, thi, 5)

    n = len(months)

    def xpx(i):
        return ml + (i / max(1, n - 1)) * pw

    parts = [
        f'<svg class="chart" id="{chart_id}" viewBox="0 0 {width} {height}" '
        f'role="img" aria-label="{esc(y_label)}" '
        f'preserveAspectRatio="xMidYMid meet">'
    ]

    # gridlines: solid hairlines, one shade off the surface (never dashed)
    for t in tick_vals:
        y = ypx(t)
        if not (mt - 1 <= y <= mt + ph + 1):
            continue
        parts.append(
            f'<line x1="{ml}" y1="{y:.1f}" x2="{ml+pw}" y2="{y:.1f}" '
            f'class="grid"/>'
        )
        parts.append(
            f'<text x="{ml-10}" y="{y+4:.1f}" class="tick" '
            f'text-anchor="end">{fmt_x(t, 2)}</text>'
        )
    # baseline at 1.0 if in range
    if tlo < 1.0 < thi:
        parts.append(
            f'<line x1="{ml}" y1="{ypx(1.0):.1f}" x2="{ml+pw}" '
            f'y2="{ypx(1.0):.1f}" class="axis"/>'
        )

    # x ticks: one per ~3 years
    year_first = {}
    for i, m in enumerate(months):
        year_first.setdefault(m[:4], i)
    years = sorted(year_first)
    stride = max(1, len(years) // 8)
    for k, yr in enumerate(years):
        if k % stride:
            continue
        i = year_first[yr]
        parts.append(
            f'<line x1="{xpx(i):.1f}" y1="{mt+ph}" x2="{xpx(i):.1f}" '
            f'y2="{mt+ph+5}" class="axis"/>'
        )
        parts.append(
            f'<text x="{xpx(i):.1f}" y="{mt+ph+22}" class="tick" '
            f'text-anchor="middle">{yr}</text>'
        )
    parts.append(
        f'<line x1="{ml}" y1="{mt+ph}" x2="{ml+pw}" y2="{mt+ph}" class="axis"/>'
    )

    # series paths
    end_labels = []      # collect for collision avoidance below
    for name, slot, ys in series:
        d, pen = [], False
        for i, v in enumerate(ys):
            if v is None:
                pen = False
                continue
            d.append(f'{"L" if pen else "M"}{xpx(i):.1f} {ypx(v):.1f}')
            pen = True
        parts.append(
            f'<path d="{" ".join(d)}" fill="none" stroke="var(--{slot})" '
            f'stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>'
        )
        # selective direct label at the endpoint
        last_i = max(i for i, v in enumerate(ys) if v is not None)
        parts.append(
            f'<circle cx="{xpx(last_i):.1f}" cy="{ypx(ys[last_i]):.1f}" r="3.5" '
            f'fill="var(--{slot})" stroke="var(--surface)" stroke-width="2"/>'
        )
        end_labels.append((ypx(ys[last_i]), name, slot, ys[last_i]))

    # End-labels: push apart when they would overlap (min 14px gap).
    end_labels.sort(key=lambda t: t[0])      # sort by y pixel, top-first
    min_gap = 14
    for i in range(1, len(end_labels)):
        if end_labels[i][0] - end_labels[i - 1][0] < min_gap:
            # Push this label down
            end_labels[i] = (end_labels[i - 1][0] + min_gap,) + end_labels[i][1:]
    for raw_y, name, slot, val in end_labels:
        parts.append(
            f'<text x="{ml+pw+8}" y="{raw_y+4:.1f}" class="endlab">'
            f'{esc(name)} {fmt_x(val, 2)}x</text>'
        )

    # hover layer: crosshair + per-series markers, driven by JS
    parts.append(f'<line class="crosshair" id="{chart_id}-cross" x1="0" y1="{mt}" '
                 f'x2="0" y2="{mt+ph}" style="opacity:0"/>')
    for si, (_, slot, _) in enumerate(series):
        parts.append(
            f'<circle class="hovdot" id="{chart_id}-dot{si}" r="4.5" '
            f'fill="var(--{slot})" stroke="var(--surface)" stroke-width="2" '
            f'style="opacity:0"/>'
        )
    parts.append(
        f'<rect id="{chart_id}-hit" x="{ml}" y="{mt}" width="{pw}" height="{ph}" '
        f'fill="transparent" style="cursor:crosshair"/>'
    )
    parts.append("</svg>")

    data = {
        "ml": ml, "mt": mt, "pw": pw, "ph": ph,
        "months": months,
        "series": [[nm, sl, ys] for nm, sl, ys in series],
        # y pixel positions precomputed here so the hover layer never has to
        # re-derive the scale in JS -- two implementations of one scale is how a
        # crosshair ends up pointing at the wrong value.
        "yOf": [[(None if v is None else round(ypx(v), 1)) for v in ys]
                for _, _, ys in series],
        "pct": False,
    }
    return "".join(parts), data


# ---------------------------------------------------------------------------
# area chart (drawdown)
# ---------------------------------------------------------------------------
def table(headers, rows, caption=""):
    h = "".join(f"<th>{esc(x)}</th>" for x in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{esc(c)}</td>" for c in r) + "</tr>" for r in rows
    )
    cap = f"<caption>{esc(caption)}</caption>" if caption else ""
    return f'<table class="data">{cap}<thead><tr>{h}</tr></thead><tbody>{body}</tbody></table>'


def stat(label, value, sub=""):
    return (f'<div class="stat"><div class="slab">{esc(label)}</div>'
            f'<div class="sval">{esc(value)}</div>'
            f'<div class="ssub">{esc(sub)}</div></div>')
