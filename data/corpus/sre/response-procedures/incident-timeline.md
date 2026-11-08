---
title: "Standard Procedure: Incident Timeline Tracking and Log Recording"
incident_id: "SRE-PROC-005"
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
content_hash: "3d950c7a6ffadc9c29eb459edcc0b82942abec77ebbcacba6343084873314e13"
---

# Standard Procedure: Incident Timeline Tracking and Log Recording

## 1. Timeline Standard
All timestamps must be recorded in UTC in ISO-8601 format (`YYYY-MM-DD HH:MM:SS UTC`).

## 2. Key Milestones to Record
1. **T0 (Incident Inception):** When the underlying failure began (derived from metrics retrospectively).
2. **T_detect (Detection):** When alert fired or customer report arrived.
3. **T_declare (Declaration):** When incident was declared and IC assigned.
4. **T_mitigate (Mitigation):** When mitigation action took effect and customer impact ceased.
5. **T_resolve (Resolution):** When root cause was corrected and soak period ended.

## 3. Real-Time Timeline Format
```markdown
- 14:02 UTC: Prometheus alert KubePodCrashLooping starts firing for payment-service.
- 14:05 UTC: On-call acknowledges alert; declares P1 incident.
- 14:10 UTC: Ops Lead identifies Exit Code 137 (OOMKilled) after recent v2.4.1 release.
- 14:15 UTC: Rollback initiated to v2.4.0 via `kubectl rollout undo`.
- 14:20 UTC: Replicas achieve 1/1 READY; error rate drops to 0%.
- 14:35 UTC: 15-minute soak period completed; mitigation verified.
```
