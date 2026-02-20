# Healthcare GPT — Project Home (Private Repo)

## What we are building
A proof-of-concept pipeline for INL that:
1) collects authoritative public data on healthcare-sector disruptions, and
2) produces:
   - a compact dashboard JSON payload (machine-readable), and
   - a 1-page executive summary (human-readable),
   both with citations/provenance and confidence metadata.

## Current POC focus (this month)
We are starting with:
- medical device disruption events (cyber + natural hazard)
- medical device shortages (when directly tied to disruptions)
- hospital cyberattacks (as supporting “user-side” disruptions)

The goal is to show a minimal working example before scaling.

## What “done” looks like for the POC demo
- A populated disruption-event dataset (CSV) with evidence/provenance
- A small KPI table (CSV) with 5–10 starter mitigation metrics
- A dashboard JSON output that follows the fixed schema
- A short executive summary with citations and confidence

## Templates and schema
- Disruption Event CSV template: `data/templates/Disruption_Event_Master_Template.csv`
- KPI CSV template: `data/templates/Mitigation_KPI_Table_Template.csv`
- Dashboard JSON schema: `schemas/Sector_Risk_Dashboard_JSON_Schema_v1.json`

## Weekly expectations
Each week, we should:
- add new disruption events with provenance
- update a small set of KPIs for the pilot subsector
- refine our source registry (trusted sources we can crawl reliably)
- log questions for INL that clarify implementation requirements

## Next INL meeting prep
Please add at least two technical questions to:
- `docs/meeting-questions.md`

Examples:
- required JSON fields and expected roll-up logic (subsector → sector)
- whether BSU needs to build front-end UI or only JSON payloads
- whether INL will provide private GPU resources for model execution
- approved source list and any data-sharing constraints

## Security note
This is a private repo. Still, treat it as “clean”:
- no restricted/PCII/FOUO content
- public sources only unless INL provides explicit approval
- keep evidence snippets short and always cite the URL + accessed date