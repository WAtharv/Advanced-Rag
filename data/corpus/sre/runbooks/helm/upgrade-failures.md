---
title: "Runbook: Helm Upgrade Failures and Resource Conflicts"
incident_id: "RB-HLM-002"
severity: "P1"
category: "runbooks/helm"
component: "helm-upgrade"
document_type: "runbook"
doc_type: "runbook"
source_type: "project-authored-sre"
applicable_versions: "Helm 3.x, Helm 4.x"
authoring_date: "2026-11-08"
spdx_license: "Apache-2.0"
attribution: "Enterprise SRE Copilot Authors"
content_hash: "40a45c5be7d37d7e1807e6c37178f25a8562c858727b3d5997e0e1cdc4c39cfd"
related_upstream_urls:
  - "https://helm.sh/docs/helm/helm_upgrade/"
---

# Runbook: Helm Upgrade Failures and Resource Conflicts

## 1. Incident Identifier
- **ID:** RB-HLM-002
- **Severity:** P1 (Critical) / P2 (High) — Helm upgrade failed, leaving release in `failed` state or blocking service updates.

## 2. Severity Classification
- **P1:** Helm upgrade failed during production release, leaving partial resources or broken deployment.
- **P2:** Upgrade aborted cleanly before mutating running pods.
- **P3:** Staging release upgrade failure.

## 3. Symptoms
- `helm upgrade` returns error: `UPGRADE FAILED: cannot patch ... with kind Deployment: field is immutable`.
- Release status in `helm list` shows `STATUS: failed`.
- Error: `upgrade failed: timed out waiting for the condition`.

## 4. Common Causes
1. **Immutable Field Modification:** Attempting to modify immutable fields in Kubernetes objects (e.g. `spec.clusterIP` in Service or `spec.selector` in Deployment).
2. **Timeout on Readiness:** New pods failed readiness checks, causing `--wait --timeout` threshold to lapse.
3. **Pre-Upgrade Hook Failure:** Hook job failed, aborting the upgrade transaction before resources applied.
4. **CRD Schema Version Mismatch:** Upgraded chart expects updated CRD schemas that were not installed.

## 5. Impact Assessment
- Release state marked `failed`; subsequent upgrades blocked until rolled back or resolved.

## 6. Diagnostic Procedure
1. Check release revision history and status.
2. Identify which resource failed to apply.
3. Inspect pod logs of the new revision.

## 7. Read-Only Investigation Commands
```bash
# 1. Check release history and status of all revisions
helm history <release-name> -n <namespace>

# 2. Check detailed status of the failed revision
helm status <release-name> -n <namespace>

# 3. Check Kubernetes events for rejection reasons
kubectl get events -n <namespace> --sort-by=.lastTimestamp | grep -i failed

# 4. Dry-run the upgrade with debug output to pinpoint schema rejection
helm upgrade <release-name> <chart-path> -n <namespace> -f values.yaml --dry-run --debug
```

## 8. Expected Outputs
- `The Deployment "xyz" is invalid: spec.selector: Invalid value: field is immutable`: Selector change requires recreate.
- `timed out waiting for the condition`: Pods under new revision failed to reach ready state.

## 9. Decision Tree
```
                         [Helm Upgrade Failed]
                                   |
                       Is Running Service Impacted?
                                   |
         +-------------------------+-------------------------+
         |                                                   |
   [Yes: User Traffic Impacted]                       [No: Old Revision Serving]
         |                                                   |
   Execute Immediate Rollback:                         Investigate & Fix Values:
   helm rollback <release> <last-good-rev>             - Check immutable fields
                                                       - Fix readiness probes
```

## 10. Remediation Steps

### Action A: Immediate Rollback to Previous Healthy Revision (Safe / Recommended)
```bash
# 1. Identify previous deployed revision number (e.g., rev 4)
helm history <release-name> -n <namespace>

# 2. Rollback immediately
helm rollback <release-name> <healthy-revision-number> -n <namespace>
```

### Action B: Fix Immutable Selector in Chart Manifest (Requires Controlled Deployment)
If a Deployment selector must be changed, the existing Deployment must be deleted without cascading to pods, or replaced cleanly:
```bash
# If selector must change, delete deployment retaining pods:
kubectl delete deployment <deployment-name> -n <namespace> --cascade=orphan
# Immediately apply the upgraded chart:
helm upgrade <release-name> <chart-path> -n <namespace> -f values.yaml
```

## 11. Verification After Remediation
1. Verify `helm status <release-name> -n <namespace>` reports `STATUS: deployed`.
2. Confirm healthy revision is active and all pods are `Running`.

## 12. Rollback Procedure
Always rollback using `helm rollback <release-name> <rev> -n <namespace>`.

## 13. Escalation Criteria
- If upgrade failed midway through a stateful database schema migration, escalate to Database On-Call before executing rollback.

## 14. Prevention Recommendations
1. Never modify `spec.selector` on existing Deployments or StatefulSets.
2. Always execute `helm diff upgrade` in CI/CD pipeline before applying in production.

## 15. Related Documentation References
- Helm Upgrade: `https://helm.sh/docs/helm/helm_upgrade/`
- Core Knowledge Base: `data/corpus/helm/operations/helm-upgrade.md`
