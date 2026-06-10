# skill-peak-runhours

Equipment run-hours analysis over PEAK 15-minute status history in Claude Chat. For a single site, returns a Gantt-style visual showing average daily start/stop times for central plant and AHU equipment against building working hours — so out-of-hours running is spottable at a glance.

## Pre-requisites

This skill requires the PEAK MCP connector to be installed and authenticated in Claude.

* MCP URL: `https://api.cimenviro.com/mcp`
* Auth: OAuth 2.0

In Claude, go to Settings > Connectors > Add custom connector, paste the URL above, and complete the OAuth sign-in with your PEAK account.

## Install

1. Click Code > Download ZIP from the Github repo
2. In Claude, go to Customize > Skills > Create Skill > Upload a skill
3. Upload the ZIP file as a skill

## Usage

From any Claude Chat session, run `/peak-runhours` and tell it which site to analyse. The skill defaults to central plant + AHU (VAV is excluded) unless you name a scope, analyses the past full week, and returns the run-hours visual with out-of-hours flagged.

For example:

* `/peak-runhours Skyline Tower`
* `/peak-runhours average run hours at 123 Main St, central plant only`

Large sites are pulled in chunks automatically. After the visual renders, ask follow-ups like "drill into CH-2 day by day" or "re-run for AHUs only" — no re-invocation needed.