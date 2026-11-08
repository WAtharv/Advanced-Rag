---
title: "Runbook: Helm Release Stuck in pending-install or pending-upgrade"
incident_id: "RB-HLM-003"
severity: "P2"
category: "runbooks/helm"
component: "helm-storage"
document_type: "runbook"
doc_type: "runbook"
source_type: "project-authored-sre"
applicable_versions: "Helm 3.x, Helm 4.x"
authoring_date: "2026-11-08"
spdx_license: "Apache-2.0"
attribution: "Enterprise SRE Copilot Authors"
content_hash: "290819c3ff03d1775c3757b97b42edcdea93898a345efd709258d986e28a30bf"
related_upstream_urls:
  - "https://helm.sh/docs/topics/advanced/"
---

# Runbook: Helm Release Stuck in pending-install or pending-upgrade

## 1. Incident Identifier
- **ID:** RB-HLM-003
- **Severity:** P2 (High) — Helm release locked in `pending-install`, `pending-upgrade`, or `pending-rollback` state; all future operations blocked.

## 2. Severity Classification
- **P1:** Automated CI/CD pipeline blocked on production release due to locked release secret.
- **P2:** Manual helm upgrade blocked by stale lock.

## 3. Symptoms
- `helm upgrade` or `helm rollback` returns: `Error: UPGRADE FAILED: another operation (install/upgrade/rollback) is in progress`.
- `helm list` shows `STATUS: pending-upgrade` or `pending-install`.

## 4. Common Causes
1. **CI/CD Job Abruptly Terminated:** GitLab runner / GitHub Actions runner killed or timed out while Helm was running `--wait`.
2. **Network Timeout During Helm CLI Execution:** Client dropped connection to API server while lock secret was held.
3. **Hook Job Hung Indefinitely:** Helm waiting for a pre-upgrade hook that never exited.

## 5. Impact Assessment
- Helm cannot perform any operations on the release until the lock is cleared.

## 6. Diagnostic Procedure
1. Verify if any active Helm CLI process or CI runner is still genuinely running.
2. Inspect the latest Helm release secret in the namespace.

## 7. Read-Only Investigation Commands
```bash
# 1. Check release status and active revision
helm list -n <namespace> --filter <release-name>

# 2. List all release secrets in the namespace
kubectl get secrets -n <namespace> -l owner=helm,name=<release-name> --sort-by=.metadata.creationTimestamp

# 3. Inspect the status recorded inside the latest release secret
LATEST_SECRET=$(kubectl get secrets -n <namespace> -l owner=helm,name=<release-name> --sort-by=.metadata.creationTimestamp -o jsonpath='{.items[-1].metadata.name}')
echo "Latest secret: $LATEST_SECRET"
```

## 8. Expected Outputs
- `STATUS: pending-upgrade`: Release locked in in-flight status.
- Latest secret named e.g. `sh.helm.release.v1.<release-name>.v5`.

## 9. Decision Tree
```
                         [Release Stuck in Pending]
                                     |
                         Is CI Job Still Running?
                                     |
         +---------------------------+---------------------------+
         |                                                       |
   [Yes: Job Active]                                       [No: Job Aborted]
         |                                                       |
   Allow job to complete or                                Safely delete or patch
   reach --timeout limit                                   stuck pending release secret
```

## 10. Remediation Steps

### Action A: Delete Stuck Pending Secret (Safe / Standard Recovery)
```bash
# 1. Confirm no active deployment process is running
# 2. Identify the stuck pending release secret (the highest revision)
STUCK_SECRET=$(kubectl get secrets -n <namespace> -l owner=helm,name=<release-name> --sort-by=.metadata.creationTimestamp -o jsonpath='{.items[-1].metadata.name}')

# 3. Delete the stuck secret
kubectl delete secret $STUCK_SECRET -n <namespace>

# 4. Helm will fall back to the previous deployed revision
helm status <release-name> -n <namespace>
```

### Action B: Patch Stuck Secret to 'failed' (Alternative Recovery)
If deleting the secret is disallowed by policy, patch its status from `pending-upgrade` to `failed` using helm rollback or `helm-rollback` plugin.

## 11. Verification After Remediation
1. Verify `helm list -n <namespace>` reports `STATUS: deployed` (or `failed` allowing upgrades).
2. Execute dry-run upgrade to verify lock is cleared:
   `helm upgrade <release-name> <chart-path> -n <namespace> --dry-run`

## 12. Rollback Procedure
If the wrong secret was deleted, restore from cluster backup.

## 13. Escalation Criteria
- If release secrets are permanently corrupted, escalate to Platform SRE to rebuild release state.

## 14. Prevention Recommendations
1. Always specify reasonable timeouts in CI/CD (e.g. `helm upgrade --timeout 5m0s`).
2. Implement graceful termination handling in CI/CD runners to avoid hard SIGKILL.

## 15. Related Documentation References
- Helm Advanced Architecture: `https://helm.sh/docs/topics/advanced/`
