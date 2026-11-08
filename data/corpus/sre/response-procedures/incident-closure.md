---
title: "Standard Procedure: Incident Closure and Handoff to Postmortem"
incident_id: "SRE-PROC-009"
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
content_hash: "04fb6dfa923de15ad08be18ef150ff6c842096dfbed7435e843ac7cab76dc4a6"
---

# Standard Procedure: Incident Closure and Handoff to Postmortem

## 1. Incident Closure Criteria
An incident may be closed ONLY when:
- Mitigation is verified.
- Mandatory soak period has elapsed without recurrence.
- All temporary workarounds are documented.
- Postmortem ticket has been created and assigned to an owner.

## 2. Final Closure Notification
Post to incident channel:
```markdown
INCIDENT RESOLVED: [P1/P2] <Title>
- Resolved at: <Timestamp UTC>
- Total Outage Duration: <X minutes>
- Final Mitigation: <Brief summary of how service was restored>
- Postmortem Owner: @username
- Postmortem Review Date: <Date within 5 business days>
```

## 3. Next Steps
The postmortem owner must draft the blameless postmortem within 48 hours using the standard template.
