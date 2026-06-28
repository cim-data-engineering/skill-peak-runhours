# What analyses does a building-performance engineer run on PEAK time-series?

Grounded in the skills CIM ships (`peak-skills`: trend-analyzer, thermal-comfort,
equipment-health-analyzer, commissioning-auditor, alert-investigation, integration-analyzer)
and `docs/domain-knowledge.md` (working hours, equipment types, FDD fault patterns, unit
conversion, stagnancy). Used to prioritise hardening the timeseries guideline.

Legend: **mechanic** = what the pipeline must do; **status** = covered / gap / tested.

| # | Analysis | Mechanic it stresses | Status |
|---|---|---|---|
| 1 | Run hours / OOH / start-stop | binary ON integration, working-hours mask, typical-ON envelope | ✅ Exp1 (skill) |
| 2 | Daily/hourly average profile | grid → avg by hour/day, weekday/weekend split | ✅ Exp2 G |
| 3 | Setpoint tracking / control perf | pair two classes by equipment, corr + MAE, constant-signal | ✅ Exp2 G2 |
| 4 | Data quality: stalled / dead / flat-line | cheap `history_available` probe, scope whole site, variance, flat≠fault | ✅ Exp2 G3 |
| 5 | **Energy / demand** | cumulative-counter diff vs interval-SUM, peak=max(kW), units | ✅ Round 4 |
| 6 | **Plant efficiency / delta-T / COP** | multi-point per-bucket arithmetic, operating mask, derived units | ✅ Round 5 |
| 7 | Thermal comfort / % time in band | threshold counting vs comfort band, WH mask, zone temp | covered-by-pattern (threshold + WH mask) |
| 8 | Short-cycling / excessive starts | count ON→OFF transitions per day on gridded binary (run-length) | gap (future) |
| 9 | FDD fault patterns (simul heat+cool, hunting, economiser) | multi-point boolean logic per bucket, hysteresis | gap (future) |
| 10 | Anomaly / outlier / spike / step | robust stats (IQR/MAD), step detection | gap (future) |
| 11 | Drift / week-over-week / seasonal | period-over-period grouping, regression slope | gap (future) |
| 12 | Cross-equipment / cross-site rollup | many collectors/intervals, multi-site tz | partial (single collector tested; per-collector grid documented) |

## Cross-cutting concerns the guideline must get right everywhere
- **Units.** Points carry `metadata.unit`; sites can be Imperial or Metric (domain-knowledge
  has an Imperial/Metric section). Aggregates over mixed units are wrong, and reports need
  the unit. → carry `metadata.unit` in Step 2; flag mixed units. **(currently a gap)**
- **Cumulative vs instantaneous.** Energy (kWh) and runtime counters are cumulative — diff
  consecutive readings; watch counter resets/rollover. Power (kW), temps are instantaneous.
- **Multi-collector / multi-interval.** Grid per collector group (`fav_id>>31`); BACER
  virtual points at 1-min.
- **Working-hours masking** recurs (run-hours, comfort, OOH energy).
- **Value semantics** (ON=1 vs ON=2; °C vs °F; cumulative vs rate) must be detected, not
  assumed.

## Cross-cutting additions tested in rounds 6-8
- **Charting** (matplotlib + numpy, no scipy/pandas): histogram + hand-rolled KDE, scatter +
  `polyfit`, derived-series line, per-entity dotplot. Now a guideline section.
- **Robustness**: empty-window→last-available re-anchor (a site's data ended ~10 months ago);
  sentinel/out-of-range screening before stats (4% sentinels wrecked one distribution);
  working-hours/weekday masking.
- **Calculated series / graceful degradation**: COP impossible (no flow point) → labelled
  proxy; magnitude sanity-check (scaled HLI power point).
- **Reset/relationship**: slope (`polyfit`) as headline + cycling-cloud interpretation +
  dynamic-range caveat.

## Status after rounds G–G8 + timing
Covered: 1-7 (run-hours, profile, correlation, DQ, energy, delta-T, comfort-band/distribution).
**Timing**: guideline did run-hours in **4 MCP calls vs the original skill's 8** (more
call-efficient; skill's edge is baked-in reasoning + the Gantt).

## Still genuinely untested (every tested pair was single-collector, 15-min)
- **Multi-collector / mixed-cadence gridding** and **asymmetric cross-cadence pairing**
  (1-min BACER vs 15-min) — documented in the guideline but never exercised; 99 Eliz's 5-min
  meter collector (79) is the natural target.
- Short-cycling (ON→OFF transition counting), FDD multi-point boolean patterns, anomaly/outlier
  (IQR/MAD), drift/period-over-period regression.
