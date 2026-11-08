---
title: "Runbook: Missing Metrics and Exposition Format Parsing Failures"
incident_id: "RB-PRM-002"
severity: "P2"
category: "runbooks/prometheus"
component: "prometheus-exposition"
document_type: "runbook"
doc_type: "runbook"
source_type: "project-authored-sre"
applicable_versions: "Prometheus 2.x, Prometheus 3.x"
authoring_date: "2026-11-08"
spdx_license: "Apache-2.0"
attribution: "Enterprise SRE Copilot Authors"
content_hash: "e28d2202e3eda512c35bb3da5b7a0eadceb4aee27e91c165bf1d7fcabcfc542b"
related_upstream_urls:
  - "https://prometheus.io/docs/instrumenting/exposition_formats/"
---

# Runbook: Missing Metrics and Exposition Format Parsing Failures

## 1. Incident Identifier
- **ID:** RB-PRM-002
- **Severity:** P2 (High) — Expected metric missing from Prometheus TSDB despite target reporting `up == 1`.

## 2. Severity Classification
- **P1:** SLA/SLO metric missing, disabling customer billing or error budget tracking.
- **P2:** Application business metric missing.

## 3. Symptoms
- PromQL queries for metric (e.g. `http_requests_total`) return empty result.
- Target is reported as `UP` in Prometheus UI.
- Prometheus logs report: `text format parsing error in line X: expected float value`.

## 4. Common Causes
1. **Metric Relabeling Dropped Metric:** `metric_relabel_configs` dropped metric due to `action: drop` or `keep` regex.
2. **Exposition Format Syntax Error:** Target emits non-standard exposition syntax (invalid characters, unquoted label values).
3. **Metric Renamed in New Version:** Upstream software changed metric name (e.g. `container_cpu_usage_seconds_total`).
4. **Scrape Limit Exceeded:** `sample_limit` exceeded on scrape configuration, causing partial metric drops.

## 5. Impact Assessment
- Dashboards display `No Data`; alerting rules fail to evaluate.

## 6. Diagnostic Procedure
1. Inspect raw `/metrics` output directly from target pod.
2. Check Prometheus scrape configuration for `metric_relabel_configs`.
3. Check Prometheus server logs for parsing errors.

## 7. Read-Only Investigation Commands
```bash
# 1. Scrape raw endpoint directly and grep for target metric
kubectl exec -it <prometheus-pod> -n monitoring -- curl -s http://<target-ip>:8080/metrics | grep "<metric-name>"

# 2. Check Prometheus server logs for parse or sample limit errors
kubectl logs -n monitoring -l app=prometheus --tail=200 | grep -E "(parse error|sample limit)"

# 3. Inspect ServiceMonitor metric_relabel_configs
kubectl get servicemonitor <name> -n <namespace> -o jsonpath='{.spec.endpoints[*].metricRelabelings}' | jq .
```

## 8. Expected Outputs
- Metric present in curl output but absent in PromQL: Relabeling config drop.
- `sample limit exceeded`: Scrape job produces more metrics than allowed.

## 9. Decision Tree
```
                         [Metric Missing in PromQL]
                                     |
                          Does curl /metrics show it?
                                     |
         +---------------------------+---------------------------+
         |                                                       |
   [Yes: Metric Exists in App]                             [No: App Not Emitting]
         |                                                       |
   Check Prometheus:                                       Check Application:
   - Check metric_relabel_configs                          - Is feature flag enabled?
   - Check sample_limit                                    - Check client library config
```

## 10. Remediation Steps

### Action A: Remove Restrictive Metric Relabeling Filter (Safe)
```yaml
# Fix metric_relabel_configs in ServiceMonitor:
metricRelabelings:
- action: keep
  sourceLabels: [__name__]
  regex: "(http_requests_total|process_cpu_seconds_total)" # Ensure target metric included
```

### Action B: Increase Sample Limit (Safe)
```yaml
sampleLimit: 100000
```

## 11. Verification After Remediation
1. Query Prometheus TSDB: `<metric_name>{job="<target-job>"}`.
2. Confirm metric values appear on Grafana dashboards.

## 12. Rollback Procedure
Revert ServiceMonitor manifest if relabeling change causes metric explosion.

## 13. Escalation Criteria
- If application client library generates malformed Prometheus text format, escalate to Software Engineering.

## 14. Prevention Recommendations
1. Use official Prometheus client libraries (client_golang, client_python, micrometer) rather than manual string concatenation.

## 15. Related Documentation References
- Exposition Formats: `https://prometheus.io/docs/instrumenting/exposition_formats/`
