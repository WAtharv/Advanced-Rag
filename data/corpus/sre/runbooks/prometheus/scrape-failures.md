---
title: "Runbook: Scrape Timeouts and Exporter Rate-Limiting"
incident_id: "RB-PRM-003"
severity: "P2"
category: "runbooks/prometheus"
component: "prometheus-scrape"
document_type: "runbook"
doc_type: "runbook"
source_type: "project-authored-sre"
applicable_versions: "Prometheus 2.x, Prometheus 3.x"
authoring_date: "2026-11-08"
spdx_license: "Apache-2.0"
attribution: "Enterprise SRE Copilot Authors"
content_hash: "e243f8c0c9f9e097009a6f6c3af553cb3852ef84a50d0285cffa65cd3a5c6217"
related_upstream_urls:
  - "https://prometheus.io/docs/prometheus/latest/configuration/configuration/"
---

# Runbook: Scrape Timeouts and Exporter Rate-Limiting

## 1. Incident Identifier
- **ID:** RB-PRM-003
- **Severity:** P2 (High) — Prometheus scrape jobs timing out before completing, causing intermittent gaps in time series data.

## 2. Severity Classification
- **P1:** Scrapes timing out across all critical cluster infrastructure exporters.
- **P2:** Single application or exporter scrape timing out.
- **P3:** Occasional slow scrape on non-critical metrics.

## 3. Symptoms
- Prometheus target status oscillates between `UP` and `DOWN` with error `context deadline exceeded`.
- Scrape duration metric `scrape_duration_seconds` consistently exceeds `scrape_timeout` (default 10s).
- Prometheus alert `PrometheusScrapeTimeout` firing.

## 4. Common Causes
1. **Target Exporter Slow Execution:** Exporter (e.g. database exporter, SNMP exporter) executes expensive queries on every scrape.
2. **Gigantic Metric Payload:** Exporter generating >50MB of metrics on single HTTP request.
3. **Scrape Timeout Shorter Than Scrape Interval:** Configured `scrape_timeout: 10s` while application takes 12s to format metrics.

## 5. Impact Assessment
- Data gaps, counter resets, and inaccurate rate calculations.

## 6. Diagnostic Procedure
1. Measure raw HTTP scrape duration using `curl -w "%{time_total}
"`.
2. Inspect Prometheus `scrape_duration_seconds` for the affected job.

## 7. Read-Only Investigation Commands
```bash
# 1. Measure total scrape execution time from Prometheus pod
kubectl exec -it <prometheus-pod> -n monitoring -- curl -s -w "Time: %{time_total}s Size: %{size_download} bytes
" -o /dev/null http://<target-ip>:8080/metrics

# 2. PromQL: Top slowest scrape targets
# topk(10, scrape_duration_seconds)
```

## 8. Expected Outputs
- Total time exceeding scrapeTimeout: Timeout configuration too tight or exporter degraded.

## 9. Decision Tree
```
                         [Scrape Timeout Failure]
                                     |
                          Is Exporter Heavy or Slow?
                                     |
         +---------------------------+---------------------------+
         |                                                       |
   [Expensive DB Queries]                                  [Huge Payload (>20MB)]
         |                                                       |
   Tune exporter collection flags;                         Drop unneeded metric series
   increase scrape interval                                using metric_relabel_configs
```

## 10. Remediation Steps

### Action A: Increase Scrape Timeout (Safe)
```yaml
scrapeTimeout: 25s
interval: 30s
```

### Action B: Disable Heavy Collectors in Exporter (Safe)
Tune exporter flags (e.g. disable expensive table stats in mysql-exporter).

## 11. Verification After Remediation
1. Verify `scrape_duration_seconds < scrape_timeout`.
2. Confirm target remains continuously `UP`.

## 12. Rollback Procedure
Revert ServiceMonitor configuration to prior values.

## 13. Escalation Criteria
- If hardware exporter is blocked on physical device locks, escalate to Systems Infrastructure.

## 14. Prevention Recommendations
1. Separate fast metrics (HTTP rates, CPU) from slow batch metrics (database table scans) into distinct scrape jobs.

## 15. Related Documentation References
- Prometheus Scrape Config: `https://prometheus.io/docs/prometheus/latest/configuration/configuration/`
