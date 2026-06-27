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

## Hardening plan (remaining rounds, highest new-mechanic value first)
- **Round 4 — energy/demand**: daily kWh + peak kW. New: cumulative-counter diff, max, units.
- **Round 5 — delta-T / efficiency**: CHW supply−return delta-T (or chiller kW/ton). New:
  multi-point per-bucket arithmetic, ratios, units.
- (Optional) Round 6 — short-cycling: transition counting on gridded binary.
