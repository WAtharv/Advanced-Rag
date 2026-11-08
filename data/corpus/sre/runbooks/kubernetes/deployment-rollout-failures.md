---
title: "Runbook: Deployment Rollout Stalled or Failed"
incident_id: "RB-K8S-007"
severity: "P1"
category: "runbooks/kubernetes"
component: "deployment-controller"
document_type: "runbook"
doc_type: "runbook"
source_type: "project-authored-sre"
applicable_versions: "Kubernetes 1.28-1.37"
authoring_date: "2026-11-08"
spdx_license: "Apache-2.0"
attribution: "Enterprise SRE Copilot Authors"
content_hash: "fdb1cffd54e5b14913830c24d5a64269a2820571a2d3abaa6b93b015e719b6d7"
related_upstream_urls:
  - "https://kubernetes.io/docs/concepts/workloads/controllers/deployment/"
---

# Runbook: Deployment Rollout Stalled or Failed

## 1. Incident Identifier
- **ID:** RB-K8S-007
- **Severity:** P1 (Critical) / P2 (High) — Automated deployment rollout progress is blocked, leaving workload in mixed revision state.

## 2. Severity Classification
- **P1:** Rollout failure during emergency fix or production release with customer-facing downtime.
- **P2:** Rollout timeout exceeded (`ProgressDeadlineExceeded`) while old revision continues serving.
- **P3:** Staging environment rollout failure.

## 3. Symptoms
- `kubectl rollout status deployment/<name>` reports: `Waiting for deployment "<name>" rollout to finish: 1 out of 3 new replicas have been updated...`
- `ProgressDeadlineExceeded` condition in Deployment status.
- New ReplicaSet cannot scale up while old ReplicaSet cannot scale down.

## 4. Common Causes
1. **New Pods Failing Startup / Probes:** New pods enter `CrashLoopBackOff`, `ImagePullBackOff`, or fail readiness checks.
2. **Quota or Scheduling Blockage:** Insufficient node resources to satisfy `maxSurge` allocation.
3. **PDB (PodDisruptionBudget) Blocking Eviction:** Strict PDB preventing old pods from being drained.
4. **Hook or Init Container Failure:** Database migration init container crashing or timing out.

## 5. Impact Assessment
- Incomplete deployment; cluster running split-brain code versions simultaneously.
- CI/CD pipeline blocked waiting for deployment confirmation.

## 6. Diagnostic Procedure
1. Inspect deployment rollout status and conditions.
2. Identify new vs old ReplicaSet.
3. Check pod status under the new ReplicaSet.

## 7. Read-Only Investigation Commands
```bash
# 1. Check current rollout status
kubectl rollout status deployment/<deployment-name> -n <namespace>

# 2. List ReplicaSets associated with deployment
kubectl get rs -n <namespace> -l app=<app-label>

# 3. Check deployment conditions for ProgressDeadlineExceeded
kubectl get deployment <deployment-name> -n <namespace> -o jsonpath='{.status.conditions[*]}' | jq .

# 4. Check pods belonging to the new ReplicaSet
kubectl get pods -n <namespace> -l app=<app-label> --sort-by=.metadata.creationTimestamp
```

## 8. Expected Outputs
- `ProgressDeadlineExceeded: Deployment does not have minimum availability`: Indicates new revision could not become ready within `progressDeadlineSeconds`.

## 9. Decision Tree
```
                     [Deployment Rollout Stalled]
                                  |
                      Check New Pod Replica Status
                                  |
        +-------------------------+-------------------------+
        |                                                   |
  [New Pods Crashing / Unready]                       [Cannot Schedule (Pending)]
        |                                                   |
  Immediate Mitigation:                               Check Resource Quota / Nodes
  Execute Rollout Undo to prior revision              Adjust maxSurge / maxUnavailable
```

## 10. Remediation Steps

### Action A: Immediate Rollback to Previous Stable Revision (Safe / Recommended)
```bash
# Rollback immediately to restore pipeline and cluster consistency
kubectl rollout undo deployment/<deployment-name> -n <namespace>
```

### Action B: Pause Rollout for In-Place Investigation (Safe)
```bash
# Pause rollout so deployment controller stops attempting further pod churn
kubectl rollout pause deployment/<deployment-name> -n <namespace>
```

### Action C: Resume Rollout after Fixing Dependency (Safe)
```bash
kubectl rollout resume deployment/<deployment-name> -n <namespace>
```

## 11. Verification After Remediation
1. Verify rollout status confirms complete success:
   ```bash
   kubectl rollout status deployment/<deployment-name> -n <namespace>
   # Output: deployment "<name>" successfully rolled out
   ```
2. Confirm old ReplicaSet is scaled to 0 and all target replicas run on the desired revision.

## 12. Rollback Procedure
If rollback command itself fails, explicitly target revision number:
```bash
kubectl rollout history deployment/<deployment-name> -n <namespace>
kubectl rollout undo deployment/<deployment-name> -n <namespace> --to-revision=<known-healthy-rev>
```

## 13. Escalation Criteria
- If database schema migration was partially applied, escalate to Application DBA on-call before rolling back application.

## 14. Prevention Recommendations
1. Set `progressDeadlineSeconds` appropriately (e.g. 600s).
2. Configure `maxSurge: 25%` and `maxUnavailable: 0` for zero-downtime rolling deployments.
3. Run canary deployments or staging validation before production promotion.

## 15. Related Documentation References
- Kubernetes Deployments: `https://kubernetes.io/docs/concepts/workloads/controllers/deployment/`
