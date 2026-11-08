---
title: "Postmortem: High-Cardinality Label Injection Causing Prometheus TSDB OOM CrashLoop"
incident_id: "SIM-PM-004"
severity: "P1"
category: "postmortems"
component: "prometheus-tsdb"
document_type: "postmortem"
doc_type: "postmortem"
source_type: "project-authored-sre"
applicable_versions: "Prometheus 2.x, Prometheus 3.x"
authoring_date: "2026-11-08"
spdx_license: "Apache-2.0"
attribution: "Enterprise SRE Copilot Authors"
content_hash: "f7a5160104ce376a881db181e70ec77fe1a9f673a6426520dbf10a3e20213904"
related_upstream_urls:
  - "https://prometheus.io/docs/practices/histograms/"
---

# Postmortem: High-Cardinality Label Injection Causing Prometheus TSDB OOM CrashLoop

> [!IMPORTANT]
> **SIMULATED INCIDENT — NOT A REAL PRODUCTION EVENT.**
> This incident postmortem is an educational case study created for operational training and automated SRE guidance. All timestamps, incident logs, service names, and metrics are simulated.

## 1. Incident Summary
On 2026-11-01 at 11:24 UTC, the production Prometheus monitoring server crashed and entered an infinite `CrashLoopBackOff` loop after exhausting its 32Gi memory limit. All cluster alerts ceased functioning and Grafana monitoring became completely unavailable. Total duration was 35 minutes. Mitigated by applying a temporary metric relabeling configuration dropping the offending label and deleting the corrupted WAL head chunk.

## 2. Customer and Service Impact
- **Impacted Systems:** Production Prometheus, Alertmanager, Grafana.
- **Duration:** 35 minutes (11:24 UTC to 11:59 UTC).
- **Impact:** Total monitoring blackout; engineers blind to cluster state for 35 minutes.

## 3. Incident Timeline (UTC)
- **10:45 UTC:** A new release of `search-service` went live containing a new metric: `search_requests_total{query="...", user_id="..."}`.
- **11:00 UTC:** Query label produced 1.8 million unique series in 15 minutes.
- **11:24 UTC:** Prometheus memory working set spiked from 14Gi to 32Gi; kernel OOM killer terminated the process.
- **11:26 UTC:** Prometheus restarted; during WAL replay, it re-loaded the massive series head into memory and OOMKilled repeatedly.
- **11:32 UTC:** P1 declared; SRE investigated using `crictl` and Prometheus logs.
- **11:42 UTC:** SRE patched `search-service` ServiceMonitor with `metricRelabelings` to drop `query` and `user_id` labels.
- **11:50 UTC:** SRE temporarily expanded Prometheus memory limit to 48Gi to allow WAL replay completion.
- **11:55 UTC:** Prometheus replayed WAL successfully, applied drop rule, and memory stabilized at 16Gi.
- **11:59 UTC:** Alerting and Grafana dashboards restored.

## 4. Root Cause
`search-service` developers added un-sanitized user query strings and user IDs directly as Prometheus metric labels. Prometheus TSDB is designed for dimensional indexing, not high-cardinality transaction logging. The sudden explosion of millions of unique label combinations overwhelmed TSDB head index memory.

## 5. Corrective Actions Taken
- [x] Applied `action: labeldrop` regex rule in ServiceMonitor.
- [x] Configured `sample_limit: 50000` on all application scrape configurations.
- [x] Implemented automated CI metric linter blocking PRs containing `user_id`, `email`, or dynamic text labels.
