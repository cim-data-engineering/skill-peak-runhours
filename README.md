# skill-peak-runhours

Equipment run-hours analysis over PEAK 15-minute status history in Claude Chat. For a single site, returns a summary table, Excel workbook, and Gantt-style visual showing average daily start/stop times for central plant and AHU equipment against building working hours — so out-of-hours running is spottable at a glance.

## Pre-requisites

This skill requires the PEAK MCP connector to be installed and authenticated in Claude.

* MCP URL: `https://api.cimenviro.com/mcp`
* Auth: OAuth 2.0

In Claude, go to Settings > Connectors > Add custom connector, paste the URL above, and complete the OAuth sign-in with your PEAK account.

The xlsx skill must also be enabled (used to build the workbook).

## Install

1. Click Code > Download ZIP from the Github repo
2. In Claude, go to Customize > Skills > Create Skill > Upload a skill
3. Upload the ZIP file as a skill

## Usage

From any Claude Chat session, run `/peak-runhours` and tell it which site to analyse. The skill confirms the equipment scope before pulling data (default: central plant + AHU — VAV is excluded), analyses the past full week, and returns the run-hours table, workbook, and Gantt visual with out-of-hours flagged.

For example:

* `/peak-runhours Skyline Tower`
* `/peak-runhours average run hours at 123 Main St, central plant only`

If the site resolves to more than 30 status points, the skill asks to narrow the scope (e.g. by level or equipment type) before pulling history. After the outputs render, ask follow-ups like "drill into CH-2 day by day" or "re-run for AHUs only" — no re-invocation needed.