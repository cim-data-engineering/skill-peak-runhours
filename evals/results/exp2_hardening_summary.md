# Experiment 2 — guideline hardening (consolidated)

Goal: stress the `timeseries-analysis` guideline as a building-performance engineer across
the real analysis space (`analysis_taxonomy.md`), looping fix→retest. Each round was a
generic agent *following the guideline* on a new question shape; its `deviations` field
drove the next fix.

## Rounds (all verified from transcripts)

| Round | Question | Calls | Schema-disc | Failed | Rows→ctx | Real finding |
|---|---|--:|--:|--:|:--:|---|
| G  | hourly SAT profile | 9 | 2 | 1 | no | curve ~20.6→25.3 °C; pipeline ran verbatim |
| G2 | SAT vs setpoint tracking | 4 | 0 | 0 | no | per-AHU corr+MAE; sign-inverted control flagged |
| G3 | stalled/dead points | 18 | 2 | 1 | no | 67 dead (34 actionable VSD, 33 benign meters) |
| G4 | daily kWh + peak kW | 15 | 2 | 1 | no | ~4,457 kWh/wk; peak 126.5 kW @5min |
| G5 | CHW plant delta-T | 4 | 0 | 0 | no | **low delta-T syndrome, 1.99 K (unhealthy)** |

Every arm kept raw rows **out of context** and used DuckDB. Efficiency improved as the
guideline got more prescriptive (G/G3/G4 = 9–18 calls; the well-specified G2/G5 = 4).

## What each round added to the guideline

- **G** — point-selection args-vs-fields (`metadata.code/name`), gateway sizing corrected
  to measured ~2 MB.
- **G2** — *Two-signal / correlation* section: pair-by-equipment, CASE-pivot + `corr`/MAE,
  constant-signal NULL caveat, no-pandas note.
- **G3** — *Data-quality* section: cheap `history_available` probe (no rows), stopped-vs-dead
  split, flat-line variance, flat≠fault semantics.
- **G4** — *Energy/meters* section + **fixed a correctness bug**: null `collection_interval`
  must infer from `ts` deltas (a 5-min meter would be 3× wrong at the 900 s default);
  cumulative-vs-interval detection; peak-kW-from-energy + demand window; `metadata.unit.unit`
  nesting; NEM data-quality variants.
- **G5** — *Derived metrics* section: per-bucket arithmetic, **operating-state masking as a
  first-class step** (raw −0.002 K → 1.99 K masked), derived units, sign sanity-check.

## Cross-cutting lessons

- **Operating-state masking** and **value semantics** (cumulative vs interval, ON=1 vs 2,
  unit of a difference) are where correctness is won or lost — more than the fetch plumbing.
- **Verify worker bug-reports.** G3 reported `history(latest:true)` as buggy; direct check
  showed it works → not propagated. (Discipline: validate platform claims before documenting.)
- A capable agent matches the guideline on easy questions; the guideline's value is
  **consistency + efficiency + correctness on the semantic traps** an unlucky agent misses.

## Remaining gaps (future rounds, lower new-mechanic value)

Short-cycling (ON→OFF transition counting), FDD multi-point boolean patterns, anomaly/outlier
(IQR/MAD), drift/period-over-period regression, true multi-collector/multi-site gridding
(only single-collector exercised; per-collector grid is documented but untested).

## For backend
See `evals/mcp_improvements.md` — the recurring tax is history extraction + semantic
guesswork; the proposed history-tool/duckdb-access direction would remove most of it, and
ideally expose meter/point semantics (cumulative vs interval, unit, cadence, import/export)
so agents don't infer them from the data.
