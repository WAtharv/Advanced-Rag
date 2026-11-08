---
title: "Standard Procedure: Mitigation Strategies, Rollbacks, and Safe Restoral"
incident_id: "SRE-PROC-006"
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
content_hash: "5a632f7a65952971570880f3731a2e8484d8a1c556d97d82661d34d2ff24b32c"
---

# Standard Procedure: Mitigation Strategies, Rollbacks, and Safe Restoral

## 1. Philosophy: Mitigate First, Root-Cause Later
During an active P1/P2 incident, the sole objective is to restore customer service as rapidly and safely as possible. Deep root cause analysis occurs during the postmortem.

## 2. Standard Mitigation Hierarchy
Order of preference for rapid mitigation:
1. **Rollback Recent Deployment:** Revert code or config change to last known healthy state.
2. **Scale Up Compute Capacity:** Add replicas or worker nodes to absorb traffic pressure.
3. **Route Traffic Away (Failover):** Divert ingress traffic to alternate healthy region or cluster.
4. **Shed Load / Degrade Gracefully:** Disable non-critical features via feature flags (e.g. recommendation engine, search autocomplete).
5. **Restart Degraded Component:** Only after capturing evidence.

## 3. Rollback Safety Rules
- Verify database backwards compatibility before rolling back application code.
- If database migration cannot be rolled back, use forward-fix or feature flag disabling.
