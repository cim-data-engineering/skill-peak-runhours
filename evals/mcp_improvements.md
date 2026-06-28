# PEAK MCP improvements — for backend discussion

Findings from the time-series eval (Exp1 run-hours; Exp2 timeseries guideline + correlation,
distributions, COP, reset; gateway probe), framed as a building-performance engineer using the
MCP + GraphQL `platform.history` for analytics.

**Reconciled against `~/dev/peak-trends-mcp`** — David's POC, which already prototypes the
server-side data path (DuckDB gridding `grid_sql.py`/`engine.py`, server charts
`charts.py`/`chart_render.py`, and large-asset delivery with **empirical CoWork results**,
`docs/asset-delivery.md`, 2026-06-04). So this eval mostly **independently validates that POC's
direction from the client/agent side**; the genuinely *new* asks are in §4.

## TL;DR

**The issue**
- Time-series analytics over the MCP spends most effort *moving data*, not analysing it:
  chunk → fetch → offload → unwrap → de-dup → Parquet → only then analyse. Fragile, high-variance.
- The graceful "offload to a file" we rely on is a **Claude Code host behaviour, not a platform
  guarantee** — other clients can silently lose large responses; CoWork drops them in `/tmp`.
- **Client portability is the crux** — two runtime buckets: **local-VM** (Claude Code / CoWork:
  DuckDB installable, sub-agents, network) vs **locked sandbox** (claude.ai-chat / API: no DuckDB,
  no network, no sub-agents). No single *client-side* design serves both.
- There is **no server-side gridding/aggregation today** — every average / run-hour / correlation
  is computed client-side over raw rows.
- `peak-trends-mcp` (the POC) already prototypes the server-side fix.

**The options** (near-term feasible first; detail in §1)
- **A — Server-side DuckDB-over-GQL analytics tool** *(recommended)*: the tool does
  gridding/aggregation and returns compact results (or a small Parquet). Works in **both**
  buckets, no client deps — and is the **migration seam** (swap GQL→postgres later, same tool).
- **B — Parquet delivery + client-side DuckDB**: return `ResourceLink`→S3 (large) or
  `EmbeddedResource(parquet)` (small/medium); client queries with DuckDB. **Local-VM clients only**
  (needs client-side DuckDB).
- **C — Postgres push-down**: **later — not short-term feasible**; reached via A's seam.

**Net-new platform asks** (not covered by the POC; detail in §4)
- **Data-quality flags** on `Favourite`/`History` — bad/scaled readings aren't surfaced.
- **Metadata discovery** — keyword-searchable metadata / metadata-type tools.

## 1. The history path — problem, runtimes, and the recommended near-term design

**The extraction tax.** Every time-series question today spends most agent effort on plumbing:
chunk fav_ids → fetch → response offloads to a file → unwrap an inconsistently-shaped payload →
filter foreign rows from a shared dir → de-dup → write Parquet → only then analyse. Fragile
(caused the PR-1 crash) and high-variance (8 calls when lucky, outright failure when not).

**The offload safety-net is client-specific — prior testing confirms data loss elsewhere.**
`peak-mcp-eval/docs/bugs.md` (28 Apr 2026): 5 of 8 tools overflow the **25K-token** cap on
unbounded calls. Claude Code persists-to-disk + 2 KB preview (graceful); **other clients may
silently lose the data**; in CoWork the payload landed in `/tmp`, reachable only via a script.
So the net we lean on is host-harness behaviour, not a platform guarantee.

**Two runtime buckets — the design must serve both.**
- **Local-VM, Claude-Code-like — Claude Code & CoWork.** Sub-agents available; shell + FS;
  network via MCP; packages installable (**DuckDB via uv/pip — confirmed installable in CoWork
  2026-06-28, not preinstalled**). CoWork runs Agent Skills via zip. Client-side DuckDB works here.
- **Remote locked sandbox — Claude API code-exec & claude.ai-chat analysis tool.** Verified vs
  Anthropic docs: **no network** (no pip, can't fetch S3), **no DuckDB / no way to add it**, **no
  sub-agents**; pandas/numpy/scipy/**pyarrow/sqlite** preinstalled. Client-side DuckDB does **not**
  work here.

You can't tell which bucket a user is in — so the data path shouldn't *depend* on client
DuckDB or sub-agents.

**Recommended near-term paths (ranked; reconciled with the POC + feasibility):**

1. **History analytics tool: server-side gridding + DuckDB-over-GQL.** The POC's model — *one
   swappable source* (`engine.py`): GQL now, postgres/analytics-DB later. The tool does the
   gridding/resample/aggregate server-side and returns **compact results** (or a small
   pre-gridded Parquet via the result channel). **Best target**: works in *both* runtime buckets,
   needs no client deps or network for compute, and is context-cheap. This is the migration seam
   — the tool surface stays put when the source later moves to postgres.
2. **Parquet delivery + client-side DuckDB** (works in the local-VM bucket only):
   - **`ResourceLink` → S3** (presigned / CloudFront). Best for **large** data — no bytes through
     MCP; client fetches on demand. NB from the POC test: presigned URLs are **too long for the
     built-in web-fetch tool** (agent fell back to `curl`); use short CloudFront URLs in prod.
   - **`EmbeddedResource(application/vnd.apache.parquet)`** — CoWork now accepts Parquet blobs
     (the old "not supported" limitation lifted); written host-side out-of-band, then copied
     host→sandbox (~3 calls). Best for **small/medium**.
   - Both then queried with local DuckDB — so **local-VM only** (CoWork/Claude Code), not the
     locked sandbox.
3. **Postgres push-down — LATER, not short-term feasible.** Path 1's DuckDB-over-GQL engine is
   the seam; swap the source to postgres or an analytics DB when ready, same tool surface.

**Delivery mechanics — empirical (CoWork 2026-06-04, `peak-trends-mcp/docs/asset-delivery.md`):**
- Write to the host's **Working Folder root (an MCP root)**, **not `/tmp`** — CoWork's file
  presenter is sandboxed and can't read server temp dirs (this is the "/tmp inaccessible" issue).
  Write to the folder *root* (CoWork's file UI only lists root-level files).
- **Charts: deliver a file, open it by path — never inline.** Inline SVG ≈ 1 min token-stream;
  inline PNG is broken. (The eval guideline independently lands the same rule.)
- **Interactive MCP Apps (`ui://`) are blocked in the CoWork preview** (host advertises ui
  support but never mounts the iframe; matches `anthropics/claude-ai-mcp#165`) → **static images
  only** for now.

**Requests for whichever tool ships:**
1. **Return the grid inputs with the data**: `collector_id` + `collection_interval` per fav_id
   (or a pre-gridded mode). (`collector_id = fav_id >> 31`; `collection_interval` is on
   `equipment.collector.collection_interval` — §3.)
2. **Server-side resample/aggregate params** — `bucket`, `agg`, `tz` — with the proven
   nearest-snap + latest-wins semantics (`floor((epoch+iv/2)/iv)*iv`, `arg_max(value,ts)`).
   *(The POC's `grid_sql.py` already encodes this.)*
3. **Stable Parquet schema** `fav_id BIGINT, ts TIMESTAMP, data DOUBLE` (+ `local_ts` if tz-applied).
4. **Pagination / cursor** for the still-large case, instead of a hard 502.

## 2. Current generic-MCP issues (status quo the tool replaces)

Real for the interim generic-MCP path; **most are moot once a dedicated tool owns delivery**
(the POC writes to the Working Folder root with its own envelope):
- **Gateway fails hard at scale** (`exp2_gateway_probe.json`): graceful file-offload at
  ~45–95 KB, but a **Cloudflare 502 beyond ~2 MB / ~30 k rows** (retryable; not clean
  truncation). Binding limit is **size, not the 30 s timeout**. → pagination, not 502.
- **Shared tool-results dir leaks across sessions** (foreign fav_ids; one chunk split across
  files). → the POC sidesteps this by writing its own file to the Working Folder root.
- **Inconsistent offloaded-payload shape** (text-block wrapper vs bare dict vs bare list;
  crashed PR-1). → a dedicated tool controls its own envelope.

## 3. GQL / data-model notes (the tool consumes these; one is a platform ask)
- `equipment.collector.collection_interval` (ISO-8601 `Period`, e.g. `PT15M`) **does** expose the
  grid via GQL — reach it via **equipment**, not device. Surfacing it (+ `collector_id`) on
  `Favourite` would save a hop.
- **BACER virtual points** (`metadata.system=TRUE`) sample at a fixed **1-min** cadence regardless
  of collector interval — the tool should flag/group them so they aren't gridded wrong.
- **`collection_interval` is null on system/meter points** (the main energy meter at site 3 logs
  every 5 min with a null interval; a naive 900 s default 3×'s derived demand). **Platform ask:**
  populate it for system collectors.
- **`metadata.unit` is a nested `MetadataUnit`** — must subselect `metadata.unit.unit`; a flat
  alias would help.

## 4. Net-new platform asks (NOT covered by the POC's data path)

### A. Known-bad data isn't quality-flagged
- **Erroneous / outlier readings** — 13 zone sensors at 100 Harris St read `-40.7` / `90.7` °C
  (not clean sentinels, just bad data) and silently corrupted the distribution until gated.
- **Scaled / partial points** — `CH-Pow-Cons-kW` (an `-HLI` point) read ~3× below the aux pumps:
  a commissioning/scaling error, trustworthy only as a trend.
- **Ask:** quality/validity flags on `Favourite`/`History` (per-point, ideally per-reading), or
  expose the BMS quality bit — so agents *and* dashboards trust/exclude rather than each
  re-deriving "is this physically plausible?".
- *(Missing instrumentation — e.g. no CHW flow → no true COP — is expected BMS reality, not a
  platform gap; agents degrade to a labelled proxy. A flag for exists-but-uncommissioned points
  would still help.)*

### B. Metadata discovery is the weak link
- No good way to **find point classes**: `metadata_name LIKE` saturates the ~300-row favourites
  page and silently drops points; `metadata_code` is exact-match; `metadata_type_code "PM"`
  returns nothing; no keyword search over metadata / metadata-types. Agents fall back to fragile
  `LIKE` probing or hand-curated maps (status `ooh_*_reference`; nothing for energy/power).
- **Ask:** dedicated **metadata / metadata-type search tools with keyword searchability** (the
  `search_sites` / `search_equipment` pattern). Subsumes the args-vs-fields friction
  (`metadata_code` etc. are filter args, not selectable fields) and the missing energy/power
  reference.

### Minor
- **Data freshness isn't a cheap lookup** — a site's history can end months before "now"
  (100 Harris St ends 2025-08-30) with no signal but an empty window. A `last_history_ts` on
  Site/Collector/Favourite would make staleness a lookup, not a probe-and-re-anchor.
- **`platform.collectors`** (collector_id, site_id, `collection_interval`) is useful for
  cadence/site discovery; surfacing it from `Site` (`site.collectors`) would save a hop.

---
*Evidence: `evals/results/exp1_*.json`, `exp2_*.json`, `exp2_gateway_probe.json`.
Cross-ref: `~/dev/peak-trends-mcp` (`docs/asset-delivery.md`, `docs/charting.md`,
`docs/postgres-pushdown.md`, `experiments/`).*
