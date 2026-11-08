---
title: "Runbook: Alertmanager Routing and Notification Dispatch Failures"
incident_id: "RB-PRM-006"
severity: "P1"
category: "runbooks/prometheus"
component: "alertmanager"
document_type: "runbook"
doc_type: "runbook"
source_type: "project-authored-sre"
applicable_versions: "Alertmanager 0.25-0.34"
authoring_date: "2026-11-08"
spdx_license: "Apache-2.0"
attribution: "Enterprise SRE Copilot Authors"
content_hash: "5a32efd4402924ae02a6220ae19b659a7a1e5592f4cfc547fd86dbba075e6505"
related_upstream_urls:
  - "https://prometheus.io/docs/alerting/latest/configuration/"
---

# Runbook: Alertmanager Routing and Notification Dispatch Failures

## 1. Incident Identifier
- **ID:** RB-PRM-006
- **Severity:** P1 (Critical) — Alertmanager failing to deliver notification webhooks to PagerDuty, Slack, or email.

## 2. Severity Classification
- **P1:** PagerDuty / On-call notification pipeline down; zero pages reaching engineers.
- **P2:** Non-critical Slack channel notification failing.

## 3. Symptoms
- Prometheus alerts are `FIRING`, but on-call engineers receive zero pages or notifications.
- Alertmanager logs report: `Notify for 1 alerts failed: Post "https://events.pagerduty.com/...": dial tcp ...: i/o timeout`.
- Prometheus alert `AlertmanagerNotificationsFailed` firing.

## 4. Common Causes
1. **Invalid Webhook URL / Expired API Token:** PagerDuty integration key or Slack incoming webhook expired.
2. **Egress Firewall / Proxy Blocking:** Outbound traffic from Alertmanager pods blocked by NetworkPolicy or security group.
3. **Routing Tree Catch-All Missing:** Alert labels do not match any route, and default receiver is invalid.
4. **Template Syntax Error:** Custom notification template has invalid Go template syntax, crashing the notifier.

## 5. Impact Assessment
- Undetected production outages; delayed incident response.

## 6. Diagnostic Procedure
1. Check Alertmanager logs for notification dispatch failures.
2. Test webhook URL reachability from Alertmanager pod.
3. Validate Alertmanager configuration using `amtool`.

## 7. Read-Only Investigation Commands
```bash
# 1. Check Alertmanager logs for notification dispatch failures
kubectl logs -n monitoring -l app.kubernetes.io/name=alertmanager --tail=100 | grep -i "notify"

# 2. Test webhook URL reachability from Alertmanager pod
kubectl exec -it <alertmanager-pod> -n monitoring -- curl -m 5 -Iv https://events.pagerduty.com

# 3. Check Alertmanager configuration validation with amtool
kubectl exec -it <alertmanager-pod> -n monitoring -- amtool check-config /etc/alertmanager/alertmanager.yml
```

## 8. Expected Outputs
- `Notify for 1 alerts failed ... 401 Unauthorized`: API key revoked or expired.
- `syntax error in template`: Template rendering failure.

## 9. Decision Tree
```
                         [Alertmanager Dispatch Failure]
                                        |
                          Is it 401 Auth or Network Timeout?
                                        |
         +------------------------------+------------------------------+
         |                                                             |
   [401 Unauthorized]                                            [Network Timeout]
         |                                                             |
   Update integration token in Secret;                           Check NetworkPolicy & Egress;
   reload Alertmanager config                                    allow HTTPS 443 outbound
```

## 10. Remediation Steps

### Action A: Update Integration Key in Secret (Safe)
Update the integration secret with fresh valid token and trigger config reload:
```bash
kubectl exec -it <alertmanager-pod> -n monitoring -- curl -X POST http://localhost:9093/-/reload
```

## 11. Verification After Remediation
1. Trigger a synthetic test alert to verify end-to-end delivery:
   `amtool alert add alertname=TestAlert severity=warning instance=manual-test`
2. Confirm receipt on target notification channel.

## 12. Rollback Procedure
Revert Alertmanager configuration Secret to previous revision.

## 13. Escalation Criteria
- If third-party incident management provider (PagerDuty/OpsGenie) is having a global outage, activate backup SMS/Email notifier.

## 14. Prevention Recommendations
1. Establish automated dead-man's-snitch heartbeat alerts (e.g. `Watchdog` alert) to verify continuous notification health.

## 15. Related Documentation References
- Alertmanager Configuration: `https://prometheus.io/docs/alerting/latest/configuration/`
