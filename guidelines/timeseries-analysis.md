---
name: peak-timeseries-analysis
description: >
  How to answer any PEAK time-series question (run hours, averages, trends,
  correlations, anomalies, duty cycles) over the GraphQL MCP without flooding
  context. Mandatory pipeline: select points -> delegate the bulk history fetch
  to a sub-agent that writes Parquet to disk -> grid + aggregate in DuckDB ->
  return only a compact summary (and optional chart). Raw samples never enter
  the orchestrator's context.
---

# PEAK Time-Series Analysis (MCP / GraphQL)

## When this applies

Any question that needs PEAK **history** (the `platform.history` time series) for one
or more points over a window: run hours, on/off duty cycles, averages, min/max, trends,
two-signal correlations, out-of-hours analysis, anomaly spotting. (Run-hours — a Gantt of
average daily start/stop against occupancy hours — is **one specialisation** of this pipeline;
a host may wrap that specific case in its own skill, but this guidance stands alone.)

## The one rule

**Raw history never enters the orchestrator's context.** Every analysis follows the same
shape, and the only thing that crosses back into the conversation is a compact summary:

```
resolve site + window
  -> select points  (one favourites call: fav_ids + collector_id + collection_interval)
  -> DELEGATE to a sub-agent: bulk-fetch history, grid it, write Parquet, return a manifest
  -> DuckDB over the Parquet: aggregate to answer the question
  -> return summary (+ optional chart). Never print or echo rows.
```

A single careless print of a `platform.history` result puts thousands of rows in context
and defeats the whole point. Inspect only derived files (`wc -l`, `head` of a summary CSV).

## Step 1 — Site and window

`search_sites(site_name=X, include_working_hours: true)`. Keep `timezone` and
`working_hours`. Compute the UTC fetch window from the site tz (local midnight in UTC).
Default window depends on the question; exclude today (partial day) unless asked.

## Step 2 — Select points (one favourites call, carry the grid inputs)

Pick `fav_id`s for the question. For status/run-hours, select each equipment's binary status
point — discover it by class (filter `metadata_codes`, or `metadata_name LIKE '%status%'`,
e.g. `Un-SAF/St`, `CH-St`), preferring a true run-status over enable-command / analog-proxy
fallbacks; for sensor/setpoint analysis filter by point class. *(If your host ships a curated
status-`metadata_id` reference, use it — this guidance doesn't require one.)* **In the same
call, also fetch the gridding inputs** so no extra round-trip:

```
execute_graphql_query(platform.favourites,
  args:   {site_id, metadata_codes: ["Un-SAT"], is_active: true, limit: 200},
  fields: ["fav_id", "metadata_id",
           "metadata.code", "metadata.name", "metadata.system",
           "metadata.unit.unit",
           "equipment.name", "equipment.metadata_type.type",
           "equipment.collector_id",
           "equipment.collector.collection_interval"])
```

**Always carry the unit** — `metadata.unit` is a nested `MetadataUnit` object, so subselect
`metadata.unit.unit` (selecting `metadata.unit` bare errors "Subselection required"). Report
the unit, and never aggregate across **mixed units** (e.g. a site with some °F and some °C
points, or kWh vs kVArh) — split by unit first.

**Args vs fields (the most common point-selection error):** `metadata_code(s)` /
`metadata_name` / `metadata_ids` are **filter arguments** only — they do **not** exist as
selectable fields. To *filter*, pass them in `args`; to *read* the point class, subselect
the nested object: `metadata.code`, `metadata.name`, `metadata.system` (NOT
`metadata_code`/`metadata_name`). Use `metadata.system` to spot BACER virtual points (see
the BACER caveat below). `metadata_name` filtering is case-insensitive SQL `LIKE`;
`metadata_code` is exact. **Prefer exact `metadata_code(s)`** for a known class — a broad
`metadata_name LIKE '%temp%'` can saturate the ~300-row favourites page with setpoints and
variants (and silently miss points). Flow searches in particular are dominated by VAV
air-flow (l/s) — for plant flow go via the `CHWS-Common` equipment, not a name `LIKE`.

- `collection_interval` is an ISO-8601 `Period` (e.g. `"PT15M"` = 900 s) — the real
  sample grid for that collector. Reach it via **equipment**, never device (Device is
  BACnet-only; non-BACnet favourites have no device).
- **`collector_id` is encoded in the `fav_id`**: `collector_id = fav_id >> 31`
  (`fav_id = (collector_id << 31) + sequence`; see `peak-db-guide`). So you can group
  points by collector with **zero queries** — and only need `collection_interval` once
  per distinct collector. Subselecting `equipment.collector.collection_interval` on the
  favourites call already gives both, so prefer that unless you've already got the fav_ids.
- Dedupe physical units (drop `-HLI` / `-MSV` duplicates).
- **BACER virtual-point caveat:** virtual/telemetry points (Poll Status, Scan Time,
  Sync %, weather injections — `metadata.system = TRUE`) sample at a fixed **1-min**
  cadence regardless of the collector's `collection_interval`. Don't grid those at the
  collector interval; grid them at their own cadence or exclude them.
- **`collection_interval` is often null on system / meter points**, and the cadence may
  not be 15 min (main energy meters commonly log every **5 min**). When it's null you
  **must infer the interval from the median `ts` delta** of the fetched history — do **not**
  default to 900 s (that 3×'d a 5-min meter's derived demand in testing). 900 s is a last
  resort only when no history can be fetched, and must be flagged in the answer.

## Step 3 — Delegate the bulk fetch (sub-agent → Parquet)

Spawn a **sub-agent / sub-task** whose entire job is: pull history, grid it, write a
Parquet file, and return only a **manifest** (paths + counts + interval per collector +
the fav_id→equipment map). The orchestrator gets the manifest, not the rows.

The sub-agent:

1. **Bulk pull.** `execute_graphql_query(platform.history, args:{fav_ids:[...], start, end, end_exclusive:true}, fields:["fav_id","ts","data"])`.
   Sizing (measured against the live gateway, 15-min points):
   - **One call safely covers ~25–30 k rows / ~2 MB** — e.g. **all 43 AHU points × 7 days
     (~29 k rows) in a single call.** Chunk only to stay under that.
   - The binding limit is **payload size, not the 30 s timeout** (large pulls returned in
     ~13–17 s). Beyond ~2 MB the origin **hard-fails with a Cloudflare 502**
     (`origin_bad_gateway`, retryable, `retry_after 60`) — *not* a clean truncation. On a
     502/5xx, **halve the fav_id batch or the window and retry** (respect `retry_after`);
     don't blindly re-issue the same call.
   - Note: a future-dated `end` does **not** grow the payload (data stops at ~now); push
     `start` back to widen the window.
2. **Unwrap defensively.** Offload-to-file is the **normal** path here — anything past
   ~1 point × 1 week (~45–95 KB) is saved to a file rather than returned inline, so expect
   a path almost always. *(This persist-to-file behaviour is **client-specific**: Claude Code
   saves it + shows a preview; Cowork drops it in `/tmp` readable only via code; some MCP
   clients lose oversized responses entirely. Always read it with code, never inline — and on
   a client that doesn't persist, pre-bound each call under the ~25K-token inline cap.)* The
   offloaded payload may be a text-block wrapper
   (`[{"text":"<json>"}]`), a bare `{"results":[...]}` dict, or a bare list:
   ```python
   obj = json.load(open(path))
   if isinstance(obj, list) and obj and isinstance(obj[0], dict) and "text" in obj[0]:
       obj = json.loads(obj[0]["text"])
   rows = obj.get("results", obj) if isinstance(obj, dict) else obj
   ```
3. **Filter to your own fav_ids and de-dup** — the tool-results dir is shared across
   concurrent sessions, and one chunk can split across files:
   ```python
   want = set(fav_ids)
   rows = [r for r in rows if r["fav_id"] in want]
   rows = list({(r["fav_id"], r["ts"]): r for r in rows}.values())
   ```
4. **Convert ts to site-local in Python** (stdlib `zoneinfo`, DST-correct — do NOT rely
   on duckdb's `AT TIME ZONE`, see Step 4) and **write Parquet**
   (`pyarrow.parquet.write_table`), columns: `fav_id INT64, ts TIMESTAMP (UTC, for
   ordering), local_ts TIMESTAMP (naive site-local, for bucketing), data DOUBLE`.
   Never print rows; return `{parquet_path, n_rows, per_fav_counts,
   interval_sec_by_collector, fav_to_equipment, window}`.

   ```python
   from zoneinfo import ZoneInfo
   tz = ZoneInfo(site_timezone)
   local_ts = ts_utc.astimezone(tz).replace(tzinfo=None)   # naive local, DST-correct
   ```

## Step 4 — Grid in DuckDB (the proven formula, GQL inputs)

Snap each `local_ts` to the nearest grid point, latest-wins per bucket, sparse (no fill).
`$iv` = the collector's `collection_interval` in seconds.

**Timezone is already handled in Step 3** (Python `zoneinfo`). Do the bucket math on the
naive `local_ts` with plain epoch arithmetic — **do not** use duckdb `to_timestamp()` or
`AT TIME ZONE`: both yield a `TIMESTAMP WITH TIME ZONE` that makes duckdb resolve the
session zone and import `pytz`, which fails in a bare environment. The arithmetic below
returns a plain `TIMESTAMP` and needs no extension. (Validated against synthetic data.)

```sql
-- $iv = interval_sec for this collector group
CREATE VIEW gridded AS
SELECT
  fav_id,
  TIMESTAMP '1970-01-01 00:00:00'
    + (floor((epoch(local_ts) + $iv/2.0) / $iv) * $iv) * INTERVAL 1 SECOND AS bucket,
  arg_max(data, ts) AS value,   -- latest-wins within the bucket (data at max ts)
  count(*)          AS n        -- raw sample multiplicity
FROM read_parquet($parquet_path)
GROUP BY fav_id, bucket;
```

If favourites span multiple collectors with different intervals, grid per collector group
(`collector_id = fav_id >> 31`; join the manifest's `interval_sec_by_collector`). To pair
two series of different cadences (e.g. 1-min OAT vs 15-min plant), grid each to the
**coarser** interval before joining. *(Multi-collector / mixed-cadence is documented but not
yet exercised in testing — every tested pair was single-collector 15-min.)*

## Step 5 — Analyse + (optional) chart

Aggregate the `gridded` view to answer the question — examples:

```sql
-- hourly averages over a week
SELECT time_bucket(INTERVAL '1 hour', bucket) h, fav_id, avg(value) v
FROM gridded WHERE ... GROUP BY 1,2 ORDER BY 1,2;

-- run hours (binary status, ON = value>=0.5): on-buckets * interval, to hours
SELECT fav_id, count(*) FILTER (WHERE value >= 0.5) * $iv/3600.0 AS run_hours
FROM gridded GROUP BY fav_id;

-- correlate two signals on a shared hourly axis
SELECT time_bucket(INTERVAL '1 hour', bucket) h,
       avg(value) FILTER (WHERE fav_id = $a) a,
       avg(value) FILTER (WHERE fav_id = $b) b
FROM gridded GROUP BY 1 ORDER BY 1;
```

Write derived results to a small CSV/JSON; chart from that if a visual is wanted (see
**Charting** below).

> **Runtime / engine.** This guideline uses `duckdb` + `pyarrow`. On **local-VM clients
> (Claude Code, Cowork)** they may not be preinstalled — install first
> (`uv pip install duckdb pyarrow`; confirmed working in Cowork). The **locked sandbox**
> (claude.ai-chat / API code-exec) ships `pyarrow`/`pandas`/`sqlite` but has **no duckdb and
> no way to install it** — there, do the gridding/aggregation in pandas or sqlite. When using
> duckdb, build lookup/map relations as DuckDB tables (`CREATE TABLE … ; INSERT INTO … VALUES …`),
> not DataFrames.

## Two-signal / correlation questions

"Does X track Y?", "correlate A vs B", "setpoint tracking" — these pair **two point
classes** and need three things the single-signal path doesn't:

1. **Double the row budget.** Two classes ≈ 2× the points/rows (e.g. 43 AHUs × SAT+setpoint
   = 86 favs ≈ 58 k rows). Plan **≥ 2 fetch batches** from the start (see Step 3 sizing).
2. **Pair the classes by equipment.** Fetch both classes with `equipment.name` +
   `metadata.code`, then build a `fav_id → (equipment, role)` map as a DuckDB table
   (one role per class, e.g. `sat` / `setpoint`). Watch for duplicate points per equipment.
3. **Align per (equipment, bucket), then correlate.** Pivot both roles onto one row, keep
   only buckets where both are present, and report **both `corr()` and MAE** — MAE alone
   misses sign-inverted control; `corr` alone misses offset:

```sql
CREATE TABLE pt(fav_id BIGINT, equip TEXT, role TEXT);   -- from the favourites call
INSERT INTO pt VALUES (...);

CREATE VIEW paired AS
SELECT equip, bucket,
       avg(value) FILTER (WHERE role='sat')      AS a,
       avg(value) FILTER (WHERE role='setpoint') AS b
FROM gridded JOIN pt USING (fav_id)
GROUP BY equip, bucket
HAVING a IS NOT NULL AND b IS NOT NULL;

SELECT equip,
       corr(a, b)        AS corr,   -- NULL if either signal is constant over the window
       avg(abs(a - b))   AS mae,
       count(*)          AS n
FROM paired GROUP BY equip ORDER BY mae DESC;
```

**Constant-signal caveat:** a flat setpoint has zero variance, so `corr()` returns
NULL/NaN — that's "undefined correlation", not zero. Report MAE for those and flag the
correlation as undefined rather than dropping the equipment.

**Reset / relationship questions** ("is X reset against Y?", "X vs OAT"): the headline is a
**regression slope** (`numpy.polyfit(x, y, 1)` → units of y per unit of x), not just `corr`.
Scatter x vs y with the fit line overlaid (see Charting), masked to operating periods. Read
the cloud shape: a **tight, consistent-sign slope** = active reset; a **narrow horizontal
cloud** = fixed setpoint; a **wide, looping, sign-unstable cloud** = the controlled side is
*cycling* against a roughly fixed target (a common third outcome — name it, don't force
reset-vs-flat). Caveat: you can only detect a reset if the driver has enough **dynamic range
in the window** — a winter week with ~6 K of OAT swing can't reveal an OAT reset even if one
exists; widen the window or pick a shoulder/summer period.

## Derived metrics (delta-T, ratios, efficiency)

"Plant delta-T", "chiller kW/ton", "COP", "approach temp" — these compute a **per-bucket
arithmetic combination** of two+ points, then aggregate the result. Same pivot scaffolding
as correlation, but three extra rules that decide whether the number is real:

1. **Mask to operating periods BEFORE aggregating — this is the crux.** A derived metric is
   meaningless when the plant is off (supply = return → delta-T ≈ 0; a denominator → 0 →
   garbage). **Pull a status/flow point alongside the sensors** and filter to running
   buckets. (Worked example: raw weekly avg delta-T was −0.002 K — useless — vs **1.99 K**
   over chiller-on buckets only.) Treat "join a status/flow point and mask to running" as a
   first-class step for any efficiency/derived question.
2. **Derived units & shared operands.** A difference of two °C sensors is **K** (a temperature
   *difference*), not °C absolute; `kWh/h = kW`; `kW/kW = dimensionless`. The two operands
   must share a unit before you subtract — check `metadata.unit.unit` first.
3. **Sign / orientation sanity-check.** Which point is supply vs return (leaving vs entering)
   sets the sign — a metric that's mostly negative during operation means the operands are
   swapped. Prefer **common-header / system points** (e.g. `CHWS-Common` leaving/entering)
   over per-unit points for plant-level metrics.
4. **Multi-operand & unit chains.** Real efficiency metrics combine >2 points with a unit
   chain — e.g. cooling `kW_th = flow(L/s) × ΔT(K) × 4.18`, then `COP = kW_th / kW_elec`
   (dimensionless). Carry every operand's `metadata.unit.unit` and convert explicitly; the
   pivot below extends to N roles.
5. **Graceful degradation when a required operand is missing.** If a measurement the metric
   needs simply doesn't exist (e.g. **no CHW flow point** — common), you cannot compute the
   true metric: say so, then compute and **clearly label a proxy** you can (e.g. `kW per K`
   of ΔT, a 1/COP-style indicator), noting it's relative-only.
6. **Magnitude sanity-check.** Confirm operands are physically plausible in absolute terms,
   not just in sign — a chiller drawing less power than its aux pumps means a scaled/partial
   HLI point; trust the trend, flag the absolute level.

```sql
-- pt: fav_id -> role in {supply, return, status}
CREATE VIEW derived AS
SELECT bucket,
       max(value) FILTER (WHERE role='return') - max(value) FILTER (WHERE role='supply') AS delta_t,
       max(value) FILTER (WHERE role='status') AS running
FROM gridded JOIN pt USING (fav_id)
GROUP BY bucket
HAVING max(value) FILTER (WHERE role='return') IS NOT NULL
   AND max(value) FILTER (WHERE role='supply') IS NOT NULL;   -- both operands present

SELECT avg(delta_t) AS avg_k, median(delta_t) AS med_k,
       100.0 * count(*) FILTER (WHERE delta_t < 4) / count(*) AS pct_below_4k
FROM derived
WHERE running >= 0.5;   -- mask to operating buckets BEFORE aggregating
```

## Data-quality / staleness / anomaly questions

"Which points stopped reporting / look stalled / flat-lined?" are **not** aggregation
questions and must **not** pull the whole site (a site can have thousands of active
favourites — 99 Elizabeth St has ~3,574). Work cheap-first:

1. **Cheapest existence probe — `history_available`, zero rows.** Select the per-favourite
   scalar `history_available(start, end)` on a paginated `platform.favourites` query
   (`limit: 600, no_count: true`) — it returns a boolean per point and pulls **no** history.
   Use a narrow recent window (last 24 h) to find non-reporters.
   - The same field can't take two different arg sets in one query ("Conflicting
     arguments") — one window per call. To split **stopped-mid-week vs dead-all-week**,
     re-query the small false-set over the full week: `false@24h + true@week` = stopped
     this week (**the actionable cluster**); `false@week` = dead / never-populated.
   - Delegate the site-wide pass to a sub-agent that discards the `true` rows and returns
     only the suspect set — don't page thousands of favourites into the orchestrator.
   - (`history(latest:true)` also returns a single point's last value, but it's a row per
     fav, so `history_available` is cheaper for bulk staleness.)
2. **Flat-line detection needs rows — but scope them.** Only stuck-sensor detection needs
   variance: filter to a sensor subset (`metadata_name LIKE '%temp%'`, `metadata.system =
   false`, ~150 favs), pull → grid → DuckDB, flag `stddev_pop(value) = 0` (or `max = min`)
   with `n ≥ ~20` samples.
3. **Classify semantically — flat ≠ fault.** A flat **non-zero analog sensor** = likely
   stuck/faulty (high priority). A flat **0 status/enable**, or a **constant setpoint**, is
   normal (off / fixed config) — benign. Report the two classes separately.

## Energy / meters / demand questions

Energy meters have semantics that break the naive average/sum path — get these wrong and
the number is meaningless:

1. **Cumulative counter vs per-interval — detect, don't assume.** A kWh register is either
   a **monotonic cumulative counter** (daily kWh = `last − first`; mask negative diffs from
   resets/rollover) or **per-interval energy** (daily kWh = `SUM`). Test it on the pulled
   data without printing rows: compute step-to-step deltas — **mostly non-negative &
   monotonic ⇒ cumulative**; **many negatives / non-monotonic / first≈last ⇒ per-interval**.
   Using the wrong rule is the single most common energy error.
2. **Peak demand when only energy exists.** Many main meters expose **only kWh, no kW**.
   Derive `kW = interval_kWh / interval_hours`, and **state the demand-averaging window** —
   it changes the number materially (one site: 126.5 kW @ 5-min vs 78.7 kW @ 15-min). Peak
   = `max` of that derived series.
3. **Pick the right data-quality variant.** Utility/NEM energy comes as Actual / Estimated /
   Substituted / Final-Substituted / CIM-Estimated channels. Use **Actual** for recent
   operational analysis; Final-Substituted is settlement-grade but is **often empty for
   recent days**. Check `history_available` before committing to one.
4. **Sign / import-export & units.** Active energy (kWh) ≠ reactive (kVArh) — don't mix.
   A non-generating building may expose only an `-Export` register that *is* its
   consumption. Report kWh and kW with their units.
5. **No metadata reference for energy/power codes** (unlike status — there's no
   `ooh_*_reference`). `metadata_code` is exact-match and `metadata_type_code "PM"` may
   return nothing; discover meters via `metadata_name LIKE '%energy%'/'%power%'` or by
   pulling a known meter equipment's favourites.

## Charting (histograms, density, scatter, derived series)

The analysis env has **matplotlib + numpy** (no `scipy`, no `pandas`). General statistical
charts — not just the run-hours Gantt — are expected for distribution/scatter/efficiency
questions:

- **Plot only from gridded/derived values**, never raw rows (same context-hygiene rule).
- **Histogram**: density-normalised, sensible bin width; overlay a **hand-rolled Gaussian
  KDE** (Silverman bandwidth in numpy — there is no `scipy.stats.gaussian_kde`); mark the
  comfort/target band (`axvspan`) and median (`axvline`).
- **Scatter / reset curves**: x vs y from the aligned per-bucket frame; fit with
  `numpy.polyfit(x, y, 1)` and report slope + spread; mask to operating periods.
- **Derived time series** (ΔT, kW/K, COP): line plot of the per-bucket metric plus a
  distribution — a Gantt-style runner-bar chart does **not** fit these.
- **Per-entity dotplot**: one mean per zone/equipment, sorted, coloured by band — for
  "which zones run hot/cold".
- Save PNG(s) to the scratch dir and reference the path; never paste images into context.
  *(For a run-hours Gantt specifically, a host skill may ship a bespoke renderer; absent one,
  render it here with matplotlib like any other chart.)*

## Robustness — run on every analysis

- **Verify the window has data; re-anchor if empty.** A site may have stopped reporting (one
  tested site's history ends ~10 months before "today"). Before a full pull, confirm data
  exists in the requested window (cheap `history_available`, or a `latest` ts probe); if
  empty, fall back to the **last available complete window** and **state the actual window**
  — never return "no data" when the site has simply moved on.
- **Screen erroneous / out-of-range values before any stats.** Faulty sensors emit implausible
  readings (e.g. `-40.7` / `90.7` °C — bad data, not clean sentinels like `9999`); 4% such rows
  made one distribution's stats meaningless until gated. The platform doesn't quality-flag
  these, so drop values outside a physically-plausible range for the unit **before**
  averaging / histogramming / correlating, and report the count excluded.
- **Working-hours / weekday masking.** Run-hours, OOH and comfort questions need the site
  `working_hours` block: derive weekday (`isodow`) and hour-of-day from `local_ts` and mask
  to occupied periods. "Weekday average" divides by weekdays in the window; OOH = ON/active
  outside the band (all weekend on-time is OOH when weekends are unoccupied).

## Discipline

- Never `list_graphql_queries` / re-discover schema — the two queries are above.
- Never echo raw samples; read back only derived files.
- Follow-ups re-query the Parquet already on disk — no re-pull for the same window.
- State assumptions (scope, interval fallback, excluded VAV/FCU) in the output, don't ask.
