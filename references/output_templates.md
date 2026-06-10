# Output templates

## 1. Markdown summary table

One row per equipment item, grouped Central plant first (CH, CT, PCHWP, CWP, then any HWB/SHW/SCHW/PHWP), then AHUs. Columns:

| Type | Name | Level | WD avg run (h) | WE avg run (h) | WD avg start | WD avg stop | WD OOH total (h) | WE OOH total (h) |

- Run hours 1 dp (2 dp only in the workbook). Times `HH:MM`. Never-ran rows: `0` runs, `—` times.
- Footnote under the table (once): *stop = end of last ON interval; start/stop averaged over days the unit ran; run-hour averages include zero-run days. OOH = run time outside <working hours>.*

## 2. Excel workbook (`<site>_avg_run_hours.xlsx`)

Build with the xlsx skill (openpyxl, Arial, recalc + zero formula errors). Two sheets:

**Daily Detail** — one row per equipment per day:
`Equipment Type | Equipment Name | Date | Day | Day Type (WD/WE) | Run Hours | Start (time, hh:mm) | Stop (time) | OOH Hours`

**Run Hours Summary** (first sheet) — title row, scope/convention note row, then the table above with every metric as a formula against Daily Detail (no hardcoded stats):

- WD avg run: `=SUMIFS(Detail!F,name,daytype="WD")/5` — WE: `/2`
- WD avg start/stop: `=IFERROR(AVERAGEIFS(Detail!G,name,"WD"),"—")` (AVERAGEIFS skips blank time cells, giving "days run" semantics; IFERROR covers never-ran rows)
- OOH totals: `SUMIFS`
- Formats: runs `0.00`, times `hh:mm`, header row dark fill + white bold, freeze panes under the header.

## 3. Gantt widget (show_widget, SVG)

Goal: instantly see who runs outside the working-hours window.

Layout (scale paddings with row count; ~22px row pitch):

- X axis = 24h, label ticks at 00/06/12/24 plus emphasised `HH:MM` labels at the working-hours start and end.
- Vertical overlay: shade the two OUTSIDE-working-hours regions with a light gray rect (`c-gray`, opacity ~0.45) full chart height; dashed vertical lines at the working-hours boundaries. The occupied window stays unshaded so bars poking into gray = out of hours.
- Rows grouped by equipment type with a small bold group label row; equipment name right-aligned in a ~125px label gutter.
- Bar per equipment = weekday average start → stop, split into segments:
  - portion inside working hours → `c-teal`
  - portions before start / after end of working hours → `c-coral`
- Duty shading ("shaded by status"): if duty cycle = run_h ÷ span < 0.5, render the bar with `fill-opacity="0.45"` and `stroke-dasharray="3 2"` → reads as intermittent/cycling. Solid = ran continuously across the span.
- Right-aligned label column past the track: `HH:MM–HH:MM · X.Xh`.
- Never-ran rows: no bar, gray label "no runtime this week".
- Legend row at the bottom: teal "in working hours", coral "out of hours run", faded-dashed teal "intermittent (cycling within span)", gray "outside <start>–<end>".
- Weekend-only events (e.g. isolated pump kicks) do not get bars — flag them in the anomaly lines instead.
- SVG must carry `role="img"` with `<title>` and `<desc>`; use ramp classes (`c-teal`, `c-coral`, `c-gray`) and text classes (`t`, `ts`) so dark mode works; sentence case everywhere.

## 4. Point-map cache (`peak_runhours_pointmap_<site_id>.json`)

```json
{
  "site_id": 371, "site_name": "...", "timezone": "Australia/Brisbane",
  "working_hours": {"mon": ["08:00","18:00"], "...": "...", "sat": null, "sun": null},
  "grid_min": 15,
  "points": [
    {"name": "CH-1", "type": "Chiller", "type_code": "CH", "level": "Plantroom",
     "fav_id": 2896955463694, "metadata_id": 9, "convention": "0/1", "tier": "1_status"}
  ],
  "excluded": [{"name": "Common CHWS", "reason": "no status favourite"}]
}
```

Reuse it for any repeat run on the same site; refresh only if the user says equipment changed or a fetch 404s.