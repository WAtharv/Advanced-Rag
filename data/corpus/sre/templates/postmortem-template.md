---
title: "Template: Blameless Postmortem and Root Cause Analysis"
incident_id: "TMPL-INC-003"
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
content_hash: "aba3bd3284a2f14dea1d50f3c84194c56642660cc5c4d78b1791a421ea470595"
---

# Template: Blameless Postmortem and Root Cause Analysis

## Incident Summary
- **Incident Title:** <Title>
- **Date of Incident:** YYYY-MM-DD
- **Severity:** P1 / P2
- **Total Duration:** <Hours:Minutes>
- **Incident Commander:** @name
- **Postmortem Author:** @name
- **Service(s) Impacted:** `<services>`

## Executive Summary
<2-3 concise paragraphs explaining what happened, the customer impact, how it was mitigated, and the primary root cause. Written for technical leadership.>

## Customer & Business Impact
- **User Impact:** `<e.g. 14,000 users experienced 500 Internal Server Error during checkout>`
- **Error Rate:** `<Peak error rate percentage>`
- **SLO Impact:** `<Error budget consumed, e.g. 42% of monthly budget>`

## Timeline (UTC)
| Timestamp (UTC) | Event Description |
| :--- | :--- |
| **YYYY-MM-DD HH:MM** | T0: Underlying failure begins (retrospectively identified) |
| **YYYY-MM-DD HH:MM** | Automated alert fires |
| **YYYY-MM-DD HH:MM** | Incident declared; IC assigned |
| **YYYY-MM-DD HH:MM** | Mitigation action initiated |
| **YYYY-MM-DD HH:MM** | Customer impact ceases; service restored |
| **YYYY-MM-DD HH:MM** | Soak period ends; incident closed |

## Root Cause Analysis (5 Whys)
1. **Why did the service fail?** <Answer>
2. **Why?** <Answer>
3. **Why?** <Answer>
4. **Why?** <Answer>
5. **Why?** <Answer (Root Cause)>

## Contributing Factors
- Trigger: `<What triggered the failure today?>`
- Detection Gap: `<Why didn't automated testing or monitoring catch this earlier?>`
- Architectural Factor: `<What system design allowed this failure to cascade?>`

## What Went Well / What Went Poorly
### What Went Well
- Quick rollback execution restored service within 10 minutes of identification.
- Automated alerts notified on-call within 2 minutes.

### What Went Poorly
- Monitoring lacked container memory saturation alerts prior to OOM kill.
- Initial triage was delayed due to missing preStop hooks.

## Action Items (Preventative Measures)
| Action Item | Type | Priority | Owner | Target Date | Tracking Ticket |
| :--- | :--- | :--- | :--- | :--- | :--- |
| Add memory limit alert | Prevent | High | @sre | YYYY-MM-DD | JIRA-101 |
| Implement startupProbe | Mitigate | Med | @dev | YYYY-MM-DD | JIRA-102 |
| Load test new release | Detect | High | @qa | YYYY-MM-DD | JIRA-103 |
