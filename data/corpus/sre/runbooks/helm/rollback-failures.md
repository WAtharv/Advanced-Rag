---
title: "Runbook: Helm Rollback Failures and Hook Failures"
incident_id: "RB-HLM-004"
severity: "P1"
category: "runbooks/helm"
component: "helm-rollback"
document_type: "runbook"
doc_type: "runbook"
source_type: "project-authored-sre"
applicable_versions: "Helm 3.x, Helm 4.x"
authoring_date: "2026-11-08"
spdx_license: "Apache-2.0"
attribution: "Enterprise SRE Copilot Authors"
content_hash: "4a24925afc6100342d97dcc22abe48c28b9d914fa36e3e5c1af51f04b591f9d1"
related_upstream_urls:
  - "https://helm.sh/docs/helm/helm_rollback/"
---

# Runbook: Helm Rollback Failures and Hook Failures

## 1. Incident Identifier
- **ID:** RB-HLM-004
- **Severity:** P1 (Critical) — Emergency `helm rollback` command failed or timed out during incident mitigation.

## 2. Severity Classification
- **P1:** Outage in progress; emergency rollback fails to restore service.
- **P2:** Rollback blocked by pre-rollback hook failure.

## 3. Symptoms
- `helm rollback <name> <rev>` fails with: `ROLLBACK FAILED: release: already exists` or `timed out waiting for condition`.
- Release status transitions to `failed`.
- Pods continue running failing container image.

## 4. Common Causes
1. **Pre-Rollback Hook Failure:** Database migration rollback job failed or timed out.
2. **Missing Secrets/ConfigMaps in Target Revision:** Target historical revision references a Secret that was manually deleted from the cluster.
3. **Node Capacity Starvation:** Cannot schedule pods from previous revision due to resource exhaustion.

## 5. Impact Assessment
- Incident mitigation blocked; prolonged customer-facing outage.

## 6. Diagnostic Procedure
1. Check hook job logs if rollback hooks are configured.
2. Check if resources referenced in target revision exist in the cluster.
3. Manually inspect target revision manifest.

## 7. Read-Only Investigation Commands
```bash
# 1. Retrieve the exact manifest of the revision being rolled back to
helm get manifest <release-name> --revision=<target-rev> -n <namespace>

# 2. Check hook jobs in namespace
kubectl get jobs -n <namespace> -l helm.sh/hook

# 3. Check pods that failed during rollback
kubectl get pods -n <namespace> --sort-by=.metadata.creationTimestamp
```

## 8. Expected Outputs
- `hook failed`: Hook job failed to execute cleanly.
- `Secret "<name>" not found`: Missing dependency in target revision.

## 9. Decision Tree
```
                         [Helm Rollback Failed]
                                    |
                           Is Hook Blocking?
                                    |
         +--------------------------+--------------------------+
         |                                                     |
    [Yes: Hook Failed]                                  [No: Pods Failing]
         |                                                     |
  Bypass hooks in emergency:                            Manually patch deployment to
  helm rollback --no-hooks                              last known healthy container image
```

## 10. Remediation Steps

### Action A: Rollback with Hooks Disabled (Emergency Mitigation — Requires Lead Approval)
```bash
# In an emergency, bypass failing hooks to restore serving workload
helm rollback <release-name> <target-rev> -n <namespace> --no-hooks --timeout 5m
```

### Action B: Direct Manual Patch of Deployment Image (Immediate Triage)
```bash
# Bypass Helm temporarily to restore customer service immediately
kubectl set image deployment/<deployment-name> <container>=<known-good-image> -n <namespace>
```

## 11. Verification After Remediation
1. Verify user-facing service is restored and healthy.
2. Reconcile Helm release state once service stability is confirmed.

## 12. Rollback Procedure
N/A — This is the rollback remediation runbook.

## 13. Escalation Criteria
- Escalate immediately to Incident Commander and Core Development Team.

## 14. Prevention Recommendations
1. Avoid non-idempotent rollback hooks.
2. Never delete Secrets or ConfigMaps manually outside of Helm release lifecycle.

## 15. Related Documentation References
- Helm Rollback: `https://helm.sh/docs/helm/helm_rollback/`
