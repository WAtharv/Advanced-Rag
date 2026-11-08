---
title: "Standard Procedure: Initial Triage and Role Handover Protocol"
incident_id: "SRE-PROC-003"
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
content_hash: "85f7d629d759073ff39ddf5fb761de49ad32402947d75f571dc12450b7ad00b6"
---

# Standard Procedure: Initial Triage and Role Handover Protocol

## 1. Incident Command Roles
For P1 and complex P2 incidents, the following roles must be assigned:
1. **Incident Commander (IC):** Leads response, coordinates actions, assigns tasks, protects responders from distractions. Does NOT troubleshoot directly.
2. **Operations Lead (Ops Lead):** Executes diagnostics, reads telemetry, implements remediation actions.
3. **Communications Lead (Comms Lead):** Writes status updates, notifies stakeholders, coordinates public status page.
4. **Scribe:** Maintains the chronological incident timeline, logs commands executed, and records findings.

## 2. Role Handover Protocol
Handovers must occur verbally on the bridge and be recorded in the incident channel:
```markdown
ROLE HANDOVER:
- Outgoing IC: @alice
- Incoming IC: @bob
- Current Status: Mitigation in progress (rollout undo initiated)
- Next Action: Awaiting replica readiness verification
- Accepted by: @bob at 14:32 UTC
```
