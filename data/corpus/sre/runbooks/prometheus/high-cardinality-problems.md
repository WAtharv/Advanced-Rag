---
title: "Runbook: High Cardinality Explosion and TSDB Churn"
incident_id: "RB-PRM-004"
severity: "P1"
category: "runbooks/prometheus"
component: "prometheus-tsdb"
document_type: "runbook"
doc_type: "runbook"
source_type: "project-authored-sre"
applicable_versions: "Prometheus 2.x, Prometheus 3.x"
authoring_date: "2026-11-08"
spdx_license: "Apache-2.0"
attribution: "Enterprise SRE Copilot Authors"
content_hash: "adc7dcf262d01b06323c973949f80568761ac7decdc548177d594c45d3cb2ea5"
related_upstream_urls:
  - "https://prometheus.io/docs/practices/histograms/"
---

# Runbook: High Cardinality Explosion and TSDB Churn

## 1. Incident Identifier
- **ID:** RB-PRM-004
- **Severity:** P1 (Critical) / P2 (High) — Unbounded label values (user IDs, URLs, UUIDs) causing exponential TSDB series explosion and Prometheus OOM.

## 2. Severity Classification
- **P1:** Prometheus crashing repeatedly with OOMKilled; monitoring offline.
- **P2:** Head series surging (>1,500,000 series); memory growing steadily.

## 3. Symptoms
- Prometheus memory usage surges dramatically; pods restart with `OOMKilled` (exit code 137).
- Prometheus TSDB head series metric `prometheus_tsdb_head_series` climbing steeply.
- Alert `PrometheusTSDBCardinalityExplosion` firing.

## 4. Common Causes
1. **Dynamic High-Cardinality Labels:** Metric contains user IDs, email addresses, order IDs, or unnormalized URLs (e.g. `/api/v1/user/123456`).
2. **Too Many Custom Metrics:** Application microservice exporting thousands of unique metric names dynamically.
3. **Microservice High Churn:** Pods scaling up and down every 10 seconds with unique ephemeral pod names.

## 5. Impact Assessment
- Memory starvation; complete crash of monitoring infrastructure.

## 6. Diagnostic Procedure
1. Run Prometheus TSDB status API to identify top cardinality labels and metric names.
2. Pinpoint the offending workload exporter.

## 7. Read-Only Investigation Commands
```bash
# 1. Query Prometheus TSDB Status API for top 10 label names with highest cardinality
kubectl exec -it <prometheus-pod> -n monitoring -- curl -s http://localhost:9090/api/v1/status/tsdb | jq .data.seriesCountByMetricName[:10]
kubectl exec -it <prometheus-pod> -n monitoring -- curl -s http://localhost:9090/api/v1/status/tsdb | jq .data.labelValueCountByLabelName[:10]

# 2. PromQL: Head series total count
# prometheus_tsdb_head_series
```

## 8. Expected Outputs
- `labelValueCountByLabelName`: Pinpoints exact label name with hundreds of thousands of distinct values.

## 9. Decision Tree
```
                         [Cardinality Explosion]
                                    |
                         Identify Offending Label
                                    |
         +--------------------------+--------------------------+
         |                                                     |
  [User ID / UUID in Label]                             [Unnormalized URL Path]
         |                                                     |
  Drop label immediately via:                           Normalize path in app code;
  metricRelabelings: action: labeldrop                  drop raw path label in scrape
```

## 10. Remediation Steps

### Action A: Drop High-Cardinality Label in Metric Relabeling (Safe / Immediate Mitigation)
```yaml
# Add metric_relabel_configs to strip offending label at scrape time:
metricRelabelings:
- action: labeldrop
  regex: "(user_id|order_id|uuid)"
```

### Action B: Normalize Path Labels in Application Code
Replace raw URL paths with route templates (e.g. `/users/:id` instead of `/users/987654`).

## 11. Verification After Remediation
1. Verify `prometheus_tsdb_head_series` stabilizes and stops climbing.
2. Confirm Prometheus memory working set drops to safe operating levels.

## 12. Rollback Procedure
Revert ServiceMonitor patch if required metrics were dropped accidentally.

## 13. Escalation Criteria
- If WAL replay cannot complete due to memory limits, expand Prometheus memory limit temporarily.

## 14. Prevention Recommendations
1. Establish automated CI linting for metric definitions blocking labels that contain unbounded identifiers.

## 15. Related Documentation References
- Histograms and Cardinality: `https://prometheus.io/docs/practices/histograms/`
