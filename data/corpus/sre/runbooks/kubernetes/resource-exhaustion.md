---
title: "Runbook: Cluster Resource Exhaustion and Quota Limits"
incident_id: "RB-K8S-012"
severity: "P1"
category: "runbooks/kubernetes"
component: "quota-admission"
document_type: "runbook"
doc_type: "runbook"
source_type: "project-authored-sre"
applicable_versions: "Kubernetes 1.28-1.37"
authoring_date: "2026-11-08"
spdx_license: "Apache-2.0"
attribution: "Enterprise SRE Copilot Authors"
content_hash: "4f5a1c8666c963327a0d898c95198db31b75fb9a85653a71ae57542a0d2916a7"
related_upstream_urls:
  - "https://kubernetes.io/docs/concepts/policy/resource-quotas/"
  - "https://kubernetes.io/docs/concepts/policy/limit-range/"
---

# Runbook: Cluster Resource Exhaustion and Quota Limits

## 1. Incident Identifier
- **ID:** RB-K8S-012
- **Severity:** P1 (Critical) / P2 (High) — Namespace or cluster-wide compute resources exhausted, blocking all pod creation.

## 2. Severity Classification
- **P1:** Production namespace cannot scale up during traffic spike due to ResourceQuota exhaustion.
- **P2:** Non-production namespace blocked from deployments.
- **P3:** LimitRange mismatch on single container.

## 3. Symptoms
- `kubectl create` or rollout fails with `admission webhook or ResourceQuota denied the request: exceeded quota: ...`.
- Horizontal Pod Autoscaler (HPA) cannot provision new replicas.
- Prometheus alert `KubeResourceQuotaExhausted` firing.

## 4. Common Causes
1. **Unbounded HPA Scaling:** HPA scaled up pods to maximum limit, consuming all quota allocated to namespace.
2. **Oversized Resource Requests:** Workloads requesting 4 CPU cores while actually utilizing 100 millicores.
3. **Orphaned Pods / Jobs:** Completed Jobs or dead pods holding quota allocations.
4. **Cluster Node Pool Capacity Limit:** Cloud autoscaler reached maximum allowed nodes (`max_size`).

## 5. Impact Assessment
- Inability to deploy updates, scale out services, or recover failed replicas.

## 6. Diagnostic Procedure
1. Check target namespace ResourceQuota status (Used vs Hard).
2. Check total cluster CPU and memory commit ratio.
3. Identify top consuming deployments in the namespace.

## 7. Read-Only Investigation Commands
```bash
# 1. Check ResourceQuota usage in target namespace
kubectl get resourcequota -n <namespace> -o yaml

# 2. List top pods by CPU and Memory usage
kubectl top pods -n <namespace> --sort-by=memory

# 3. List top deployments by requested resources
kubectl get deployment -n <namespace> -o custom-columns=NAME:.metadata.name,CPU_REQ:.spec.template.spec.containers[*].resources.requests.cpu,MEM_REQ:.spec.template.spec.containers[*].resources.requests.memory

# 4. Check cluster-autoscaler status configmap
kubectl get configmap cluster-autoscaler-status -n kube-system -o yaml
```

## 8. Expected Outputs
- `used: requests.cpu: 64, hard: requests.cpu: 64`: Namespace quota ceiling saturated.
- `cluster-autoscaler-status` reporting `MaxNodesReached`: Cloud provider cluster size ceiling hit.

## 9. Decision Tree
```
                         [Resource Quota Exhaustion]
                                      |
                         Where is the bottleneck?
                                      |
         +----------------------------+----------------------------+
         |                                                         |
  [Namespace ResourceQuota]                                 [Cluster Node Autoscaler]
         |                                                         |
  Oversized requests or dead pods?                          Max nodes reached?
  - Delete failed jobs/pods                                 - Increase autoscaler max_size
  - Right-size requests                                     - Request cloud quota increase
  - Increase ResourceQuota                                  
```

## 10. Remediation Steps

### Action A: Clean Up Completed Jobs and Failed Pods (Safe)
```bash
# Free up quota reserved by completed jobs
kubectl delete jobs --field-selector status.successful=1 -n <namespace>
kubectl delete pods --field-selector status.phase=Failed -n <namespace>
```

### Action B: Temporarily Increase Namespace Quota (Requires Approval)
```bash
# Patch quota to permit emergency scale-up
kubectl patch resourcequota <quota-name> -n <namespace> --type=merge -p '{"spec":{"hard":{"requests.cpu":"128"}}}'
```

### Action C: Right-Size Workload Requests (Safe)
```bash
# Downsize idle deployment requests to match realistic usage
kubectl set resources deployment/<deployment-name> -n <namespace> --requests=cpu=500m,memory=1Gi
```

## 11. Verification After Remediation
1. Verify quota usage reflects headroom:
   `kubectl get resourcequota -n <namespace>`
2. Confirm stalled pods transition to `Running`.

## 12. Rollback Procedure
Revert quota manifest once emergency passes.

## 13. Escalation Criteria
- If cloud provider instance quota (e.g. AWS vCPU limits) is reached, escalate to Cloud FinOps / Platform Lead.

## 14. Prevention Recommendations
1. Implement Vertical Pod Autoscaler (VPA) in recommendation mode to guide right-sizing.
2. Configure automatic cleanup of completed jobs via `ttlSecondsAfterFinished`.

## 15. Related Documentation References
- Resource Quotas: `https://kubernetes.io/docs/concepts/policy/resource-quotas/`
