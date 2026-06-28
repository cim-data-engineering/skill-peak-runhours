# Experiment 1 — run-hours via PEAK MCP: 3-arm eval

Question: *"What are the equipment run hours at 99 Elizabeth St for the last week?"*
Arms: **A** bare MCP · **B** bare MCP + GQL glossary · **C** skill emulation
(reads SKILL.md + `ooh_status_metadata_reference.md`, runs `render_runhours.py`).
All counts verified from sub-agent transcripts.

## Runs

| Run | Arm | MCP calls | Schema-disc. | Failed | Equip covered | Deduped HLI | Offload trick | Chart | Gave up |
|-----|-----|----:|----:|----:|----:|:--:|:--:|:--:|:--:|
| A1 | control | 12 | 3 | 1 | **2** | n/a | ✗ | no | partial |
| A2 | control | 17 | 3 | 0 | 59 | ✗ | ✓ | no | no |
| B1 | glossary | 13 | 2 | 1 | 44 | ✗ | ✓ | no | no |
| B2 | glossary | 11 | 5 | 1 | 35 | ✗ | ✓ | no | no |
| **C2** | **skill** | **8** | **0** | **0** | **56** | **✓** | ✓ (scripted) | **yes** | no |

CH-01 run hours ≈ 11.7–11.8h in every run → cross-validated, the method is sound.

## Findings

**1. The glossary did not measurably help.** It targets GQL field/arg mechanics,
but the arms rarely got stuck there: A2 made **0** failed calls *without* it;
B2 made 1 *with* it. Schema-discovery counts overlap (control 3 / glossary 2–5).
For run-hours-shaped questions the glossary is not the lever.

**2. Bare MCP (A/B) is wildly high-variance — this is the real story.**
The control arm ranged from *gave up at 2 equipment* (A1) to *59 equipment, clean*
(A2). The variance is driven by two things the glossary doesn't touch:
   - **Did the agent discover the "offload trick"?** Oversized `execute_graphql_query`
     responses are auto-saved to a file instead of dumped into context. A1 never
     realised this and died on the response-size cap; every other run found it and
     used local Python over the files. This single discovery determines whether the
     run succeeds — and it's luck.
   - **Did the agent reason correctly about which "status" points are run-status?**
     A naive `metadata_name LIKE '%status%'` returns ~395–397 points, most of which
     (filter status, lockout, VAV cooling/active, poll) are *not* run signals.
     Each bare arm had to re-derive the exclusions by hand, with different results.

**3. The skill (C) removes the variance and wins on every axis.**
Fewest calls (8), **zero** schema discovery (SKILL.md gives exact queries), zero
errors, and the **only arm that selected fav_ids correctly**: deterministic from the
reference table's tier-1 `metadata_id`s and properly **deduped CH-01-HLI** — which
*all four* bare runs left in (A2 even flagged it in prose, then listed it anyway).
It also produced the actual deliverable (the Gantt SVG). Its efficiency and
correctness don't depend on the agent getting lucky.

> So the two bottlenecks resolve to two *different* aids, and neither is the glossary:
> - (a) *which* points are run-status → the **reference metadata_id table**
> - (b) history volume / context → the **scripted offload+duckdb-style pipeline**
> The skill bundles both; bare agents reinvent (b) by luck and never get (a) clean.

## Skill bugs surfaced by arm C (fix regardless of eval outcome)

1. **SKILL.md's unwrap snippet is wrong.** It documents
   `json.loads(json.load(open(path))[0]["text"])["results"]`, but the offloaded
   payload arrived as a plain dict `{results, …}`, not a list-of-text-blocks.
   A real `/peak-runhours` run could crash on that line. → make the unwrap shape-robust.
2. **Shared offload dir across concurrent sessions.** Foreign `fav_id`/`ts` files
   were present; C filtered to its own fav_ids + dedup by `(fav_id, ts)`. Partly an
   artifact of running arms concurrently, but a production hazard. → skill should
   filter offloaded files to its own fav_id set.

## Eval-harness lessons

- **N=1 is meaningless for the bare arms** — the offload-trick coin-flip dominates.
  Need ≥3 replicates per bare arm to characterise the distribution.
- **Run arms sequentially (or isolate output dirs)** — concurrent arms contaminated
  the shared offload dir and confound the "rows into context" metric.
- A useful future metric: **context tokens consumed** (true cost of pulling rows),
  not just call count. Pull from transcript usage fields.

## So what

This validates the Experiment-2 thesis directly: the win is a **mandated pipeline**
(reference-driven fav_id selection → bulk fetch to disk → script/duckdb → summary
+ chart), exactly what arm C does. The generic glossary is necessary hygiene for
arbitrary GQL questions but is not what makes run-hours fast or correct.
