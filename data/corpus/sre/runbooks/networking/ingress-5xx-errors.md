---
title: "Runbook: Ingress HTTP 502, 503, and 504 Upstream Errors"
incident_id: "RB-NET-003"
severity: "P1"
category: "runbooks/networking"
component: "ingress-controller"
document_type: "runbook"
doc_type: "runbook"
source_type: "project-authored-sre"
applicable_versions: "Kubernetes 1.28-1.37"
authoring_date: "2026-11-08"
spdx_license: "Apache-2.0"
attribution: "Enterprise SRE Copilot Authors"
content_hash: "447cbdfa2a60cadfbde2d2dc26ed5bfb952bfcd69c51f7f5c444f4fdb84d7357"
related_upstream_urls:
  - "https://kubernetes.io/docs/concepts/services-networking/ingress/"
  - "https://kubernetes.io/docs/concepts/services-networking/ingress-controllers/"
---

# Runbook: Ingress HTTP 502, 503, and 504 Upstream Errors

## 1. Incident Identifier
- **ID:** RB-NET-003
- **Severity:** P1 (Critical) — External clients receiving HTTP 502 Bad Gateway, 503 Service Unavailable, or 504 Gateway Timeout from Ingress.

## 2. Severity Classification
- **P1:** Customer-facing production ingress returning 5xx on primary domains.
- **P2:** Intermittent 502 errors during deployment rollouts.
- **P3:** Staging ingress failure.

## 3. Symptoms
- Ingress controller logs report: `connect() failed (111: Connection refused) while connecting to upstream` or `upstream timed out (110: Connection timed out)`.
- Client requests fail with HTTP 502/503/504.
- Alert `IngressHigh5xxRate` firing.

## 4. Common Causes
1. **502 Bad Gateway:** Upstream pod terminated abruptly or backend application closed keep-alive connection without notifying ingress.
2. **503 Service Unavailable:** Ingress controller has 0 endpoints registered for target service; all pods unready.
3. **504 Gateway Timeout:** Upstream application took longer to respond than `proxy-read-timeout` (default 60s).
4. **Premature Pod Termination:** Kubelet killed pod immediately before Ingress controller drained active connections (missing `preStop` hook).

## 5. Impact Assessment
- Immediate customer-visible failure; dropped e-commerce transactions, API errors.

## 6. Diagnostic Procedure
1. Check HTTP status code distribution in Ingress controller logs.
2. Check upstream backend pod readiness and endpoint count.
3. Compare connection keepalive and timeout settings between Ingress and backend.

## 7. Read-Only Investigation Commands
```bash
# 1. Inspect recent 5xx error logs in Ingress controller (e.g. ingress-nginx)
kubectl logs -n ingress-nginx -l app.kubernetes.io/name=ingress-nginx --tail=200 | grep -E " (502|503|504) "

# 2. Check Ingress object configuration and backend service binding
kubectl describe ingress <ingress-name> -n <namespace>

# 3. Check endpoint availability for target backend
kubectl get endpoints <backend-service-name> -n <namespace>

# 4. Check upstream response times in ingress controller metrics
# histogram_quantile(0.99, sum(rate(nginx_ingress_controller_request_duration_seconds_bucket{status="502"}[5m])) by (le))
```

## 8. Expected Outputs
- `502: Connection refused`: Backend container not listening or crashed.
- `503: Service Unavailable`: Endpoints count is 0.
- `504: Connection timed out`: Backend processing delay exceeded ingress timeout.

## 9. Decision Tree
```
                         [Ingress 5xx Error Detected]
                                      |
                            Identify Exact Status Code
                                      |
         +----------------------------+----------------------------+
         |                            |                            |
    [HTTP 502]                   [HTTP 503]                   [HTTP 504]
         |                            |                            |
  Pod crashed or race          Zero endpoints ready:        Backend processing slow:
  condition on rollout:        - Check readiness probe      - Check DB locks / latency
  - Add preStop sleep hook     - Scale backend pods         - Increase proxy-read-timeout
```

## 10. Remediation Steps

### Action A: Fix Rolling Update 502 Race Condition (Safe)
Add a `preStop` sleep hook to backend pod so ingress removes endpoint before container stops:
```yaml
lifecycle:
  preStop:
    exec:
      command: ["/bin/sh", "-c", "sleep 10"]
```

### Action B: Increase Ingress Read Timeout for Long-Running APIs (Safe)
```bash
# Add annotation to Ingress object
kubectl annotate ingress <ingress-name> -n <namespace>   nginx.ingress.kubernetes.io/proxy-read-timeout="120"   nginx.ingress.kubernetes.io/proxy-send-timeout="120" --overwrite
```

### Action C: Emergency Scale-Up of Backend Replicas (Safe)
```bash
kubectl scale deployment/<backend-name> -n <namespace> --replicas=<increased-count>
```

## 11. Verification After Remediation
1. Monitor Ingress access logs for 5 minutes:
   `kubectl logs -n ingress-nginx -l app.kubernetes.io/name=ingress-nginx -f | grep "<host-name>"`
2. Verify HTTP status returns 200 OK.

## 12. Rollback Procedure
Revert Ingress annotations if configuration changes cause issues:
```bash
kubectl apply -f <previous-ingress-manifest.yaml>
```

## 13. Escalation Criteria
- If 504 timeouts are caused by downstream database locks, escalate to Database On-Call.

## 14. Prevention Recommendations
1. Enforce `preStop` hooks on all microservices exposed via Ingress.
2. Configure PodDisruptionBudgets (`minAvailable: 50%`) to prevent rolling deployments from draining too many pods.

## 15. Related Documentation References
- Kubernetes Ingress: `https://kubernetes.io/docs/concepts/services-networking/ingress/`
