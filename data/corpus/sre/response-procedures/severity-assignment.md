---
title: "Standard Procedure: Severity Assignment Matrix (P1-P4) and SLA Standards"
incident_id: "SRE-PROC-002"
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
content_hash: "c3c40492ed70c2601908c1d4279d0dc5879d8013782d0ed1c8568a7b8446c07f"
---

# Standard Procedure: Severity Assignment Matrix (P1-P4) and SLA Standards

## 1. Severity Classification Matrix

| Severity Level | Definition | Impact Scope | Response SLA | Update Cadence |
| :--- | :--- | :--- | :--- | :--- |
| **P1 (Critical)** | Core service down; catastrophic data loss; critical security breach. | >25% of users impacted or core transaction pipeline halted. | Immediate (<5 min page ack) | Every 15 minutes |
| **P2 (High)** | Major functionality degraded; no immediate workaround; redundancy lost. | 5-25% of users impacted; single AZ failure; database secondary down. | <15 min page ack | Every 30 minutes |
| **P3 (Medium)** | Minor feature impacted; viable workaround exists; internal tooling broken. | Non-critical path or isolated subset (<5%) of users. | <1 hour response | Every 2 hours |
| **P4 (Low)** | Cosmetic issue, minor bug, non-urgent maintenance alert. | No end-user impact. | Next business day | On resolution |

## 2. Severity Reclassification
- **Upgrading:** If a P2 incident shows signs of cascading failure or user complaints surge, the IC must immediately upgrade to P1.
- **Downgrading:** Severity can be downgraded only after mitigation is confirmed and error rates return to normal.
