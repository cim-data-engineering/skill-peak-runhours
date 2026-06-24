#!/usr/bin/env python3
"""Render a PEAK run-hours aggregate JSON into a standalone SVG Gantt.

Usage: python3 render_runhours.py <agg.json> <out.svg>

The aggregate JSON (see SKILL.md "Render aggregate schema") is the renderer's
only input. Every layout / colour / font value lives in a named constant below
so the whole look can be restyled in one place. The encodings themselves are the
locked render contract documented in SKILL.md and must not drift.
"""
import json
import sys

# ---- Colours -----------------------------------------------------------------
C_IN_HOURS     = "#0e7490"   # running inside working hours
C_OUT_OF_HOURS = "#e08a1e"   # running outside working hours (orange)
C_WH_BAND      = "#eaf0f4"   # working-hours shaded band
C_GRIDLINE     = "#dfe5ea"   # vertical hour gridlines
C_TEXT         = "#1f2933"   # body text
C_MUTED        = "#8a97a3"   # idle rows, ticks, subtitle
C_GROUP_HEADER = "#3b4754"   # group header label
C_BG           = "#ffffff"   # page background

# ---- Typography --------------------------------------------------------------
FONT_STACK = "Inter, Segoe UI, Helvetica, Arial, sans-serif"
FS_TITLE    = 18
FS_SUBTITLE = 12
FS_GROUP    = 13
FS_ROW      = 12
FS_ANNOT    = 11
FS_TICK     = 10
FS_LEGEND   = 11

# ---- Layout ------------------------------------------------------------------
WIDTH           = 1024
LEFT_GUTTER     = 150   # equipment-label column on the left
RIGHT_COL       = 210   # annotation column on the right
TOP_MARGIN      = 96    # title + subtitle + axis ticks above the first group
BOTTOM_MARGIN   = 24
ROW_H           = 21
GROUP_HEADER_H  = 24
INTER_GROUP_GAP = 12
BAR_H           = 11
CORNER_R        = 2
LABEL_PAD       = 10    # gap between left label and plot
ANNOT_PAD       = 12    # gap between plot and right annotation
PAGE_PAD        = 16    # left edge padding for title / headers / legend
LEGEND_MARGIN   = 22    # gap between last group block and the legend
LEGEND_SWATCH   = 14
LEGEND_H        = 18

# ---- Axis --------------------------------------------------------------------
DAY_MIN        = 1440
TICK_EVERY_MIN = 240    # hour-tick interval (default every 4h)

PLOT_X0 = LEFT_GUTTER
PLOT_X1 = WIDTH - RIGHT_COL
PLOT_W  = PLOT_X1 - PLOT_X0


# ---- Helpers -----------------------------------------------------------------
def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def x_of(minute):
    m = max(0, min(DAY_MIN, minute))
    return PLOT_X0 + (m / DAY_MIN) * PLOT_W


def hhmm(minute):
    minute = int(round(minute))
    return f"{minute // 60:02d}:{minute % 60:02d}"


def g(v):
    return f"{float(v):g}"


def baseline(cy, fs):
    """Baseline y to vertically centre text of size fs about cy."""
    return cy + fs * 0.35


def split_seg(s, e, whs, whe):
    """Split an ON segment [s,e] at the working-hours band edges.

    Returns up to three (start, end, colour) pieces: the portion before whs and
    the portion after whe in the out-of-hours colour, the portion within in the
    in-hours colour. Disjoint and ordered for whs <= whe.
    """
    s = max(0, min(DAY_MIN, s))
    e = max(0, min(DAY_MIN, e))
    pieces = []
    if e <= s:
        return pieces
    if s < whs:                                  # before the band
        pieces.append((s, min(e, whs), C_OUT_OF_HOURS))
    a, b = max(s, whs), min(e, whe)
    if b > a:                                    # within the band
        pieces.append((a, b, C_IN_HOURS))
    if e > whe:                                  # after the band
        pieces.append((max(s, whe), e, C_OUT_OF_HOURS))
    return pieces


def annotation(e):
    txt = f'{e["lbl"]} · {g(e["wd_run"])}h'
    if float(e.get("wd_ooh", 0)) >= 0.1:
        txt += f'  OOH {g(e["wd_ooh"])}h'
    if float(e.get("we_run", 0)) >= 0.1:
        txt += f'  +wknd {g(e["we_run"])}h'
    return txt


# ---- Render ------------------------------------------------------------------
def render(agg):
    whs, whe = agg["wh_start_min"], agg["wh_end_min"]

    # Group equipment by type, ordered by type_order (stragglers appended).
    by_type = {}
    for e in agg["equipment"]:
        by_type.setdefault(e["type"], []).append(e)
    order = list(agg["type_order"]) + [t for t in by_type if t not in agg["type_order"]]
    groups = [(t, by_type[t]) for t in order if by_type.get(t)]

    # Pre-compute the vertical layout so total height is content-derived.
    y = TOP_MARGIN
    layout = []   # (type, header_y, [(equip, row_y), ...], band_top, band_bottom)
    for t, eqs in groups:
        header_y = y
        y += GROUP_HEADER_H
        band_top = y
        rows = []
        for e in eqs:
            rows.append((e, y))
            y += ROW_H
        layout.append((t, header_y, rows, band_top, y))
        y += INTER_GROUP_GAP
    plot_bottom = (y - INTER_GROUP_GAP) if layout else TOP_MARGIN
    legend_y = plot_bottom + LEGEND_MARGIN
    total_h = legend_y + LEGEND_H + BOTTOM_MARGIN

    grid_top, grid_bottom = TOP_MARGIN, plot_bottom
    p = []

    # 1. Background.
    p.append(f'<rect x="0" y="0" width="{WIDTH}" height="{total_h:.0f}" fill="{C_BG}"/>')

    # 2. Working-hours shaded bands (behind rows), per group.
    bx, bw = x_of(whs), x_of(whe) - x_of(whs)
    if bw > 0:
        for _, _, _, band_top, band_bottom in layout:
            p.append(f'<rect x="{bx:.1f}" y="{band_top}" width="{bw:.1f}" '
                     f'height="{band_bottom - band_top}" fill="{C_WH_BAND}"/>')

    # 3. Vertical gridlines + hour-tick labels.
    tick = 0
    while tick <= DAY_MIN:
        gx = x_of(tick)
        p.append(f'<line x1="{gx:.1f}" y1="{grid_top}" x2="{gx:.1f}" y2="{grid_bottom}" '
                 f'stroke="{C_GRIDLINE}" stroke-width="1"/>')
        p.append(f'<text x="{gx:.1f}" y="{TOP_MARGIN - 8}" font-size="{FS_TICK}" '
                 f'fill="{C_MUTED}" text-anchor="middle">{hhmm(tick)}</text>')
        tick += TICK_EVERY_MIN

    # 4. Title + subtitle.
    title = f'{agg["site_name"]} - average weekday equipment run hours'
    subtitle = (f'{agg["window_label"]}. Working hours {hhmm(whs)}-{hhmm(whe)} '
                f'(shaded). Orange = running out of hours.')
    p.append(f'<text x="{PAGE_PAD}" y="32" font-size="{FS_TITLE}" font-weight="600" '
             f'fill="{C_TEXT}">{esc(title)}</text>')
    p.append(f'<text x="{PAGE_PAD}" y="52" font-size="{FS_SUBTITLE}" '
             f'fill="{C_MUTED}">{esc(subtitle)}</text>')

    # 5. Group headers, rows (bars or idle text), annotations.
    for t, header_y, rows, _, _ in layout:
        p.append(f'<text x="{PAGE_PAD}" y="{header_y + GROUP_HEADER_H - 7}" '
                 f'font-size="{FS_GROUP}" font-weight="600" '
                 f'fill="{C_GROUP_HEADER}">{esc(t)}</text>')
        for e, row_y in rows:
            cy = row_y + ROW_H / 2
            tb = baseline(cy, FS_ROW)
            p.append(f'<text x="{LEFT_GUTTER - LABEL_PAD}" y="{tb:.1f}" '
                     f'font-size="{FS_ROW}" fill="{C_TEXT}" '
                     f'text-anchor="end">{esc(e["name"])}</text>')
            if e.get("ran"):
                bar_y = row_y + (ROW_H - BAR_H) / 2
                for s, end in e["segs"]:
                    for x0m, x1m, colour in split_seg(s, end, whs, whe):
                        rx, rw = x_of(x0m), x_of(x1m) - x_of(x0m)
                        if rw <= 0:
                            continue
                        p.append(f'<rect x="{rx:.1f}" y="{bar_y:.1f}" width="{rw:.1f}" '
                                 f'height="{BAR_H}" rx="{CORNER_R}" ry="{CORNER_R}" '
                                 f'fill="{colour}"/>')
                p.append(f'<text x="{PLOT_X1 + ANNOT_PAD}" y="{tb:.1f}" '
                         f'font-size="{FS_ANNOT}" fill="{C_TEXT}">{esc(annotation(e))}</text>')
            else:
                p.append(f'<text x="{PLOT_X0 + LABEL_PAD}" y="{tb:.1f}" '
                         f'font-size="{FS_ROW}" font-style="italic" fill="{C_MUTED}">'
                         f'{esc(e.get("lbl", "no runtime this week"))}</text>')

    # 6. Legend, below the last group block (never overlaps rows).
    items = [(C_IN_HOURS, "Running in working hours"),
             (C_OUT_OF_HOURS, "Running out of hours"),
             (C_WH_BAND, "Working hours")]
    lx = PAGE_PAD
    for colour, label in items:
        p.append(f'<rect x="{lx}" y="{legend_y}" width="{LEGEND_SWATCH}" '
                 f'height="{LEGEND_SWATCH}" rx="{CORNER_R}" ry="{CORNER_R}" '
                 f'fill="{colour}" stroke="{C_GRIDLINE}" stroke-width="1"/>')
        ty = baseline(legend_y + LEGEND_SWATCH / 2, FS_LEGEND)
        p.append(f'<text x="{lx + LEGEND_SWATCH + 6}" y="{ty:.1f}" '
                 f'font-size="{FS_LEGEND}" fill="{C_TEXT}">{esc(label)}</text>')
        lx += LEGEND_SWATCH + 6 + len(label) * FS_LEGEND * 0.55 + 28

    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" '
           f'height="{total_h:.0f}" viewBox="0 0 {WIDTH} {total_h:.0f}" '
           f'font-family="{FONT_STACK}">\n' + "\n".join(p) + "\n</svg>\n")
    return svg


def main(argv):
    if len(argv) != 3:
        sys.exit("usage: python3 render_runhours.py <agg.json> <out.svg>")
    with open(argv[1]) as fh:
        agg = json.load(fh)
    with open(argv[2], "w") as fh:
        fh.write(render(agg))


if __name__ == "__main__":
    main(sys.argv)
