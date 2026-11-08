---
title: "Template: Incident Declaration and First 15-Minute Triage Checklist"
incident_id: "TMPL-INC-002"
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
content_hash: "bd911c33f2ecdbc7df3fc7c97dcbb2b45db383ea1227f5a41a89a5036a70b6d3"
---

# Template: Incident Declaration and First 15-Minute Triage Checklist

## First 5 Minutes: Declaration & Roles
- [ ] Confirm issue is affecting production workloads or customer traffic.
- [ ] Declare incident in Slack: `/incident declare [P1/P2] <Title>`.
- [ ] Appoint Incident Commander (IC).
- [ ] Open dedicated incident bridge (Zoom/Meet).
- [ ] Assign Operations Lead to drive technical diagnostics.
- [ ] Assign Communications Lead to handle internal/external status updates.

## 5 to 10 Minutes: Triage & Blast Radius
- [ ] Identify which Kubernetes cluster and namespace are affected.
- [ ] Identify if recent deployment / rollout occurred within the last 60 minutes.
- [ ] Capture non-destructive evidence snapshot (logs, describe, events).
- [ ] Determine if blast radius is expanding or isolated.

## 10 to 15 Minutes: First Stakeholder Communication
- [ ] Post first status update to internal stakeholder channel.
- [ ] If P1, update public/customer status page.
- [ ] Formulate initial mitigation strategy (Rollback vs Scale vs Failover).
- [ ] Set timer for next status update (15 minutes for P1, 30 minutes for P2).
