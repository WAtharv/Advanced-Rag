---
title: "Standard Procedure: Stakeholder Communications and Escalation Matrix"
incident_id: "SRE-PROC-007"
severity: "Standard"
category: "response-procedures"
component: "incident-framework"
document_type: "procedure"
doc_type: "procedure"
source_type: "project-authored-sre"
applicable_versions: "All Environments"
authoring_date: "2026-11-08"
spdx_license: "Apache-2.0"
attribution: "Enterprise SRE Copilot Authors"
content_hash: "a469d72fa358e15fd219e51017ac9d16104f04585a9b9d3b0cfb5df9566263e5"
---

# Standard Procedure: Stakeholder Communications and Escalation Matrix

## 1. Stakeholder Communication Cadence
- **P1 Incidents:** Internal update every 15 minutes; public status page update within 15 minutes of declaration.
- **P2 Incidents:** Internal update every 30 minutes; public status page if user-visible.

## 2. Standard Status Update Template
```markdown
CURRENT STATUS UPDATE: [INVESTIGATING / MITIGATING / MONITORING / RESOLVED]
- Incident: [P1/P2] <Title>
- Summary: <1-2 sentences on what is happening>
- Customer Impact: <% of users affected, specific error symptoms>
- Current Actions: <What the team is actively doing right now>
- Next Update: <Time in UTC, e.g. 15:00 UTC>
```

## 3. Escalation Contacts
- Secondary On-Call: Auto-escalated after 10 minutes of unacknowledged page.
- Engineering Manager: Escalated after 30 minutes of unresolved P1.
- VP / Executive On-Call: Escalated after 60 minutes of ongoing P1 outage.
