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
two-signal correlations, out-of-hours analysis, anomaly spotting. The run-hours Gantt is
**one specialisation** of this pipeline — see `SKILL.md`.

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

Pick `fav_id`s for the question. For status/run-hours use the tiered `metadata_id` map in
`references/ooh_status_metadata_reference.md`; for sensor/setpoint analysis filter by point
class. **In the same call, also fetch the gridding inputs** so no extra round-trip:

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
`metadata_code` is exact.

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
   a path almost always. The offloaded payload may be a text-block wrapper
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
(`collector_id = fav_id >> 31`; join the manifest's `interval_sec_by_collector`).

## Step 5 — Analyse + (optional) chart

Aggregate the `gridded` view to answer the question — examples
(see `peak-skills/skills/trend-analyzer/SKILL.md` for more):

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

Write derived results to a small CSV/JSON; chart from that if a visual is wanted (for the
run-hours Gantt, hand the render-aggregate JSON to `scripts/render_runhours.py`).

> **No pandas.** The analysis env has `duckdb` + `pyarrow` only — build lookup/map
> relations as DuckDB tables (`CREATE TABLE ... ; INSERT INTO ... VALUES ...`), not
> DataFrames.

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

## Discipline

- Never `list_graphql_queries` / re-discover schema — the two queries are above.
- Never echo raw samples; read back only derived files.
- Follow-ups re-query the Parquet already on disk — no re-pull for the same window.
- State assumptions (scope, interval fallback, excluded VAV/FCU) in the output, don't ask.
