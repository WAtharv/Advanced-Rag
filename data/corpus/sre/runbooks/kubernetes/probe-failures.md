---
title: "Runbook: Failed Liveness, Readiness, and Startup Probes"
incident_id: "RB-K8S-006"
severity: "P2"
category: "runbooks/kubernetes"
component: "kubelet-probes"
document_type: "runbook"
doc_type: "runbook"
source_type: "project-authored-sre"
applicable_versions: "Kubernetes 1.28-1.37"
authoring_date: "2026-11-08"
spdx_license: "Apache-2.0"
attribution: "Enterprise SRE Copilot Authors"
content_hash: "b982917655388f869b319585b1c514ff6da8102a5ff85f24e1cae8fa8ddecf8f"
related_upstream_urls:
  - "https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/"
---

# Runbook: Failed Liveness, Readiness, and Startup Probes

## 1. Incident Identifier
- **ID:** RB-K8S-006
- **Severity:** P2 (High) — Pod probes failing, leading to traffic withdrawal (readiness) or container restart loops (liveness).

## 2. Severity Classification
- **P1:** Liveness probe killing all healthy pods during traffic spike due to slow response times.
- **P2:** Readiness probe removing pods from Service Endpoints, reducing serving capacity.
- **P3:** Startup probe failing on slow batch init container.

## 3. Symptoms
- Pod status shows `0/1 READY` while container status is `Running`.
- Pod restarts periodically with `Liveness probe failed: HTTP probe failed with statuscode: 500`.
- Service endpoints dropped: `kubectl get endpoints <service-name>` shows 0 healthy IP addresses.

## 4. Common Causes
1. **Liveness Probe Endpoint Doing Heavy Work:** Probe invokes database queries or heavy external dependency checks instead of checking local process health.
2. **Timeout Too Aggressive:** `timeoutSeconds: 1` too short under load, timing out transiently.
3. **Slow Application Initialization:** Application takes 45 seconds to boot, but liveness probe begins after 10 seconds without a `startupProbe`.
4. **Port or Path Mismatch:** Probe checks HTTP `/healthz` on port 8080, but application listens on port 8081 or path `/actuator/health`.
5. **Deadlock / Event Loop Blocked:** Application event loop saturated or thread pool exhausted, failing to respond to probe requests.

## 5. Impact Assessment
- Readiness failure: Zero endpoints available, causing Ingress to return HTTP 503 Service Unavailable.
- Liveness failure: Infinite restart cascade preventing cache warming or connection pool recovery.

## 6. Diagnostic Procedure
1. Identify which probe is failing (Startup, Liveness, or Readiness).
2. Check probe failure count, timeout, and consecutive failure threshold.
3. Test the probe endpoint manually from inside the cluster network.

## 7. Read-Only Investigation Commands
```bash
# 1. Inspect probe failure events on the pod
kubectl get events -n <namespace> --field-selector involvedObject.name=<pod-name> | grep -i probe

# 2. Inspect exact probe configuration in PodSpec
kubectl get pod <pod-name> -n <namespace> -o jsonpath='{range .spec.containers[*]}{.name}{"
Liveness:"}{.livenessProbe}{"
Readiness:"}{.readinessProbe}{"
Startup:"}{.startupProbe}{"

"}{end}'

# 3. Check container logs immediately preceding probe failure
kubectl logs <pod-name> -n <namespace> -c <container-name> --tail=100

# 4. Probe the endpoint manually using curl from a debug container
kubectl run probe-test --rm -it --image=curlimages/curl --restart=Never -n <namespace> -- curl -m 5 -Iv http://<pod-ip>:<probe-port><probe-path>
```

## 8. Expected Outputs
- `Liveness probe failed: Get "http://10.244.1.45:8080/health": context deadline exceeded`: Timeout reached.
- `Readiness probe failed: HTTP probe failed with statuscode: 503`: Backend dependency marked unhealthy.
- `Readiness probe failed: connection refused`: Application not yet listening on target port.

## 9. Decision Tree
```
                         [Probe Failure Detected]
                                     |
                            Identify Failing Probe
                                     |
        +----------------------------+----------------------------+
        |                            |                            |
  [Startup Probe]              [Liveness Probe]             [Readiness Probe]
        |                            |                            |
  App boots slowly:            Process alive?               Traffic dropped:
  - Increase failureThreshold  - Yes: increase timeout      - Check dependency health
  - Increase periodSeconds     - No: profile thread dump    - Separate shallow / deep health
```

## 10. Remediation Steps

### Action A: Implement or Tune Startup Probe (Safe)
```yaml
# Add startupProbe to protect slow-initializing applications
startupProbe:
  httpGet:
    path: /healthz
    port: 8080
  initialDelaySeconds: 5
  periodSeconds: 5
  failureThreshold: 30 # Allows up to 150 seconds to initialize
```

### Action B: Increase Liveness Probe Timeout & Threshold (Safe)
```yaml
livenessProbe:
  httpGet:
    path: /livez
    port: 8080
  timeoutSeconds: 3
  periodSeconds: 10
  failureThreshold: 5 # Prevent single-spike kills
```

### Action C: Temporarily Remove Liveness Probe During Incident (Requires Approval)
```bash
# If liveness probe is repeatedly killing pods in an emergency:
kubectl patch deployment <deployment-name> -n <namespace> --type=json -p='[{"op": "remove", "path": "/spec/template/spec/containers/0/livenessProbe"}]'
```

## 11. Verification After Remediation
1. Verify pod achieves `1/1 READY`.
2. Confirm pod endpoints are restored in the Service:
   ```bash
   kubectl get endpoints <service-name> -n <namespace>
   ```

## 12. Rollback Procedure
```bash
kubectl rollout undo deployment/<deployment-name> -n <namespace>
```

## 13. Escalation Criteria
- If thread deadlocks are observed in application thread dumps, escalate to Software Engineering.

## 14. Prevention Recommendations
1. Separate liveness (process alive?) from readiness (ready to serve traffic?). Never check external dependencies (DB/Redis) in liveness probes.
2. Always pair slow-booting applications with dedicated `startupProbe`.
3. Set `timeoutSeconds` to at least 2-3 seconds for non-trivial HTTP endpoints.

## 15. Related Documentation References
- Configure Probes: `https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/`
