# Making agent-toni state-of-the-art at time-series analytics

Deep review of `agent-toni` + `peak-cli` + `peak-skills` against the analytical methods
hardened in this session (the timeseries-analysis guideline). Unlike the MCP work (external,
GraphQL, general-purpose), agent-toni is **internal and postgres-backed**, so its *data
plumbing* is already excellent — the transferable value is the **analytical-reasoning layer**.

## Headline

agent-toni today will write **syntactically-correct DuckDB and reach analytically-wrong
conclusions** — exactly the failure class this session hardened against (the −0.002 K
unmasked delta-T → 1.99 K masked; the 3×-inflated 5-min-meter demand; the unscreened −40.7 °C
sensor poisoning a mean). The plumbing is SOTA; the **method playbook and a routing
decision-tree are missing**.

## Already SOTA — preserve (in places exceeds the guideline)
- **Gridding**: `peak-cli` grids **multi-collector mixed-cadence in one pass** (`trends.py`
  `NATIVE_GRID_BUCKET_EXPR` + per-row `interval_sec`; latest-wins `trends_engine.py:117`) —
  the exact case the guideline marked "documented-but-untested." Does tz in **Postgres**
  (`AT TIME ZONE` + 1h pad), sidestepping the duckdb/pytz issue the guideline warns about.
- **Cheap staleness**: `trends-meta` returns the actual `last_sample_ts` (better than the
  MCP's `history_available` boolean).
- **BACER virtual-point cadence** caveat (both directions) in `trend-analyzer` is *more*
  developed than the guideline's.
- **Off-vs-no-data coverage check** before claiming "off all day" (`trend-chart`) and the
  **narrate-from-data-not-the-image** guardrail are hard-won and worth keeping.

## The gaps (consistent across all three layers)

| # | Method | peak-cli | peak-skills | agent-toni |
|---|---|---|---|---|
| Gridding / tz / working-hours-flag | ✅ exceeds | ✅ | ✅ (via CLI) |
| Staleness / stopped-vs-dead | ✅ exceeds | ✅ | ✅ |
| **Correlation: corr + MAE, pair-by-equip, constant→NULL** | raw-SQL only | ❌ | ❌ |
| **Reset/relationship: slope headline + reset/flat/cycling + dynamic-range** | ❌ | ❌ | ❌ |
| **Derived metrics (ΔT/COP): per-bucket arith + OPERATING-STATE MASKING + units + proxy + magnitude** | ❌ | ❌ | ❌ |
| **Sentinel / out-of-range screening before stats** | ❌ | ❌ | ❌ |
| **Flat-line-vs-off semantic classification** | ❌ | ❌ | ❌ |
| **Energy/meters: cumulative-vs-interval, demand-from-kWh, NEM variants, import/export** | ❌ | ❌ | ❌ |
| Empty-window **re-anchor** (vs just diagnose) | partial | partial | partial |
| Charting *prescription* (histogram/KDE, scatter+fit, dotplot, derived-series) | n/a (emits Parquet) | capability yes, "when" no | "when" no |
| **Analytics methodology / decision-tree** | n/a | n/a | ❌ (routing is template/rules-only; sensor-analytics is "coming soon") |

## Recommendations — by layer (priority order)

### 1. peak-cli — enrich the relation + correctness guardrails (highest leverage)
- **Enrich the `trend` relation** with `equipment_id`, `equipment_name`, `point_code`,
  `unit` (the fetch already joins equipment; add metadata/units). **One change unblocks four
  items** — pairing-by-equipment (correlation/derived metrics), unit-safe ΔT/COP, and
  per-unit sentinel screening — none of which is possible today because `trend` is just
  `trend_id, local_ts, value, count, working_hours`.
- **Sentinel/range screening**: a `--screen` flag (or per-unit plausibility default) that
  NULLs implausible values before they reach the relation, with a `screened_count` in the
  envelope. Cheap, protects every `avg()`/`corr()`.
- **Energy mode**: `--meter cumulative|interval|auto` on `trends` (auto = detect monotonicity
  server-side) emitting per-interval kWh + a derived-kW column with an explicit demand window;
  counter-semantics must not be left to agent SQL. (Or a `peak energy` command.)
- **Empty-window `--anchor last-available`**: on an empty window, slide to the last complete
  window and report the actual window in the envelope (`trends-meta` already provides the
  `last_sample_ts` primitive).

### 2. peak-skills/trend-analyzer — turn the SQL cookbook into a method playbook
Port these guideline sections (most SQL drops in unchanged — the `gridded` view → the `trend`
relation). This is the **de-facto home** (the agent's topic-trigger reflex already routes
sensor/trend/correlation questions here; a new skill would need a new trigger + host symlink).
- **Correlation** — `corr()` + MAE side-by-side, pair-by-equipment, constant→NULL caveat
  (extend the existing pivot at `trend-analyzer:62-68`).
- **Reset/relationship** — `regr_slope` as headline; active-reset / flat-setpoint / **cycling**
  taxonomy; driver-dynamic-range caveat.
- **Derived metrics** — per-bucket ΔT/COP; the **mask-to-operating-periods-BEFORE-aggregating**
  rule (the crux); derived units (°C−°C=K, kWh/h=kW); operand magnitude/sign sanity; labelled
  proxy when a required operand (e.g. CHW flow) is missing. *(Highest-value single section.)*
- **Flat-line classification** — `stddev_pop=0` / `min=max`, and flat non-zero analog = stuck
  (priority) vs flat-0 status / constant setpoint = benign — alongside the existing strong
  stopped-streaming section.
- **Energy/meters** — new section: cumulative-vs-interval detection, demand-window, NEM
  Actual/Estimated/Substituted, kWh≠kVArh, export-as-consumption.
- **Robustness** — sentinel screening before any stat; empty-window **re-anchor** (recover,
  not just diagnose); OOH / weekday-average masking (no run-hours/OOH recipe exists today).
- A short **"Choosing a method"** preamble (single-signal / correlation / derived-metric /
  energy / data-quality → which section).
- **trend-chart**: add worked `--spec` templates for histogram+KDE, scatter + `polyfit`
  fit-line, per-entity dotplot, derived-series line, and a "when to reach for which" note.

### 3. agent-toni — give it an analytics decision-tree
- The routing (`analysing-templates/agent.md` Query Routing, 14 types) is **template/rules-only**;
  open sensor-analytics ("does SAT track setpoint?", "plant delta-T?", "peak demand?", "which
  sensors look stuck?") has no route, and `routing-requests/agent.md` lists trend-data
  diagnosis as **"coming soon" / out of scope**. Add an **open-analytics query type** → load
  `trend-analyzer`, classify, apply the matching section. Revisit the "coming soon" lines now
  that the capability exists.
- Keep method content in the skill, not the (already ~690-line) agent.md, per its own
  "ground answers in skill documentation" rule.

## Why it matters
The session's live runs proved the methods find real issues an unguided agent would miss or
mis-state: low delta-T syndrome at 99 Eliz (only visible once masked to operating periods),
dead VSD comms, a cold-skewed building + 13 dead sensors (only correct after sentinel
screening), and a chiller cycling (not reset) against a fixed target. Porting these into
agent-toni's skill layer + a routing decision-tree closes the gap between "writes valid SQL"
and "reaches the right answer."

---
*Source reviews: peak-skills, peak-cli, agent-toni (this session). Port from:
`skill-peak-runhours/guidelines/timeseries-analysis.md`.*
