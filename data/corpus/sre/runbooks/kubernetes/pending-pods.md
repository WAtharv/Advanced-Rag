---
title: "Runbook: Pod Stuck in Pending State"
incident_id: "RB-K8S-004"
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
content_hash: "c9010f02e366e6ac003eb1df743b9dbf2c9f663db83a06406a76a258f4192ed4"
related_upstream_urls:
  - "https://kubernetes.io/docs/concepts/scheduling-eviction/kube-scheduler/"
  - "https://kubernetes.io/docs/tasks/debug/debug-application/debug-pods/"
---

# Runbook: Pod Stuck in Pending State

## 1. Incident Identifier
- **ID:** RB-K8S-004
- **Severity:** P2 (High) — Pod created in API server but cannot be scheduled or initialized on any cluster node.

## 2. Severity Classification
- **P1:** Critical service scaling blocked; cannot provision replacement pods during outage.
- **P2:** Routine deployment rollout delayed because new pods cannot find schedulable nodes.
- **P3:** Development or batch workload pending resource availability.

## 3. Symptoms
- `kubectl get pods` shows `STATUS: Pending` for longer than 60 seconds.
- Pod is not bound to any node (`NODE: <none>`).
- Alert `KubePodPending` firing for prolonged duration.

## 4. Common Causes
1. **Insufficient Cluster Resources:** Total requested CPU or memory exceeds allocatable capacity across all worker nodes.
2. **Node Affinity / NodeSelector Mismatch:** Pod requests labels (e.g. `disktype=ssd`, `topology.kubernetes.io/zone=us-east-1a`) that match 0 ready nodes.
3. **Taints and Tolerations:** Worker nodes have taints (e.g. `dedicated=gpu:NoSchedule`, `node.kubernetes.io/unreachable`) without matching pod tolerations.
4. **PersistentVolumeClaim Pending:** Pod mounts a PVC that has not yet bound to a PV or volume provisioning is blocked.
5. **ResourceQuota Exceeded:** Namespace has hit its quota limit for total CPU, memory, or pod count.

## 5. Impact Assessment
- Deployment updates stall in progress.
- Redundancy compromised if existing pods terminate without replacement pods coming online.

## 6. Diagnostic Procedure
1. Check pod events for scheduler rejection explanation.
2. Check if the pod specifies volume mounts waiting for PVC binding.
3. Inspect namespace ResourceQuota usage.
4. Check node schedulability, taints, and available allocatable capacity.

## 7. Read-Only Investigation Commands
```bash
# 1. Inspect scheduler failure reason from pod events
kubectl describe pod <pod-name> -n <namespace> | grep -A 10 "Events:"

# 2. Check PVC status if pod mounts persistent storage
kubectl get pvc -n <namespace>

# 3. Check ResourceQuota usage in target namespace
kubectl get resourcequota -n <namespace> -o yaml

# 4. Check cluster-wide node allocatable resources vs requested allocations
kubectl describe nodes | grep -A 8 "Allocated resources:"

# 5. Check if nodes are cordoned or have taints
kubectl get nodes -o custom-columns=NAME:.metadata.name,SCHEDULABLE:.spec.unschedulable,TAINTS:.spec.taints
```

## 8. Expected Outputs
- `0/12 nodes are available: 12 Insufficient cpu`: Cluster CPU exhaustion.
- `0/12 nodes are available: 3 node(s) had untolerated taint, 9 node(s) didn't match Pod's node affinity/selector`: Scheduling constraint mismatch.
- `pod has unbound immediate PersistentVolumeClaims`: Storage provisioning issue.
- `exceeded quota: compute-resources, requested: requests.cpu=2, used: requests.cpu=15, limited: requests.cpu=16`: Namespace quota ceiling reached.

## 9. Decision Tree
```
                         [Pod Stuck in Pending]
                                    |
                          Inspect Pod Events
                                    |
      +---------------------+-------+---------------------+
      |                     |                             |
[Insufficient CPU/Mem]  [Taint/Affinity Mismatch]     [PVC Unbound]
      |                     |                             |
Scale Cluster Nodes or  Review nodeSelector/taints;   Inspect StorageClass / CSI
adjust pod requests     add tolerations or fix label  driver provisioning logs
```

## 10. Remediation Steps

### Action A: Right-Size Excessive Pod Requests (Safe)
```bash
# If requested resources are oversized compared to actual usage, adjust downward
kubectl set resources deployment/<deployment-name> -n <namespace>   --requests=cpu=250m,memory=512Mi
```

### Action B: Uncordon Schedulable Worker Nodes (Safe)
```bash
# If nodes were left cordoned after maintenance:
kubectl uncordon <node-name>
```

### Action C: Increase Namespace ResourceQuota (Requires Team Lead Approval)
```bash
# Patch namespace resource quota if justified by business requirements
kubectl patch resourcequota <quota-name> -n <namespace> --type=merge -p '{"spec":{"hard":{"requests.cpu":"32"}}}'
```

### Action D: Trigger Cluster Autoscaler Scale-Up (Safe)
```bash
# Verify cluster-autoscaler is running and healthy
kubectl get pods -n kube-system -l app=cluster-autoscaler
```

## 11. Verification After Remediation
1. Verify pod is scheduled to a node:
   ```bash
   kubectl get pod <pod-name> -n <namespace> -o wide
   ```
2. Verify pod transitions through `ContainerCreating` to `Running`.

## 12. Rollback Procedure
If modifying requests or quota causes unwanted cluster contention:
```bash
kubectl apply -f <previous-manifest-backup.yaml>
```

## 13. Escalation Criteria
- If cloud provider rejects cluster autoscaler node requests due to quota/stockout, escalate to Cloud Infrastructure on-call.
- If PVC provisioning is permanently failed, escalate to Storage SRE (see RB-STO-001).

## 14. Prevention Recommendations
1. Establish cluster autoscaling with multiple instance types across availability zones.
2. Implement ResourceQuotas alongside LimitRanges to enforce sensible default requests.
3. Use Pod Topology Spread Constraints instead of hard pod anti-affinities to avoid unresolvable scheduling deadlocks.

## 15. Related Documentation References
- Kubernetes Scheduler Concepts: `https://kubernetes.io/docs/concepts/scheduling-eviction/kube-scheduler/`
- Debugging Pods: `https://kubernetes.io/docs/tasks/debug/debug-application/debug-pods/`
