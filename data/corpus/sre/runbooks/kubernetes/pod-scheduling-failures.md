---
title: "Runbook: Pod Scheduling Failures and Affinity/Taint Constraints"
incident_id: "RB-K8S-005"
severity: "P2"
category: "runbooks/kubernetes"
component: "kube-scheduler"
document_type: "runbook"
doc_type: "runbook"
source_type: "project-authored-sre"
applicable_versions: "Kubernetes 1.28-1.37"
authoring_date: "2026-11-08"
spdx_license: "Apache-2.0"
attribution: "Enterprise SRE Copilot Authors"
content_hash: "c56da9f1c4d21c60cf7bd723026177aa191d4da0551f697ef0153566cdd97969"
related_upstream_urls:
  - "https://kubernetes.io/docs/concepts/scheduling-eviction/assign-pod-node/"
  - "https://kubernetes.io/docs/concepts/scheduling-eviction/taint-and-toleration/"
---

# Runbook: Pod Scheduling Failures and Affinity/Taint Constraints

## 1. Incident Identifier
- **ID:** RB-K8S-005
- **Severity:** P2 (High) — Pods fail to schedule due to conflicting node affinity, pod anti-affinity, topology spread, or node taints.

## 2. Severity Classification
- **P1:** Replicas cannot satisfy required anti-affinity across failure domains, causing service outage.
- **P2:** Subset of pods unscheduled; degraded redundancy.
- **P3:** Staging or test environment constraint conflict.

## 3. Symptoms
- Pod events report: `0/N nodes are available: N node(s) didn't match Pod's node affinity/selector, N node(s) had untolerated taint, N node(s) didn't match pod anti-affinity rules`.
- Pod remains in `Pending` state indefinitely despite ample cluster CPU and memory.

## 4. Common Causes
1. **Hard Anti-Affinity Saturation:** `requiredDuringSchedulingIgnoredDuringExecution` anti-affinity configured on `topologyKey: kubernetes.io/hostname`, but replica count exceeds node count.
2. **Missing Node Labels:** Pod requires `topology.kubernetes.io/zone=us-east-1a`, but nodes in that zone were decommissioned or lack the label.
3. **Untolerated Node Taints:** Dedicated node pool tainted with `workload=batch:NoSchedule`, but new pod lacks the toleration.
4. **Topology Spread MaxSkew Violation:** `topologySpreadConstraints` with `whenUnsatisfiable: DoNotSchedule` cannot be fulfilled due to uneven zone distribution.

## 5. Impact Assessment
- Prevented deployment rollouts, scaling events, and high availability distribution.

## 6. Diagnostic Procedure
1. Extract exact affinity and anti-affinity rules from PodSpec.
2. Check existing pod distribution across nodes and topology domains.
3. Inspect taints on all ready worker nodes.

## 7. Read-Only Investigation Commands
```bash
# 1. Inspect affinity, anti-affinity, and topology spread constraints on the failing pod
kubectl get pod <pod-name> -n <namespace> -o jsonpath='{.spec.affinity}' | jq .
kubectl get pod <pod-name> -n <namespace> -o jsonpath='{.spec.topologySpreadConstraints}' | jq .

# 2. Check taints present on all nodes
kubectl get nodes -o custom-columns=NAME:.metadata.name,TAINTS:.spec.taints

# 3. List distribution of existing pods carrying the matching anti-affinity label
kubectl get pods -A -l <affinity-label-key>=<affinity-label-val> -o wide

# 4. Check labels on nodes in target pool
kubectl get nodes -l <nodepool-label> --show-labels
```

## 8. Expected Outputs
- `node(s) didn't match pod anti-affinity rules`: Every available node already hosts a replica matching the anti-affinity label selector.
- `node(s) had untolerated taint`: Nodes have specific taints that the pod does not tolerate.

## 9. Decision Tree
```
                      [Scheduling Constraint Failure]
                                     |
                         Identify Constraint Type
                                     |
         +---------------------------+---------------------------+
         |                                                       |
  [Hard Anti-Affinity]                                    [Untolerated Taint]
         |                                                       |
  More pods than nodes?                                   Is taint intentional?
  - Switch to soft anti-affinity                          - Yes: add toleration to pod
    (preferredDuringScheduling)                           - No: remove taint from node
```

## 10. Remediation Steps

### Action A: Convert Hard Anti-Affinity to Soft Anti-Affinity (Safe)
Modify PodSpec from `requiredDuringScheduling` to `preferredDuringScheduling`:
```yaml
affinity:
  podAntiAffinity:
    preferredDuringSchedulingIgnoredDuringExecution:
    - weight: 100
      podAffinityTerm:
        labelSelector:
          matchExpressions:
          - key: app
            operator: In
            values:
            - <app-name>
        topologyKey: "kubernetes.io/hostname"
```

### Action B: Add Missing Toleration to Pod (Safe)
```yaml
tolerations:
- key: "workload"
  operator: "Equal"
  value: "specialized"
  effect: "NoSchedule"
```

### Action C: Remove Stale Taint from Node (Disruptive — Requires Node Approval)
```bash
# Remove taint if left behind by failed maintenance job
kubectl taint nodes <node-name> <taint-key>:<taint-effect>-
```

## 11. Verification After Remediation
1. Verify pod binds immediately to an eligible node.
2. Verify topology spread constraint score remains balanced across failure domains.

## 12. Rollback Procedure
Re-apply previous deployment manifest if modified scheduling affects traffic distribution:
```bash
kubectl rollout undo deployment/<deployment-name> -n <namespace>
```

## 13. Escalation Criteria
- If hardware constraint requires dedicated node pool expansion, escalate to Capacity / Cloud Engineering.

## 14. Prevention Recommendations
1. Prefer `preferredDuringSchedulingIgnoredDuringExecution` for pod anti-affinity unless strict physical isolation is legally required.
2. Set `whenUnsatisfiable: ScheduleAnyway` in `topologySpreadConstraints` to permit degradation during maintenance.

## 15. Related Documentation References
- Kubernetes Assigning Pods to Nodes: `https://kubernetes.io/docs/concepts/scheduling-eviction/assign-pod-node/`
- Taints and Tolerations: `https://kubernetes.io/docs/concepts/scheduling-eviction/taint-and-toleration/`
