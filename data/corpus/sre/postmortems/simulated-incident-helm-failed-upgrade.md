---
title: "Postmortem: Failed Helm Upgrade Leading to Ingress Misrouting and 502 Bad Gateway"
incident_id: "SIM-PM-003"
severity: "P1"
category: "postmortems"
component: "helm-ingress"
document_type: "postmortem"
doc_type: "postmortem"
source_type: "project-authored-sre"
applicable_versions: "Helm 3.x, Helm 4.x"
authoring_date: "2026-11-08"
spdx_license: "Apache-2.0"
attribution: "Enterprise SRE Copilot Authors"
content_hash: "8045f36c50e441facd86a0cc032113b0bc4656f5659c944ee7b2461e660486cd"
related_upstream_urls:
  - "https://helm.sh/docs/helm/helm_upgrade/"
---

# Postmortem: Failed Helm Upgrade Leading to Ingress Misrouting and 502 Bad Gateway

> [!IMPORTANT]
> **SIMULATED INCIDENT — NOT A REAL PRODUCTION EVENT.**
> This incident postmortem is an educational case study created for operational training and automated SRE guidance. All timestamps, incident logs, service names, and metrics are simulated.

## 1. Incident Summary
On 2026-10-19 at 16:04 UTC, a production Helm upgrade of the `customer-portal` chart failed midway due to an immutable field conflict on a Service resource. Ingress controller retained routes to old deleted endpoints while new pods failed to start, returning HTTP 502 Bad Gateway. Total duration was 19 minutes. Mitigated by executing `helm rollback customer-portal 14 -n portal`.

## 2. Customer and Service Impact
- **Impacted Domain:** `portal.example.com`
- **Duration:** 19 minutes (16:04 UTC to 16:23 UTC).
- **User Impact:** 100% of web portal users received HTTP 502 error pages.

## 3. Incident Timeline (UTC)
- **16:02 UTC:** CD pipeline triggered `helm upgrade customer-portal ./charts/customer-portal -n portal -f values-prod.yaml`.
- **16:04 UTC:** Helm failed with error: `cannot patch "customer-portal-svc" with kind Service: field is immutable: spec.clusterIP`.
- **16:06 UTC:** Release entered `failed` state; Ingress controller reloaded configuration pointing to orphaned port.
- **16:08 UTC:** On-call alerted to 502 Bad Gateway errors; P1 declared.
- **16:14 UTC:** SRE investigated revision history: `helm history customer-portal -n portal` showed revision 15 `failed`, revision 14 `deployed`.
- **16:17 UTC:** SRE executed emergency rollback: `helm rollback customer-portal 14 -n portal`.
- **16:21 UTC:** Revision 14 restored healthy Service and Ingress configuration.
- **16:23 UTC:** Traffic verified healthy; incident closed after soak period.

## 4. Root Cause
In chart version v3.2.0, a developer attempted to clear `spec.clusterIP` in the Service template to allow Kubernetes to regenerate it. In Kubernetes, `spec.clusterIP` is an immutable field that cannot be changed once allocated. The API server rejected the patch, causing Helm upgrade to terminate abruptly.

## 5. Corrective Actions Taken
- [x] Restored service via `helm rollback`.
- [x] Fixed chart template to preserve existing `.spec.clusterIP` or omit field during upgrades.
- [x] Added `helm diff upgrade` step to CI pipeline to fail builds that attempt to alter immutable fields.
