# Experiment 2 — the timeseries-analysis guideline

Tested the draft `guidelines/timeseries-analysis.md` on a **non-run-hours** question
(to prove it generalises beyond the run-hours skill):
*"At 99 Elizabeth St, typical weekday hourly-average AHU supply air temperature, last week."*

Arms: **G** = generic agent following the guideline · **A'** = bare MCP, no guideline.
Counts verified from transcripts.

| Metric | A' bare | G guideline |
|---|---:|---:|
| MCP calls | 21 | **9** |
| Schema-discovery calls | 5 | **2** |
| Failed calls | 0 | 1 |
| fav_ids found | 43 | 43 |
| Gridded to cadence | yes | yes |
| Interval source | **inferred empirically** | **`collection_interval` + `fav_id>>31`** |
| DuckDB / Parquet | yes | yes (SQL ran verbatim) |
| Raw rows in context | no | no |
| 05/10/13/23h values (°C) | 20.63 / 25.28 / 21.92 / 21.54 | 20.63 / 25.28 / 21.92 / 21.54 |

## Findings

1. **Both correct — values identical to 2 dp** (independent cross-validation; the method
   is sound, the live answer is real: SAT ~20.6 °C pre-dawn → ~25 °C late-morning peak).
2. **The guideline ~halves the cost and removes the guesswork.** G used 9 calls vs 21,
   2 schema-discovery vs 5, and read the interval deterministically
   (`collection_interval=PT15M`, collector confirmed via both `equipment.collector_id`
   and `fav_id>>31`) instead of inferring it from jittery COV timestamps. Combined with
   Exp1 (where the bare arm A1 *failed outright*), the guideline's value is **consistency
   + efficiency**, not peak-case correctness — a capable bare agent can match it, an
   unlucky one can't.
3. **The validated DuckDB gridding SQL + Parquet path ran verbatim** in a live worker —
   no `AT TIME ZONE`/`pytz` issue, `zoneinfo` conversion correct, perfect sample
   completeness (860 buckets/hour = 43 AHUs × 5 weekdays × 4 quarter-hours, no gaps).

## Guideline defects found and fixed

1. **Point-selection args-vs-fields** — `metadata_code`/`metadata_name` are filter *args*
   only; to read the class you subselect `metadata.code/name/system`. Cost G one failed
   call. Fixed: explicit args-vs-fields note + corrected Step 2 example.
2. **"528 KB known-good" was wrong** — see gateway probe below. Fixed with measured
   numbers + "offload is the normal path".

## Gateway behaviour — measured (overrides the SKILL.md framing)

A dedicated probe swept `platform.history` from 1→43 fav_ids and 7→37 days
(`exp2_gateway_probe.json`):

- **Inline → offload at ~45–95 KB** (~1 fav × 1 week). A *harness/client token cap*, not
  the gateway. Above it, results are **gracefully saved to a file** — harmless, expected,
  the normal path. NOT an error.
- **Gateway origin ceiling ~2–10 MB.** Largest success: 43 favs × 7 days =
  **1,999,691 bytes / 28,889 rows**. At ~10 MB (43 × 37 days) it **hard-fails with a
  Cloudflare 502** (`origin_bad_gateway`, retryable, `retry_after 60`) — a transport
  error, not clean truncation.
- **Size is the binding constraint, not the 30 s timeout** — nothing neared 30 s (max
  ~17 s real). One call safely covers **~25–30 k rows / ~2 MB** (e.g. all 43 AHU points ×
  7 days). The skill's "~11 points × 7 days / 528 KB" is **~2.5× too conservative**.
- Future-dated `end` does not grow payload (data stops at ~now); widen via `start`.

## Follow-up

The same wrong "528 KB / chunk above ~12 points" framing is in `SKILL.md` (now on `main`).
Worth a follow-up PR correcting it to "one call covers all status points × 7 days; chunk
only above ~2 MB / ~25 k rows; the limit is size not the 30 s timeout; a 502 means narrow
and retry."
