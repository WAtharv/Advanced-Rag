---
title: "Template: Operational Incident Tracking and Handover Document"
incident_id: "TMPL-INC-001"
severity: "Standard"
category: "templates"
component: "templates"
document_type: "template"
doc_type: "template"
source_type: "project-authored-sre"
applicable_versions: "All Environments"
authoring_date: "2026-11-08"
spdx_license: "Apache-2.0"
attribution: "Enterprise SRE Copilot Authors"
content_hash: "439c3e6a4ad7e0124b6c9111fef9075906524a3a49c45c20937186e2f17915bb"
---

# Template: Operational Incident Tracking and Handover Document

## 1. Incident Overview
- **Incident Title:** [P1/P2/P3] <Descriptive Title>
- **Incident ID:** INC-YYYYMMDD-XXX
- **Current Severity:** [P1 / P2 / P3 / P4]
- **Current State:** [Investigating / Mitigating / Monitoring / Resolved]
- **Incident Commander (IC):** @name
- **Operations Lead:** @name
- **Communications Lead:** @name
- **Scribe:** @name
- **Incident Channel:** `#incident-YYYYMMDD-name`
- **Bridge Link:** `<url>`

## 2. Impact Summary
- **Impacted Services:** `<service-names>`
- **Impacted Regions / Clusters:** `<regions>`
- **Customer Experience:** `<What are users seeing? Error codes, latency, timeouts>`
- **SLO Breach:** `[Yes / No - Details]`

## 3. Current Hypotheses and Investigation Log
| Timestamp (UTC) | Investigator | Observation / Hypothesis | Command / Action Taken | Outcome |
| :--- | :--- | :--- | :--- | :--- |
| HH:MM | @name | E.g. Pods failing OOM | `kubectl describe pod` | Exit code 137 confirmed |

## 4. Remediation Plan
- [ ] Action 1: `<Immediate mitigation>`
- [ ] Action 2: `<Verification step>`
- [ ] Action 3: `<Rollback contingency>`

## 5. Communications Log
- **HH:MM UTC:** Initial declaration posted to #general-announcements.
- **HH:MM UTC:** Public status page updated.
