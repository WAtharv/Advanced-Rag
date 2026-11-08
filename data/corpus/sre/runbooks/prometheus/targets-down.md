---
title: "Runbook: Prometheus Scrape Targets Down and Up=0 Alerts"
incident_id: "RB-PRM-001"
severity: "P1"
category: "runbooks/prometheus"
component: "prometheus-scrape"
document_type: "runbook"
doc_type: "runbook"
source_type: "project-authored-sre"
applicable_versions: "Prometheus 2.x, Prometheus 3.x"
authoring_date: "2026-11-08"
spdx_license: "Apache-2.0"
attribution: "Enterprise SRE Copilot Authors"
content_hash: "b36575c1888c146ab604a9b76fbbbaa5413a7245ebe31e105fddc549c327ace5"
related_upstream_urls:
  - "https://prometheus.io/docs/prometheus/latest/configuration/configuration/#scrape_config"
---

# Runbook: Prometheus Scrape Targets Down and Up=0 Alerts

## 1. Incident Identifier
- **ID:** RB-PRM-001
- **Severity:** P1 (Critical) / P2 (High) — Prometheus scrape target unreachable (`up == 0`), causing monitoring blind spots and missed alerts.

## 2. Severity Classification
- **P1:** Core infrastructure targets (Node Exporter, CoreDNS, Kubelet) down across multiple nodes.
- **P2:** Single application pod instance scrape failing.
- **P3:** Intermittent scrape drops on ephemeral batch job.

## 3. Symptoms
- Prometheus alert `TargetDown` or `KubeNodeExporterDown` firing.
- PromQL query `up == 0` returns active failing endpoints.
- Prometheus `/targets` web UI shows target state `DOWN` with error message.

## 4. Common Causes
1. **Target Pod Crashed or Unhealthy:** Application container is in `CrashLoopBackOff` or terminated.
2. **NetworkPolicy Blocking Scraper:** Ingress NetworkPolicy on target pod does not permit traffic from Prometheus pods.
3. **Port / Path / Scheme Mismatch:** Scrape config targets port 8080 instead of 9090, or path `/metrics` instead of `/actuator/prometheus`.
4. **TLS / Certificate Verification Rejection:** Scrape configured with `scheme: HTTPS` but target uses self-signed certificate without `insecure_skip_verify`.
5. **Kubelet / Service Discovery Stale:** Prometheus discovery retains dead pod IP addresses.

## 5. Impact Assessment
- Loss of observability, missing rate metrics, false positive or silenced alerts.

## 6. Diagnostic Procedure
1. Execute `up == 0` in Prometheus query UI to identify failing targets.
2. Check error message recorded in Prometheus Targets page.
3. Test direct scrape connection from Prometheus pod using `curl`.

## 7. Read-Only Investigation Commands
```bash
# 1. PromQL: List all targets currently down
# up == 0

# 2. Check if target pod is actually alive and ready
kubectl get pod <target-pod> -n <namespace> -o wide

# 3. Test direct scrape from Prometheus pod
kubectl exec -it <prometheus-pod> -n monitoring -- curl -m 5 -Iv http://<target-pod-ip>:<metrics-port>/metrics

# 4. Check target ServiceMonitor / PodMonitor configuration
kubectl get servicemonitor <monitor-name> -n <namespace> -o yaml
```

## 8. Expected Outputs
- `context deadline exceeded`: Scrape timeout exceeded (target slow or blocked by network).
- `connection refused`: Target process not listening on expected port.
- `x509: certificate signed by unknown authority`: TLS certificate verification failure.

## 9. Decision Tree
```
                         [Prometheus Target Down: up == 0]
                                         |
                            Test Direct HTTP Scrape
                                         |
         +-------------------------------+-------------------------------+
         |                                                               |
  [Connection Refused]                                            [Connection Timeout]
         |                                                               |
  Check application process:                                      Check NetworkPolicy & Firewall:
  - Is metrics port listening?                                    - Allow monitoring namespace ingress
  - Fix port in ServiceMonitor                                    - Increase scrape_timeout
```

## 10. Remediation Steps

### Action A: Correct ServiceMonitor Port or Path (Safe)
```yaml
# In ServiceMonitor:
endpoints:
- port: http-metrics # Must match port name on Service
  path: /metrics
  interval: 30s
  scrapeTimeout: 10s
```

### Action B: Add Monitoring Allow Rule to Target NetworkPolicy (Safe)
```yaml
ingress:
- from:
  - namespaceSelector:
      matchLabels:
        kubernetes.io/metadata.name: monitoring
  ports:
  - protocol: TCP
    port: 8080
```

## 11. Verification After Remediation
1. Verify PromQL `up{job="<target-job>"}` returns `1`.
2. Confirm targets page in Prometheus UI displays green `UP` badge.

## 12. Rollback Procedure
Revert ServiceMonitor manifest if modifications cause syntax errors.

## 13. Escalation Criteria
- If Kubelet `/metrics` endpoint is failing across all nodes, escalate as P1 Kubelet Incident.

## 14. Prevention Recommendations
1. Establish standard metrics port naming (e.g. `http-metrics`) across all Helm charts.
2. Include monitoring namespace ingress permits in base NetworkPolicy templates.

## 15. Related Documentation References
- Prometheus Scrape Config: `https://prometheus.io/docs/prometheus/latest/configuration/configuration/#scrape_config`
