# Experiment 2 — research: gridding + duckdb/parquet for the MCP/GQL path

Reused from `peak-cli` / `peak-skills` / `agent-toni`, corrected against the live GQL schema.

## Gridding interval IS GQL-reachable (corrects the postgres-only assumption)

The proven gridding needs a per-collector sampling interval. `peak-cli` reads it from
postgres (`EXTRACT(EPOCH FROM c.collection_interval)`, default 900) and an initial
research pass concluded it was postgres-only. **It is not** — it's on the GQL schema:

```
Favourite.equipment.collector_id              -> Int   (group points by collector)
Favourite.equipment.collector.collection_interval -> Period (ISO-8601 duration)
```

`Collector.collection_interval` is documented *"How often Collector gathers data and
sends to API."* Live check at 99 Elizabeth St (site_id 3), collector 57:
`collection_interval = "PT15M"` (= 900 s). `collector_type = "Physical"`.
(Distinct from `Collector.sync_interval` (Int seconds) = API-sync cadence, NOT the grid.)

> Path notes: the collector hangs off **Equipment**, not Device (Device is BACnet-only,
> and non-BACnet favourites have no device). So always traverse
> `favourite -> equipment -> collector`.

So one favourites call returns fav_ids + collector_id + interval together — no extra
round-trip, no postgres. `assume 15 min` is only a fallback (null interval, or infer
from median ts-delta).

## The gridding algorithm (peak-cli, ported to GQL inputs)

`peak-cli/src/peak_cli/tools/trends.py:26` (`NATIVE_GRID_BUCKET_EXPR`):

```sql
to_timestamp(FLOOR((EXTRACT(EPOCH FROM local_ts) + interval_sec/2.0) / interval_sec) * interval_sec)
```

Plain English:
1. Get `interval_sec` per favourite from `collection_interval` (ISO-8601 -> seconds).
2. Convert each raw `ts` to the site-local frame (`AT TIME ZONE site.timezone`) — DST is
   then transparent.
3. **Nearest-neighbour snap**: `floor((epoch + interval/2)/interval)*interval`.
4. **Latest-wins** within each `(fav_id, bucket)`: rank by `ts DESC`, keep `rn=1`
   (`trends_engine.py:117-118`). Carry `count(*)` to expose raw sample multiplicity.
5. **Sparse grid** — no forward-fill/zero-order-hold; empty buckets are simply absent.
   Detect gaps by comparing expected vs actual bucket counts.

## DuckDB-over-parquet pattern

- Write: `pyarrow.parquet.write_table(table, path)` (`trends_engine.py:282-291`).
  Schema used by peak-cli's `trend` relation: `trend_id INT64, local_ts TIMESTAMP,
  value FLOAT64, count INT64, working_hours BOOL`.
- Aggregate in duckdb SQL (`peak-skills/skills/trend-analyzer/SKILL.md:46-68`):
  `time_bucket(INTERVAL '1 hour', local_ts)`, `date_trunc('day', local_ts)`,
  `avg(value) FILTER (WHERE trend_id = ...)`, etc.

## What does NOT port from agent-toni

`agent-toni` shells out: `peak trends "<SQL>" --out x.parquet` then a chart script over
the parquet (bash pipes, file-based). That path talks to **postgres** via the CLI and
needs a shell — neither exists for an MCP client. We keep the *shape* (fetch -> parquet
-> duckdb -> summary/chart) but the fetch is GQL `platform.history` offloaded to a file,
and the gridding interval comes from `collection_interval`, not the CLI.

## Implication for the guideline

A GQL-only client CAN do correct, deterministic gridding. The Experiment-2 guideline:
reference-driven fav_id selection (carry collector_id + collection_interval in the same
query) -> sub-agent bulk history fetch offloaded to disk -> parquet -> duckdb gridding +
aggregation with the formula above -> return summary (+ optional chart). No raw rows in
the orchestrator context; no postgres.
