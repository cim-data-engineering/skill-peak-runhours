---
name: peak-runhours
description: Average weekly run-hours and out-of-hours analysis for central plant and AHU equipment at a single PEAK site — summary table, Excel workbook, and a Gantt-style visual of average start/stop times against building working hours. Only invoke when the user explicitly runs the /peak-runhours slash command. Do not auto-trigger on related keywords, topics, or PEAK-adjacent questions. Once invoked, remain active for the rest of the session for follow-ups (other sites, equipment subsets, re-runs) without requiring re-invocation.
---

# PEAK Run Hours

## Purpose

Show a building operator **when equipment actually runs versus when the building is occupied** at one PEAK site, averaged over the past full week. The skill pulls 15-minute status history via the PEAK MCP, computes per-equipment weekday/weekend averages, and renders three deliverables:

1. A markdown summary table (one row per equipment item)
2. An Excel workbook (formula-driven summary + per-day detail sheet)
3. A Gantt-style widget — rows are equipment, bars span average daily start→stop shaded by run status, with the site's working hours overlaid so out-of-hours running is visible at a glance

Column layout, xlsx structure, and Gantt rendering rules are in `references/output_templates.md`. Read it before rendering anything.

## Triggering & scope

Resolve exactly **one site** before anything else. Call `search_sites(site_name=X, include_working_hours: true)`. Top score ≥ 0.9 → take it; otherwise show the top 3 candidates and ask. The same call returns the site's `timezone` and per-day `working_hours` — keep both; every later step depends on them. If working hours come back degenerate (all `00:00`, or every day disabled), ask the user what occupancy hours to assess against instead of guessing.

**Equipment scope — always confirm before pulling data.** Use AskUserQuestion with these options:

- **Central plant + AHU (recommended)** — type codes `CH, CT, HWB, PCHWP, CWP, SCHW, PHWP, SHW` + `AHU`
- **Central plant only**
- **AHU only**
- Other (user supplies type codes)

**VAV is a hard exclusion.** Never run this analysis on VAV boxes, even if the user asks — the point counts are enormous and box-level status is not a meaningful run-hours signal. Say so and offer the AHU serving the zone instead. Treat FCU the same way unless the user insists and the point-count gate (below) passes.

**Analysis window** is the last 7 complete local days (yesterday backwards). Do **not** day-sample — equipment with lead/lag rotation (chillers, pumps) and optimum-start AHUs produce wrong numbers from any single sampled day; this was validated empirically. The user can override the window with an explicit ask (e.g. "last fortnight": run two week-long passes and average).

## Workflow

Track progress with this checklist:

- [ ] Phase 1: site + working hours + timezone resolved, equipment scope confirmed with user
- [ ] Phase 2: status-point map built (one favourite per physical equipment item), point-count gate passed
- [ ] Phase 3: history pulled by worker subagents (data-only fetch, checksummed), per-day CSV returned
- [ ] Phase 4: aggregates computed, table + xlsx + Gantt rendered, anomalies flagged

### Phase 1 — Resolve site and scope

As above. Compute the UTC fetch window from the site timezone: local midnight = `T(00:00 local)` expressed in UTC (e.g. Brisbane UTC+10 → local day D = `(D-1)T14:00Z → (D)T14:00Z`). Note the weekday/weekend split for the 7 days and the working-hours window (e.g. 08:00–18:00 Mon–Fri).

### Phase 2 — Build the status-point map

1. `search_equipment(site_ids: [id], metadata_type_codes: [confirmed codes], limit: 200)`.
2. Look up tier-1 status `metadata_id`s for those types in `references/ooh_status_metadata_reference.csv` (`1_status` rows). Query favourites in **one call**:
   `execute_graphql_query(platform.favourites, args: {site_id, metadata_ids: [...], is_active: true, limit: 200}, fields: ["fav_id","equipment_id","metadata_id","equipment.name","equipment.zone.level.level_name","equipment.metadata_type.type"])`
3. Equipment with no tier-1 status point: fall back to tier `2_enable_command`, then `3_analog_proxy` (treat analog > 5% of observed max as ON). Flag fallback rows in the output. Equipment with no point in any tier (virtual/shared units, relief fans): silently exclude, list them once in a footnote.
4. **Dedupe physical units**: where the same plant item has two favourites (e.g. `CH-1` and `CH-1-HLI`), keep the non-HLI one.
5. **Cache the map**: write `peak_runhours_pointmap_<site_id>.json` (site, timezone, working hours, rows of name/type/level/fav_id/metadata_id) to the outputs folder. On a repeat run for the same site in this session or when the user supplies a previous cache file, skip steps 1–3.

**Point-count gate.** If the final map exceeds **30 points**, stop and use AskUserQuestion before pulling history: offer to (a) scope to a subset of levels, (b) scope to fewer equipment types, or (c) proceed anyway (warn: ~1.5k tokens per point-day; quote the estimate). Never silently launch a 100-point pull.

### Phase 3 — Pull history via worker subagents

**Grid probe first.** One call in the main context: `platform.history` for a single fav, a single local day, fields `["ts","data"]`. Confirm the sampling grid (expect 96 rows/day at 15 min — if different, recompute expected counts and slot width) and confirm the first row lands on local midnight.

Then fan out **general-purpose subagents in parallel, ~6–8 points each**, using the prompt template in `references/worker_prompt.md` verbatim with the placeholders filled. The non-negotiables baked into that template:

- `fields: ["data"]` only — never fetch `ts` (3× token cost). Time is derived from array index on the known grid.
- One call per point for the whole 7-day window; verify `result_count` equals the expected total. On mismatch (missing samples, DST shift days), refetch that point WITH `ts` and say so.
- Run-length encode; RLE lengths must sum to the expected count before any stats are derived.
- MSV convention: value set `{0,1}` → 1=ON; `{1,2}` → 2=ON; anything else → report distinct values and treat max as ON.
- Output is **CSV only**: `name,date,run_h,first_on,last_off,ooh_h` — one line per point per day, no raw data echoes, no prose.

Raw samples must never enter the main (orchestrator) context — only the workers' CSV comes back.

### Phase 4 — Aggregate and render

Per equipment item, from the per-day CSV:

- `Weekday avg daily run hours` = Σ weekday run_h ÷ 5 (zero-run days included)
- `Weekend avg daily run hours` = Σ weekend run_h ÷ 2
- `Weekday avg start` / `avg stop` = mean of first_on / last_off over **days the unit ran** ("stop" = end of last ON interval)
- `Total weekday OOH` / `Total weekend OOH` = Σ ooh_h per bucket, where OOH = ON time outside the site's working hours (all weekend ON time is OOH when weekends are unoccupied)

Render the markdown summary table, build the xlsx (use the **xlsx skill**; layout in `references/output_templates.md` — summary sheet formulas reference the detail sheet via SUMIFS/AVERAGEIFS, then recalc and verify zero errors), and draw the Gantt widget per the same reference.

Close with a short anomaly list only (no essay): zero-runtime points, weekend OOH events, heavy short-cycling, pre-dawn starts, run-on past close. Each gets one line.

## Output discipline

- The summary table, workbook, and Gantt are the product. Keep commentary to the one-line anomaly flags.
- Round run hours to 2 dp in the workbook, 1 dp in the table and Gantt labels; times as `HH:MM`.
- Equipment that never ran: keep the row, em-dash the times, label the Gantt row "no runtime this week".
- State the convention footnote once under the table: stop = end of last ON interval; start/stop averaged over running days; run-hour averages include zero days.

## Token discipline

This skill exists because naive history pulls are expensive (~575k tokens for 24 points). Hold the line:

- Data-only fetch + index math, never `ts` (unless a checksum fails)
- All history reading happens inside workers; orchestrator sees CSV only
- Reuse the cached point map; never re-discover schema (`list_graphql_queries` etc.) — the queries needed are named in this file
- Full week, ~24 points ≈ 150–170k tokens total. Quote ~1.5k × points × 7 days when the gate asks the user about big scopes
- Follow-up drilldowns (one equipment item, one day) reuse the CSV already in context — no re-pull

## Tool sequence summary

```
search_sites                     (Phase 1, include_working_hours: true)
AskUserQuestion                  (Phase 1 equipment scope; Phase 2 gate if >30 points)
search_equipment                 (Phase 2)
execute_graphql_query            (Phase 2 platform.favourites; Phase 3 grid probe platform.history)
Agent → execute_graphql_query    (Phase 3 workers, platform.history, fields ["data"] only)
Skill: xlsx                      (Phase 4 workbook)
show_widget / visualize          (Phase 4 Gantt)
```