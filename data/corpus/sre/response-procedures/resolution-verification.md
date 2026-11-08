---
title: "Standard Procedure: Post-Mitigation Verification and Soak Period Standards"
incident_id: "SRE-PROC-008"
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
content_hash: "0b855b110aae584bcaa7551a545ea6715f01a325162f5f6a74d8eec7c8303daa"
---

# Standard Procedure: Post-Mitigation Verification and Soak Period Standards

## 1. Purpose
Prevent premature incident closure by enforcing rigorous verification and a mandatory observational soak period.

## 2. Mandatory Verification Steps
Before declaring an incident mitigated:
1. **User-Facing Telemetry:** 5xx error rates must return below 0.1% baseline across all endpoints.
2. **Latency Metrics:** P95 and P99 latency must return to pre-incident baseline.
3. **Synthetic Canaries:** End-to-end synthetic user transactions must succeed consecutively for 5 runs.
4. **Workload Health:** All deployment pods must report `1/1 READY` with 0 crashlooping restarts.

## 3. Mandatory Soak Period
- **P1 Incidents:** Minimum 30 minutes of continuous normal operation before closing the incident.
- **P2 Incidents:** Minimum 15 minutes of continuous normal operation before closing.

If symptoms recur during the soak period, the incident timer resets immediately.
