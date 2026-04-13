# Source Framework v1

## Purpose

This document organizes the expanded healthcare disruption source universe into a clearer framework that can support collection, schema development, and later JSON-based storage.

The goal is not to finalize one permanent source list, but to group sources in a way that makes them easier to use for:
- event discovery
- source validation
- resilience/context gathering
- later UI or dashboard organization

---

## Why This Matters

The project is moving beyond a narrow government-heavy source mix.

The source framework should help with:
- broader disruption coverage
- better balance across cyber, natural hazard, supply, and operational events
- richer metadata collection
- cleaner organization as the dataset grows

---

## Core Source Classes

### 1. Government / Official Sources
These are the strongest sources for official notices, public-health guidance, shortages, emergency response, and direct public-sector reporting.

Examples:
- FDA
- HHS
- ASPR
- CDC
- CMS
- CISA
- FEMA
- SEC EDGAR

Primary use:
- official notices
- shortages
- government advisories
- direct public reporting
- healthcare policy / response context

---

### 2. Healthcare Associations / Professional Groups
These sources help capture sector-wide operational concerns, supply issues, hospital logistics, and professional guidance.

Examples:
- AHA
- ASHP
- AMA
- AHRMM
- ASHRM
- Healthcare Ready
- Health-ISAC

Primary use:
- healthcare operations context
- supply chain and hospital logistics
- sector interpretation
- resilience-related commentary

---

### 3. Company / Manufacturer / Hospital / Vendor Sites
These sources are useful for direct reporting from affected organizations, manufacturers, hospital systems, and healthcare vendors.

Examples:
- Baxter
- B. Braun
- ICU Medical
- Medtronic
- Abbott
- Change Healthcare
- Optum
- Mayo Clinic
- HCA Healthcare
- Ascension

Primary use:
- direct disruption statements
- outage notices
- shortage/manufacturing issues
- operational updates
- organizational response

---

### 4. Healthcare Trade Press
These sources are useful for broader event discovery and healthcare-focused reporting that may connect disruptions to hospitals, manufacturers, and supply systems.

Examples:
- Becker’s Hospital Review
- Modern Healthcare
- Fierce Healthcare
- Healthcare Dive
- MedTech Dive
- Pharma Manufacturing

Primary use:
- event discovery
- healthcare-specific reporting
- additional operational context
- cross-checking company and government information

---

### 5. Cybersecurity / Technology Reporting
These sources are especially useful for cyber incidents, outages, ransomware, vendor compromise, and technology-related healthcare disruption.

Examples:
- CyberScoop
- The Record
- BleepingComputer
- Dark Reading
- SC Media
- SecurityWeek

Primary use:
- cyber event discovery
- incident details
- technical reporting
- timeline/context building

---

### 6. Major News + Local News
These sources are useful when disruptions are reported locally before official follow-up appears, or when a major event receives broad media coverage.

Examples:
- AP News
- Reuters
- NPR
- CNN
- NBC News
- major regional/local outlets

Primary use:
- early event detection
- local operational reporting
- broader coverage of major events

---

### 7. Research / Literature
These sources are useful for background context, resilience framing, historical analysis, and strategic interpretation rather than immediate event collection.

Examples:
- PubMed
- PMC
- Google Scholar
- RAND
- Brookings
- CSIS
- National Academies

Primary use:
- resilience framing
- historical patterns
- strategic interpretation
- literature support

---

### 8. Corporate / Financial Disclosure Sources
These sources are useful for exploring company risk disclosures, manufacturing issues, dependence risks, and possible disruption references.

Examples:
- SEC EDGAR
- SEC Company Filings Search
- Nasdaq
- MarketScreener

Primary use:
- company disclosures
- dependence / supplier concentration signals
- financial and operational risk language
- exploratory resilience information

---

### 9. News / Event Discovery Aggregators
These are useful for finding possible candidate events quickly, but they should not be treated as final authoritative sources on their own.

Examples:
- GDELT
- Google News
- Bing News
- Event Registry

Primary use:
- broad event discovery
- early signal detection
- identifying candidate events for follow-up

---

## Suggested Source Roles

### Discovery Sources
Use for finding possible events.
- healthcare trade press
- cyber reporting
- major/local news
- aggregators

### Validation Sources
Use for confirming the event and key facts.
- government / official sources
- company / hospital / vendor sites
- association statements when relevant

### Context Sources
Use for upstream, downstream, resilience, and strategic interpretation.
- research / literature
- associations
- trade press
- corporate disclosures

---

## Practical Collection Guidance

When possible, try to capture:
- source title
- source URL
- source class
- published date
- organization
- disruption type
- short event summary
- why it matters
- confidence
- raw text excerpt

Where evidence exists, also note:
- upstream dependency
- downstream impact
- resilience / mitigation notes
- strategic relevance

---

## Current Working Principle

This framework is intended to support broader and richer collection without locking the team into one final schema too early.

It should be treated as a working organizational layer that can be refined after more examples are collected and after additional INL feedback.
