---
title: "Postmortem: Memory Leak and OOMKilled Cascade in Payment Processing Pods"
incident_id: "SIM-PM-001"
severity: "P1"
category: "postmortems"
component: "workload-memory"
document_type: "postmortem"
doc_type: "postmortem"
source_type: "project-authored-sre"
applicable_versions: "Kubernetes 1.28-1.37"
authoring_date: "2026-11-08"
spdx_license: "Apache-2.0"
attribution: "Enterprise SRE Copilot Authors"
content_hash: "333d59c559564a789ea673db674cf5b2bd5e32523be900f5b3b2f31b4af72bb0"
related_upstream_urls:
  - "https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/"
---

# Postmortem: Memory Leak and OOMKilled Cascade in Payment Processing Pods

> [!IMPORTANT]
> **SIMULATED INCIDENT — NOT A REAL PRODUCTION EVENT.**
> This incident postmortem is an educational case study created for operational training and automated SRE guidance. All timestamps, incident logs, service names, and metrics are simulated.

## 1. Incident Summary
On 2026-09-14 at 14:12 UTC, the `payment-gateway` service experienced a cascading failure across all 12 replica pods in production cluster `us-east-prod`. Incoming checkout requests failed with HTTP 502 Bad Gateway. Total customer downtime was 28 minutes. The incident was mitigated by rolling back the application deployment from v2.4.1 to v2.4.0.

## 2. Customer and Service Impact
- **Impacted Service:** `payment-gateway` (Order processing and authorization).
- **Duration:** 28 minutes (14:12 UTC to 14:40 UTC).
- **Failed Requests:** Approximately 8,400 checkout transactions failed.
- **SLO Breach:** Consumed 34% of monthly 30-day availability error budget.

## 3. Incident Timeline (UTC)
- **13:45 UTC:** Release v2.4.1 deployed to production via automated CD pipeline.
- **14:02 UTC:** Application memory consumption began climbing linearly across all replicas at ~45MB/min.
- **14:12 UTC:** First replica hit container limit (1Gi) and was terminated by kernel OOM killer (exit code 137).
- **14:14 UTC:** Remaining replicas absorbed redirected traffic, accelerating memory growth. All replicas entered `CrashLoopBackOff` in cascade.
- **14:16 UTC:** PagerDuty P1 alert `PaymentGatewayHigh5xxErrorRate` fired; on-call engineer acknowledged page.
- **14:20 UTC:** Incident declared; Incident Commander opened incident bridge.
- **14:25 UTC:** Ops Lead inspected previous container logs and pod termination reasons, confirming `OOMKilled exit code 137`.
- **14:28 UTC:** Decision taken to rollback to v2.4.0 immediately.
- **14:32 UTC:** `kubectl rollout undo deployment/payment-gateway -n payments` executed.
- **14:36 UTC:** New v2.4.0 pods passed readiness probes; traffic resumed.
- **14:40 UTC:** Error rate dropped below 0.05%; mitigation verified.
- **15:10 UTC:** 30-minute observational soak period ended; incident closed.

## 4. Root Cause
In release v2.4.1, an internal caching layer was introduced to store idempotency keys. The cache implementation utilized an unbounded Go `sync.Map` without an eviction policy (TTL or LRU). Under peak production load, the map accumulated 2.5 million keys in 25 minutes, exhausting container memory limits.

## 5. Contributing Factors
1. **Oversized Max Surge:** Deployment configured with `maxSurge: 50%`, which scheduled new pods rapidly without allowing adequate canary soak time.
2. **Missing Memory Headroom Alerting:** Alerting rules checked for node memory pressure but lacked container-level saturation alerts (`container_memory_working_set_bytes / container_spec_memory_limit_bytes > 0.85`).
3. **Staging Traffic Discrepancy:** Staging environment received low-frequency synthetic requests which did not trigger the memory leak prior to release.

## 6. Detection Gaps
- The memory leak was not detected during automated integration tests because test runs concluded in 3 minutes, well before the 1Gi memory ceiling was reached.

## 7. Recovery Actions Taken
1. Executed `kubectl rollout undo deployment/payment-gateway -n payments`.
2. Verified all 12 replicas achieved `1/1 READY`.
3. Verified payment authorization endpoints returned HTTP 200 OK.

## 8. Corrective and Preventive Measures
- [x] Revert code change and replace unbounded map with LRU cache with strict 100k item capacity (PR #409).
- [x] Configure Prometheus alert `ContainerMemoryApproachingLimit` firing when working set exceeds 85% of limit for >5 minutes.
- [x] Mandate minimum 30-minute canary deployment bake period for core transaction services.

## 9. Lessons Learned
- Unbounded in-memory collections are the single most common cause of sudden OOM cascades in microservices.
- Rolling back the deployment immediately was the correct decision; attempting in-place debugging during customer downtime would have prolonged the outage.
