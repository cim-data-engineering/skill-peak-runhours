# PEAK MCP improvements — for backend discussion

Findings from the time-series eval (Exp1 run-hours, Exp2 timeseries guideline +
correlation, gateway probe). Framed as a building-performance-engineer using the MCP +
GraphQL `platform.history` for analytics. Ordered by impact.

## 1. The history-extraction tax (the big one)

**Every** time-series question spends the majority of agent effort on plumbing, not
analysis: chunk fav_ids → fetch → response offloads to a file → unwrap an
**inconsistently-shaped** payload → filter foreign rows out of a **shared** dir → de-dup →
write Parquet → only then analyse in DuckDB. This is fragile (it caused the PR-1 crash bug)
and high-variance (a capable agent does it in 8 calls; an unlucky one fails — Exp1 A1).

### Strongly endorse: a dedicated history tool
The proposed tool — **`history(fav_ids[], start, end) → writes Parquet to S3 → returns a
link`, agent then DuckDB-queries the link** — would delete almost all of the above. The
eval evidence is unambiguous: Step 3 of our guideline exists **only** to work around the
absence of this tool. Benefits: no chunking, no offload-shape handling, no foreign-row
filtering, no context-flood risk, deterministic cost.

### Even better (the "ideal"): direct DuckDB/quack access to history
Letting the agent attach read-only to the history store and write SQL directly would:
- eliminate extraction entirely (no S3 round-trip for ad-hoc work);
- **enable server-side gridding + aggregation**, which the platform currently lacks — the
  run-hours skill notes "there is no server-side run-hours/aggregation field; history
  returns raw ts/data only." Today every average/run-hour/correlation is computed
  client-side over raw rows. Pushing `time_bucket`/`avg`/`corr` to the engine is a step
  change in both speed and context cost.

### Requests for whichever tool ships
1. **Return the grid inputs with the data**: `collector_id` + `collection_interval` per
   fav_id (or offer a pre-gridded mode), so the agent needn't a second query. (Note:
   `collector_id = fav_id >> 31`, and `collection_interval` is already on
   `equipment.collector.collection_interval` — see #4.)
2. **Optional server-side resample/aggregate** params: `bucket=15m|1h|1d`, `agg=avg|max|last`,
   `tz=<site>` — with the proven nearest-snap + latest-wins semantics
   (`floor((epoch+iv/2)/iv)*iv`, `arg_max(value,ts)`).
3. **Stable Parquet schema**: `fav_id BIGINT, ts TIMESTAMP, data DOUBLE` (+ `local_ts` if tz
   applied).
4. **Session-isolated output** (see #3 below) and a **cursor/pagination** path instead of a
   hard 502 (see #2).

## 2. Gateway response-size behaviour is undocumented + fails hard

Measured (`exp2_gateway_probe.json`):
- **Inline → file-offload at ~45–95 KB** (a client token cap). Graceful, but undocumented;
  agents discover it by luck.
- **Hard Cloudflare 502 beyond ~2 MB / ~30 k rows** (`origin_bad_gateway`, retryable). Not a
  clean truncation or MCP error — a raw transport failure. Largest success: 43 favs × 7 days
  = 28,889 rows / ~2 MB.
- Binding limit is **size, not the 30 s timeout** (nothing neared 30 s).

**Ask:** document the real ceiling; ideally return a structured "too large, paginate with
cursor X" instead of a 502. (The skill currently mis-states this as "528 KB / chunk above
~12 points" — PR #2 corrects the skill side.)

## 3. Shared tool-results directory leaks across sessions

Offloaded files from concurrent sessions land in the same dir; an agent globbing for "its"
files picks up foreign fav_ids, and one logical chunk can split across files. We work around
it by filtering to our own fav_id set + de-dup by `(fav_id, ts)`. **Ask:** per-session
(or per-call) isolation of offloaded results.

## 4. Inconsistent offloaded-payload shape

The offloaded file is sometimes the MCP text-block wrapper (`[{"text":"<json>"}]`),
sometimes a bare `{"results":[...]}` dict, sometimes a bare list. The documented unwrap
assumed one shape and crashed (PR #1). **Ask:** one stable envelope.

## 5. Smaller GQL/MCP notes
- `equipment.collector.collection_interval` (ISO-8601 `Period`, e.g. `PT15M`) **does** expose
  the sample grid via GQL — good. Reach it via **equipment**, not device. Consider surfacing
  it (and `collector_id`) directly on `Favourite` to save a hop.
- **Args-vs-fields friction**: `metadata_code`/`metadata_name`/`metadata_ids` are filter
  *args* but not selectable fields; reading the class needs `metadata.code/name/system`.
  This trips every agent (cost a failed call in two eval arms). The glossary fixes it for
  humans; embedding the rule in the tool descriptions would fix it for agents.
- **BACER virtual points** (`metadata.system=TRUE`: Poll Status, Scan Time, Sync %, weather)
  sample at a fixed 1-min cadence regardless of the collector interval — a flag/grouping the
  history tool should expose so callers don't grid them wrong.
- **`collection_interval` is null on system/meter points** (e.g. the main energy meter at
  site 3 logs every 5 min with a null interval). Agents must then infer cadence from `ts`
  deltas; a naive 900 s default 3×'s derived demand. **Ask:** populate `collection_interval`
  for system collectors, or expose the true cadence on the point.
- **`metadata.unit` is a nested `MetadataUnit`** — `metadata.unit.unit` is the string. Minor,
  but every units-aware query must subselect it; a flat `unit` string alias would help.
- **No metadata reference for energy/power point classes.** Status has the curated
  `ooh_status_metadata_reference`; there is no analogue for meters (PM-AEx active/reactive,
  CH-Pow-Cons-kW, VSD-Pow/EnCon). `metadata_code` is exact-match and `metadata_type_code
  "PM"` returns nothing, so meter discovery is fragile (name `LIKE` probing). **Ask:** a
  point-class catalogue (or richer `search_*`) for energy/power, incl. cumulative-vs-interval
  and import/export/Actual-vs-Substituted semantics so agents don't infer them from the data.

---
*Evidence: `evals/results/exp1_*.json`, `exp2_*.json`, `exp2_gateway_probe.json`.*
