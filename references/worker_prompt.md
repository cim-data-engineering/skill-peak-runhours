# Worker subagent prompt template

Spawn `general-purpose` subagents in parallel, ~6–8 points each. Fill every `{placeholder}` before sending. Do not soften the token rules — they are the point.

---

Token-efficiency data pull via the PEAK MCP. Use ONLY `mcp__<peak-server>__execute_graphql_query` with query_name `platform.history`. No other tools, no schema listing.

Favourites (site {site_id}, equipment status points):
{name}={fav_id}, {name}={fav_id}, ...

For EACH favourite make exactly ONE call:
args: {"fav_id": <id>, "start": "{window_start_utc}", "end": "{window_end_utc}", "end_exclusive": true}

CRITICAL TOKEN RULE: fields: ["data"] ONLY — do NOT fetch ts. Results are strictly time-ordered on a {grid_min}-minute grid starting at the window start (= local midnight at the site, UTC offset {utc_offset}). Derive time from array index: within a day, slot i (0-based, i mod {slots_per_day}) starts at local {grid_min}×(i mod {slots_per_day}) minutes after midnight; day number = i div {slots_per_day}, day 0 = {first_local_date}.

Verify result_count == {expected_total} ({n_days} days × {slots_per_day}). If it differs, refetch that favourite WITH ts, derive stats from timestamps instead, and say so in your reply.

ON/OFF convention: value set {0,1} → ON=1; {1,2} (multi-state) → ON=2; anything else → treat the maximum value as ON and report the distinct values you saw.

METHOD: build a run-length encoding of each response (e.g. 29×0, 48×1, 19×0). CHECK: RLE lengths must sum to {expected_total} — if not, recount before deriving anything. Then per local day:
- run_h = {slot_h} × (ON slots that day)
- first_on = local HH:MM of the first ON slot's start; last_off = HH:MM of (last ON slot index + 1) × {grid_min} min
- ooh_h = {slot_h} × ON slots starting outside working hours {wh_start}–{wh_end}; on {weekend_days} ALL ON slots are ooh

OUTPUT: CSV only, one line per favourite per day:
name,date,run_h,first_on,last_off,ooh_h
(empty first_on/last_off if no run that day). No raw data echoes, no tables, no prose beyond a one-line note per favourite if the value convention was not {0,1} or a refetch happened.

---

## Worked example (validated)

Site 371, Brisbane (UTC+10), 15-min grid, week Wed 2026-06-03 → Tue 2026-06-09:
window_start_utc = 2026-06-02T14:00:00Z, window_end_utc = 2026-06-09T14:00:00Z, expected_total = 672, slots_per_day = 96, slot_h = 0.25, OOH slot test = (i mod 96) < 32 or ≥ 72 for working hours 08:00–18:00.

Observed conventions at that site: binary AHU/chiller status `{0,1}`; MSV cooling-tower/pump status `{1,2}` with 1=OFF, 2=ON.