---
title: "Standard Procedure: Incident Declaration and Initial Assessment"
incident_id: "SRE-PROC-001"
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
content_hash: "cd1a9aee0f420fe58957f50b3e92c607f61a945977b65179ddec32cbd98bdcc9"
---

# Standard Procedure: Incident Declaration and Initial Assessment

## 1. Purpose
Define the mandatory operational steps for declaring an active production incident, establishing initial communication channels, and appointing the Incident Commander (IC).

## 2. Trigger Criteria
An incident MUST be formally declared when any of the following occur:
- Automated P1/P2 alert fires without auto-recovery within 5 minutes.
- User-facing error rate exceeds SLA threshold (>1% 5xx errors for >3 minutes).
- Security breach, unauthorized access, or credential exposure is detected.
- External dependency outage halts payment, checkout, or core user journeys.

## 3. Initial Assessment (First 5 Minutes)
1. **Confirm Production Impact:** Is the issue affecting live users, data integrity, or core SLAs?
2. **Determine Initial Scope:** Which services, regions, and clusters are degraded?
3. **Appoint Incident Commander (IC):** The on-call engineer who acknowledges the page assumes the role of IC until explicitly handed off.

## 4. Declaration Actions
Execute within 5 minutes of confirmation:
```bash
# 1. Open dedicated incident bridge (Zoom/Meet/Slack)
# Format: #incident-<YYYYMMDD>-<service-name>

# 2. Post Initial Declaration Message:
# "INCIDENT DECLARED: [P1/P2] <Short Description>
#  Incident Commander: @username
#  Bridge: <link>
#  Impacted Services: <service-list>
#  Initial Symptoms: <brief bullet points>"
```

## 5. Escalation Rules
If impact is confirmed as P1, immediately notify the Incident Commander On-Call and Engineering Lead.
