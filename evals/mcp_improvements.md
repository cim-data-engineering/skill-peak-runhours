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

### Why this can't be solved client-side (portability argument)
Our guideline keeps raw history out of context by **delegating the fetch to a sub-agent** that
returns only a manifest — a **Claude Code** orchestration construct. You **can't assume it
exists** across MCP clients: claude.ai chat runs the work inline in one context (no sub-agent);
cowork's status is unconfirmed (it may be Claude-Code-based and keep sub-agents, or have them
disabled — being verified). Where there is no sub-agent, the client **cannot** isolate data
volume itself; offload-to-file helps (you get a path, not rows) but relies on disciplined
code-execution and breaks the moment a chunk returns inline. **So data-volume isolation
shouldn't depend on a client-specific feature — own it on the platform.** Server-side
aggregation (next subsection) works identically whether or not the client has sub-agents.

### Runtime reality reshapes the tool design (verified against Anthropic docs)
The execution environment external clients use is **constrained in ways that rule out the
obvious "S3 link + client DuckDB" design**:
- claude.ai / API **code-execution sandbox** preinstalls pandas, numpy, scipy, **pyarrow,
  matplotlib, sqlite** — but **not DuckDB**, and there is **no dependency-manifest field** in
  the Agent Skill format to add it.
- The **API sandbox has no network** (no `pip install`, and **cannot fetch an external S3
  URL**); claude.ai network access "varies by admin/user settings". (Claude Code runs locally,
  so DuckDB/uv are fine there — but that's not where external customers run.)

Consequences for the tool:
1. **Don't hand back an S3 link the sandbox can't reach.** Deliver results through the **MCP
   tool-result channel** (the sandbox-accessible path, as offload already does), not an
   external URL the no-network sandbox can't GET.
2. **Don't assume the client can run DuckDB.** If the client must query the data, it'll be with
   **pandas / sqlite / pyarrow** (preinstalled), not DuckDB.
3. **Therefore prefer server-side aggregation.** Do the gridding/resample/aggregate on *your*
   side (DuckDB/quack on the server) and return **compact results** (or a small pre-gridded
   Parquet via the result channel). This is the only design that is simultaneously
   network-safe, dependency-free on the client, and context-cheap — and it works identically
   in claude.ai, cowork, and Claude Code.

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

## 6. Two cross-cutting themes from the analytics rounds

Beyond the history tool (§1), the rounds kept hitting **two** things:

### A. Known-bad data isn't quality-flagged
- **Erroneous / outlier readings** — 13 zone sensors at 100 Harris St read `-40.7` / `90.7` °C.
  These are *not* clean sentinels (you'd expect e.g. `9999`); they're just bad sensor data, and
  they silently corrupted the distribution until gated.
- **Scaled / partial points** — `CH-Pow-Cons-kW` (an `-HLI` point) read ~3× below the plant's
  aux pumps: a commissioning/scaling error, trustworthy only as a trend, not an absolute.
- Both are **known-bad data the platform doesn't surface**. **Ask:** quality/validity flags on
  `Favourite` / `History` (per-point, ideally per-reading), or expose the BMS quality bit — so
  agents *and* dashboards can trust or exclude values instead of each re-deriving "is this
  physically plausible?".
- *(Missing instrumentation — e.g. no CHW flow, so no true COP — is **expected** BMS reality,
  not a platform gap; agents should degrade to a labelled proxy, which the guideline now does.
  A flag for points that **exist but aren't commissioned** would still help.)*

### B. Metadata discovery is the weak link
- There's no good way to **find the right point classes**. `metadata_name LIKE` saturates the
  ~300-row favourites page and silently drops points; `metadata_code` is exact-match;
  `metadata_type_code "PM"` returns nothing; and there's no keyword search over metadata /
  metadata-types. Agents fall back to fragile `LIKE` probing or hand-curated references (the
  status `ooh_*_reference`; nothing exists for energy/power).
- **Ask:** dedicated **metadata** and **metadata-type search tools with keyword searchability**
  — the same pattern as `search_sites` / `search_equipment`. This subsumes the args-vs-fields
  friction (§5) and the "no energy/power reference" pain: with real discovery, agents stop
  guessing codes and stop maintaining curated maps.

### Minor, still worth noting
- **Data freshness isn't a cheap lookup** — a site's history can end months before "now"
  (100 Harris St: last record 2025-08-30) with no signal but an empty window. A `last_history_ts`
  on Site/Collector/Favourite would make staleness a lookup, not a probe-and-re-anchor.
- **`platform.collectors` is useful** (collector_id, site_id, `collection_interval`); surfacing
  it from `Site` (`site.collectors`) would save a hop.

---
*Evidence: `evals/results/exp1_*.json`, `exp2_*.json`, `exp2_gateway_probe.json`.*
