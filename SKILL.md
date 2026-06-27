---
name: peak-runhours
description: Average weekly run-hours visualisation for central plant and AHU equipment at a single PEAK site — a Gantt-style visual of average daily start/stop times against building working hours, with out-of-hours running visible at a glance. Only invoke when the user explicitly runs the /peak-runhours slash command. Do not auto-trigger on related keywords, topics, or PEAK-adjacent questions. Once invoked, remain active for the rest of the session for follow-ups (other sites, equipment subsets, re-runs) without requiring re-invocation.
---

# PEAK Run Hours

## Purpose

Show a building operator **when equipment actually runs versus when the building is occupied** at one PEAK site, averaged over the past full week. One deliverable: a Gantt-style visual — rows are equipment, bars span average daily start→stop, working hours overlaid so out-of-hours running is visible at a glance.

Pipeline, optimised for speed and minimal context: resolve site → one favourites call → bulk history pull saved to disk → one script computes the stats and renders the visual.

## Scope rules

- Resolve exactly **one site**: `search_sites(site_name=X, include_working_hours: true)`. Top score ≥ 0.9 → take it; otherwise show the top 3 candidates and ask. Keep the returned `timezone` and per-day `working_hours`. Degenerate working hours (all `00:00`, or every day disabled) → ask the user what occupancy hours to assess against.
- **Equipment scope**: if the user named one, use it; otherwise default to central plant + AHU (type codes `CH, CT, HWB, PCHWP, CWP, SCHW, PHWP, SHW, AHU`) and state the assumption in the output rather than asking.
- **VAV is a hard exclusion** — point counts are enormous and box-level status is not a run-hours signal. Offer the AHU serving the zone instead. Treat FCU the same unless the user insists.
- **Window**: the last 7 complete local days, yesterday backwards. Today is always excluded — a partial day drags average stop times earlier. Never day-sample (lead/lag plant and optimum-start AHUs make any single day wrong; validated empirically). Explicit user overrides are fine.

## Step 1 — Site and window

Compute the UTC fetch window from the site timezone: local midnight expressed in UTC (e.g. Brisbane UTC+10 → local day D = `(D-1)T14:00Z → (D)T14:00Z`). Note the weekday/weekend split and the working-hours window.

## Step 2 — Status points (one call)

Look up tier-1 status `metadata_id`s for the scoped type codes in `references/ooh_status_metadata_reference.md` (`1_status` rows), then:

`execute_graphql_query(platform.favourites, args: {site_id, metadata_ids: [...], is_active: true, limit: 200}, fields: ["fav_id","metadata_id","equipment.name","equipment.metadata_type.type"])`

- A type that returns nothing → retry it with its `2_enable_command` ids, then `3_analog_proxy` (treat analog > 5% of observed max as ON). Flag fallback rows; equipment with no point in any tier gets one footnote line.
- Dedupe physical units: same plant item with two favourites (e.g. `CH-1` / `CH-1-HLI`) → keep the non-HLI one.
- No equipment listing, no cache file, no scope gate — whatever this call returns is the point list. Large lists are handled by chunking the pull, not by asking.

## Step 3 — Pull to disk, script, visualise

**Bulk pull.** `execute_graphql_query(platform.history, args: {fav_ids: [...], start, end, end_exclusive: true}, fields: ["fav_id","ts","data"])`. Fetch all three fields — payload size is irrelevant once offloaded, and real `ts` + `fav_id` let the script derive the grid itself (no index math, no DST bugs, no grid probe). Chunk only to keep each call under ~2 MB / ~25 k rows — measured against the live gateway: **all status points × 7 days fits one call** (43 points × 7 days ≈ 29 k rows ≈ 2 MB succeeded). The binding limit is payload **size, not the 30 s timeout** (large pulls returned in ~15 s), and oversized responses **offload to a file gracefully** rather than erroring (the ~45–95 KB inline→file boundary is a client token cap, not a failure). Beyond ~2 MB the gateway hard-fails with a Cloudflare **502** (`origin_bad_gateway`, retryable, `retry_after 60`) — on a 502/5xx, halve the fav_id batch or window and retry.

**Response shape.** Results too large for context are stored to a file (e.g. `/mnt/user-data/tool_results/<id>.json`) with only the path returned — the raw samples never enter context. Small results come back inline; process them without echoing rows. **The offloaded payload's shape varies** — it may be the MCP text-block wrapper (`[{"text": "<json>"}]`), a bare `{"results": [...]}` dict, or a bare list — so unwrap defensively rather than assuming one shape:

```python
obj = json.load(open(path))
if isinstance(obj, list) and obj and isinstance(obj[0], dict) and "text" in obj[0]:
    obj = json.loads(obj[0]["text"])          # text-block wrapper
rows = obj.get("results", obj) if isinstance(obj, dict) else obj
```

**The tool-results dir is shared across concurrent sessions**, so an offloaded file may carry rows for fav_ids that aren't yours, and one chunk's rows may be split across files. Always filter to your own fav_id set and de-dup before computing — never trust filenames to map to your chunks:

```python
want = set(fav_ids)
rows = [r for r in rows if r["fav_id"] in want]
rows = list({(r["fav_id"], r["ts"]): r for r in rows}.values())   # de-dup across files
```

If the structure still differs, inspect key names only — never print elements.

**One script.** Hard hygiene rule: never print raw rows or "sample" elements — one careless print puts the entire blob in context. Inspect only derived files, with `wc -l` / `head`.

- Group rows by `fav_id` and local day; expect 96 rows/day on the 15-min grid (derive the actual cadence from `ts` deltas if different). Flag short days and exclude partial days from averages.
- ON/OFF convention: value set `{0,1}` → 1=ON; `{1,2}` → 2=ON; anything else → treat max as ON and note the values seen.
- Per equipment: weekday avg daily run-h (Σ ÷ 5, zero-run days included), weekend avg (Σ ÷ 2), avg start / stop over days the unit ran (stop = end of last ON interval), OOH = ON time outside working hours (all weekend ON time is OOH when weekends are unoccupied).
- **Typical-ON envelope (`segs`).** Over the weekday days in the window, mark each 15-min slot ON per the ON/OFF rule above; a slot is *typical-ON* if it was ON on a majority (≥ 50%) of those weekday days. `segs` is the list of contiguous typical-ON slot runs as `[start_min, end_min]`, minutes from local midnight within `[0,1440]`. This — not a naive average start→stop — is what the bar draws, and it is the one field the renderer needs that the per-day CSV cannot reconstruct (correct for across-midnight, 24/7, and cycling units).
- The script writes **two artifacts** to disk: the per-day CSV (`name,date,run_h,first_on,last_off,ooh_h`) for reuse, and a **render-aggregate JSON** matching the schema in the appendix (per-equipment stats plus the `segs` envelope). The bundled renderer consumes the aggregate JSON, not the CSV.

**The visual.** Default renderer is `scripts/render_runhours.py`: the script emits the render-aggregate JSON, then call `python3 scripts/render_runhours.py <agg.json> <out.svg>`, then `show_widget` once. Don't re-invent the chart each run.

*Locked encodings* — the render contract, do not deviate:

- Horizontal Gantt, one row per equipment, grouped by `type` in `type_order`, with a group header per group.
- Fixed 00:00→24:00 x-axis at the same scale on every row; hour ticks at a regular interval (default 4 h) labelled `HH:00`.
- Behind each group's rows, a shaded band spanning `[wh_start_min, wh_end_min]`; light vertical gridlines at the tick positions.
- For each unit that ran: every seg `[s,e]` (clamped to `[0,1440]`) split at `wh_start_min` and `wh_end_min` into up to three rounded rects — the portions before/after the band in the out-of-hours colour, the portion within in the in-hours colour. A 24/7 unit (`segs [[0,1440]]`) thus renders out/in/out automatically; across-midnight and cycling units render the bands their envelope produced.
- Right-hand per-row annotation: `{lbl} · {wd_run:g}h`, then ` OOH {wd_ooh:g}h` only if `wd_ooh ≥ 0.1`, then ` +wknd {we_run:g}h` only if `we_run ≥ 0.1`.
- `ran=false` keeps its row, drawn as italic muted "no runtime this week" near the start of the plot (no bar).
- Title `{site_name} - average weekday equipment run hours`; one-line subtitle giving the window and "Working hours HH:MM-HH:MM (shaded). Orange = running out of hours."
- Three-item legend (in-hours, out-of-hours, working-hours band), placed below the last group block with a clear margin — total SVG height is computed from the content so the legend never overlaps rows.

*Default style tokens* — shipped defaults; substitution allowed only if internally consistent within a single run: in-hours `#0e7490`, out-of-hours `#e08a1e`, working-hours band `#eaf0f4`, gridlines `#dfe5ea`, body `#1f2933`, muted `#8a97a3`, group header `#3b4754`; font `Inter, Segoe UI, Helvetica, Arial, sans-serif`. Layout: width 1024, left gutter 150, right column 210, top margin 96, row 21, group header 24, inter-group gap 12, bar ≈ 11, corner ≈ 2.

*Free to vary*: exact hex values, font, exact row height / gutters / overall width, tick interval, and title/label wording.

*Escape hatch*: the bundled renderer is the default for the standard portfolio Gantt. Single-equipment timelines, weekend-only views, and other variants may diverge from it (a hand-rolled fallback should still target the aggregate schema below, so it lands in the same family).

Never `cat`/paste the SVG into the conversation; one render via `show_widget`.

Close with one-line anomaly flags only (zero runtime, weekend OOH, pre-dawn starts, run-on past close, heavy cycling). No essay, no table, no workbook.

## Discipline

- Never re-discover schema (`list_graphql_queries` etc.) — the two queries needed are written above.
- Never echo raw samples into context; only derived outputs are read back.
- Follow-ups (weekend view, one-equipment drilldown, unit conversions) re-script from the raw file / derived CSV already on disk — no re-pull for the same window.
- If a large result unexpectedly arrives inline rather than as a file path, don't keep pulling into context — reduce the chunk size, and warn the user if it persists.

## Tool sequence

```
search_sites             (include_working_hours: true)
execute_graphql_query    (platform.favourites — one call)
execute_graphql_query    (platform.history bulk → disk, chunked above ~12 points)
<script>                 (parse offloaded file(s) → stats → per-day CSV + render-aggregate JSON)
scripts/render_runhours.py <agg.json> <out.svg>   (aggregate JSON → standalone SVG)
show_widget              (the visual, once)
```

## Appendix — render aggregate schema

The script emits one JSON object; `scripts/render_runhours.py` is its only consumer. A hand-rolled fallback (renderer missing, or a variant) should target the same shape so it stays in the family.

```json
{
  "site_name": "str",
  "window_label": "str",          // e.g. "16-22 Jun 2026"
  "wh_start_min": 480,             // weekday working-hours band start, minutes from local midnight
  "wh_end_min": 1080,              // band end
  "type_order": ["CH", "AHU"],     // group order, top to bottom
  "equipment": [
    {
      "name": "str", "type": "str",
      "wd_run": 0.0,               // avg weekday daily run hours
      "we_run": 0.0,               // avg weekend daily run hours
      "wd_ooh": 0.0,               // avg weekday daily out-of-hours run hours
      "segs": [[0, 1440]],         // typical-ON envelope, minutes from midnight, within [0,1440]
      "lbl": "str",                // "HH:MM-HH:MM" avg weekday start-stop, or "no runtime this week"
      "ran": true
    }
  ]
}
```
