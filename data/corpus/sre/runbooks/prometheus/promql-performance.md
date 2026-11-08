---
title: "Runbook: Slow PromQL Queries and Evaluation Timeouts"
incident_id: "RB-PRM-005"
severity: "P2"
category: "runbooks/prometheus"
component: "promql-engine"
document_type: "runbook"
doc_type: "runbook"
source_type: "project-authored-sre"
applicable_versions: "Prometheus 2.x, Prometheus 3.x"
authoring_date: "2026-11-08"
spdx_license: "Apache-2.0"
attribution: "Enterprise SRE Copilot Authors"
content_hash: "b9ac9df7156468a3f5dd6063f962b22308e319d5506c624b46b64bf064e09bc6"
related_upstream_urls:
  - "https://prometheus.io/docs/prometheus/latest/querying/basics/"
---

# Runbook: Slow PromQL Queries and Evaluation Timeouts

## 1. Incident Identifier
- **ID:** RB-PRM-005
- **Severity:** P2 (High) — Heavy PromQL queries timing out, degrading Grafana dashboards and rule evaluation loops.

## 2. Severity Classification
- **P1:** Rule evaluation loop falling behind by multiple intervals, silencing alerts.
- **P2:** Grafana operational dashboards timing out.
- **P3:** Ad-hoc analytics query slow.

## 3. Symptoms
- Grafana panels show: `504 Gateway Timeout` or `query processing would load too many samples`.
- Prometheus logs: `query execution timeout exceeded`.
- Alert `PrometheusRuleEvaluationSlow` firing.

## 4. Common Causes
1. **Unindexed / Broad Regex Matchers:** Query uses `{name=~".*"}` across millions of series.
2. **Excessive Range Vector Duration:** Requesting `[30d]` resolution over thousands of series simultaneously.
3. **Lack of Recording Rules:** Computing heavy multi-level aggregations on every dashboard refresh instead of precomputing.

## 5. Impact Assessment
- Dashboards fail to render during incidents; delayed alert triggers.

## 6. Diagnostic Procedure
1. Enable and inspect the Prometheus query log.
2. Identify slow queries with high sample load.
3. Convert high-frequency slow queries into recording rules.

## 7. Read-Only Investigation Commands
```bash
# 1. Inspect slow queries from Prometheus query log
kubectl exec -it <prometheus-pod> -n monitoring -- tail -n 50 /var/log/prometheus/query.log | jq .

# 2. PromQL: Rule evaluation duration
# prometheus_rule_group_last_duration_seconds
```

## 8. Expected Outputs
- `load_time_seconds > 5.0`: Indicates disk TSDB fetch bottleneck.

## 9. Decision Tree
```
                         [Slow PromQL Query]
                                  |
                        Is it a Dashboard or Rule?
                                  |
         +------------------------+------------------------+
         |                                                 |
  [Dashboard Query]                                 [Alerting Rule]
         |                                                 |
  Precompute via Recording Rule                     Increase evaluation_interval
  and replace query on dashboard                    or tighten metric label filter
```

## 10. Remediation Steps

### Action A: Enable Query Log to Identify Slow Queries (Safe)
```yaml
# In prometheus command-line flags:
--query.log-file=/var/log/prometheus/query.log
```

### Action B: Precompute with Recording Rules (Safe / Recommended)
```yaml
groups:
- name: precomputed-metrics
  rules:
  - record: job:http_requests:rate5m
    expr: sum(rate(http_requests_total[5m])) by (job, status)
```

## 11. Verification After Remediation
1. Verify Grafana dashboards load in <1.5s using recording rules.
2. Confirm `prometheus_engine_query_duration_seconds` 99th percentile drops below 1s.

## 12. Rollback Procedure
Revert Prometheus rule file additions.

## 13. Escalation Criteria
- If TSDB disk I/O latency is throttling all queries, escalate to Storage Admin.

## 14. Prevention Recommendations
1. Avoid bare regex matches without at least one exact label matcher.
2. Mandate recording rules for dashboards queried by more than 5 users.

## 15. Related Documentation References
- PromQL Querying Basics: `https://prometheus.io/docs/prometheus/latest/querying/basics/`
