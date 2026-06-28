# Peak MCP run-hours eval

Investigation branch: isolate the bottleneck in answering time-series questions
through the **generic PEAK MCP** (GraphQL) surface, with no skill loaded — i.e.
the experience an off-the-shelf MCP client (cowork etc.) gets today.

Sub-agent replay approach, modelled on `agent-toni`'s `bot-eval` skill, **with one
inversion**: there, workers are *banned* from MCP (the prod bot has none); here,
workers **must** use the PEAK MCP and nothing else — the MCP-on-GQL path is the
system under test. No `peak` CLI, no web.

## Experiment 1 — does the GQL glossary fix fav_id discovery?

Bottleneck hypothesis (a): a generic agent struggles to find the right
`fav_id`s (timeseries channels) for a question because the GraphQL schema has
non-obvious naming/traversal rules (display-name field varies by type, no flat
`site_id`, instance-vs-type split, args-vs-fields). It burns calls on schema
discovery and failed field/arg selections.

**A/B**, same question, same live data, back-to-back:

| Arm | Condition |
|-----|-----------|
| A (control) | generic agent + PEAK MCP, no skill, no glossary |
| B (glossary) | identical + `peak_gql_glossary.md` injected into the prompt |

Question: *"What are the equipment run hours at 99 Elizabeth St for the last week?"*

### Metrics (per worker)
- `mcp_call_count` — total MCP tool calls
- `schema_discovery_calls` — describe_/list_ calls (pure overhead)
- `failed_calls` — field/arg errors, 5xx, gateway timeouts + retries
- `fav_ids_found` / `equipment_covered` — did it find the right channels?
- `gave_up`, wall-time, and correctness of the final answer

Self-reported JSON is cross-checked against the sub-agent transcript
(`agent-*.jsonl`) for true call counts.

### Hypothesis
Arm B makes fewer schema-discovery + failed calls and reaches a correct fav_id
set faster. If the glossary does *not* move the needle, the bottleneck is
elsewhere (part b: row-by-row history pull into context), which Experiment 2
will target.

Results land in `results/`.
