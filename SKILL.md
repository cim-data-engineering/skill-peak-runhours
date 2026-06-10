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

**Bulk pull.** `execute_graphql_query(platform.history, args: {fav_ids: [...], start, end, end_exclusive: true}, fields: ["fav_id","ts","data"])`. Fetch all three fields — payload size is irrelevant once offloaded, and real `ts` + `fav_id` let the script derive the grid itself (no index math, no DST bugs, no grid probe). Above ~12 points, chunk into ~10–12 points per call — the MCP gateway enforces a 30 s timeout and a max response size (11 points × 7 days ≈ 528 KB is known-good). On a transport error (5xx/timeout), halve the chunk and retry.

**Response shape.** Results too large for context are stored to a file (e.g. `/mnt/user-data/tool_results/<id>.json`) with only the path returned — the raw samples never enter context. Small results come back inline; process them without echoing rows. The offloaded file is the MCP text-block wrapper, not bare results — unwrap with:

`rows = json.loads(json.load(open(path))[0]["text"])["results"]`

If the structure differs, inspect key names only — never print elements.

**One script.** Hard hygiene rule: never print raw rows or "sample" elements — one careless print puts the entire blob in context. Inspect only derived files, with `wc -l` / `head`.

- Group rows by `fav_id` and local day; expect 96 rows/day on the 15-min grid (derive the actual cadence from `ts` deltas if different). Flag short days and exclude partial days from averages.
- ON/OFF convention: value set `{0,1}` → 1=ON; `{1,2}` → 2=ON; anything else → treat max as ON and note the values seen.
- Per equipment: weekday avg daily run-h (Σ ÷ 5, zero-run days included), weekend avg (Σ ÷ 2), avg start / stop over days the unit ran (stop = end of last ON interval), OOH = ON time outside working hours (all weekend ON time is OOH when weekends are unoccupied).
- Write a per-day CSV (`name,date,run_h,first_on,last_off,ooh_h`) to disk for reuse, then render the visual from it.

**The visual** — what it must communicate; styling otherwise at your discretion:

- Rows = equipment, grouped by type; X axis = 24 h.
- Bar = average weekday start→stop; the portion outside working hours must be visually distinct from the portion inside, and the working-hours window demarcated on the chart.
- Each row labelled with start–stop and run hours; never-ran equipment keeps its row, labelled "no runtime this week".
- A short legend and an accessible title.
- Generate it inside the script and pass it to `show_widget` once — never `cat`/paste it into the conversation a second time.

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
<script>                 (parse offloaded file(s) → stats → per-day CSV → visual)
show_widget              (the visual, once)
```
