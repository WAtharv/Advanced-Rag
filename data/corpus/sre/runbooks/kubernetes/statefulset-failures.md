---
title: "Runbook: StatefulSet Pod Partitioning and Volume Attachment Failures"
incident_id: "RB-K8S-008"
severity: "P1"
category: "runbooks/kubernetes"
component: "statefulset-controller"
document_type: "runbook"
doc_type: "runbook"
source_type: "project-authored-sre"
applicable_versions: "Kubernetes 1.28-1.37"
authoring_date: "2026-11-08"
spdx_license: "Apache-2.0"
attribution: "Enterprise SRE Copilot Authors"
content_hash: "c2a0504bead879d95e0406e9637cd4a2de0da8646ebd0c30dd7609e969daa874"
related_upstream_urls:
  - "https://kubernetes.io/docs/concepts/workloads/controllers/statefulset/"
  - "https://kubernetes.io/docs/tasks/run-application/run-replicated-stateful-application/"
---

# Runbook: StatefulSet Pod Partitioning and Volume Attachment Failures

## 1. Incident Identifier
- **ID:** RB-K8S-008
- **Severity:** P1 (Critical) — StatefulSet ordinal pod cannot progress, attach persistent volume, or achieve quorum.

## 2. Severity Classification
- **P1:** Primary database / messaging broker pod (e.g., PostgreSQL, Kafka, Elasticsearch) down or split-brain.
- **P2:** Secondary replica in clustered StatefulSet down; cluster operating without redundancy.
- **P3:** StatefulSet maintenance rollout paused.

## 3. Symptoms
- StatefulSet rollout blocked at ordinal `N` (e.g., `kafka-0` running, `kafka-1` pending).
- Pod events report: `Multi-Attach error for volume ... Volume is already exclusively attached to one node`.
- Storage volume cannot detach from terminated node.

## 4. Common Causes
1. **Volume Multi-Attach Conflict:** Node holding previous pod crashed uncleanly; cloud storage controller has not detached EBS/PD volume.
2. **Strict OrderedReady Policy:** `podManagementPolicy: OrderedReady` blocks ordinal `N+1` if ordinal `N` is not ready.
3. **PVC Provisioning Failure for Specific Ordinal:** Dedicated volume claim template failed to bind PV.
4. **Network Partition / Quorum Loss:** Clustered software refuses to start because majority consensus cannot be established.

## 5. Impact Assessment
- Stateful database or queue downtime; possible data inconsistency if forced deletion is performed improperly.

## 6. Diagnostic Procedure
1. Check StatefulSet status and current ordinal progress.
2. Inspect VolumeAttachment objects for stuck attachments.
3. Inspect pod logs for application quorum state.

## 7. Read-Only Investigation Commands
```bash
# 1. Check StatefulSet rollout and replica status
kubectl get statefulset <name> -n <namespace> -o wide

# 2. Inspect specific failed ordinal pod and events
kubectl describe pod <name>-<ordinal> -n <namespace> | grep -E "(FailedAttach|Multi-Attach|VolumeAttachment)"

# 3. Check VolumeAttachment objects across the cluster
kubectl get volumeattachment | grep <pv-name>

# 4. Check PVCs bound to this StatefulSet
kubectl get pvc -n <namespace> -l app=<app-label>
```

## 8. Expected Outputs
- `VolumeAttachment has not been deleted after 10m`: Cloud provider storage detach timeout.
- `OrderedReady`: Preceding ordinal pod not ready, blocking progress.

## 9. Decision Tree
```
                         [StatefulSet Ordinal Blocked]
                                       |
                           Is it a VolumeAttach Error?
                                       |
         +-----------------------------+-----------------------------+
         |                                                           |
   [Yes: Multi-Attach]                                       [No: App Quorum]
         |                                                           |
   Verify old node is DEAD:                                   Inspect cluster peers:
   - Safe detach if node confirmed down                       - Check headless service DNS
   - NEVER force delete on live node!                         - Resolve quorum split
```

## 10. Remediation Steps

### Action A: Safe Recovery from Stuck VolumeAttachment (Disruptive — Requires L2 Approval)
> [!CAUTION]
> NEVER force-delete a StatefulSet pod while the underlying node is still alive. Doing so can cause dual-mount data corruption (split-brain).

```bash
# 1. Verify old node is cordoned and powered off
kubectl describe node <old-node>

# 2. If node is confirmed terminated in cloud provider, delete the stuck VolumeAttachment
kubectl get volumeattachment -o json | jq -r '.items[] | select(.spec.nodeName=="<old-node>") | .metadata.name' | xargs -r kubectl delete volumeattachment

# 3. If pod remains Terminating on dead node, force-delete ONLY AFTER confirming node is dead:
kubectl delete pod <name>-<ordinal> -n <namespace> --force --grace-period=0
```

### Action B: Switch Pod Management Policy to Parallel for Non-Ordered Workloads (Safe)
```yaml
# In StatefulSet spec:
spec:
  podManagementPolicy: Parallel
```

## 11. Verification After Remediation
1. Verify ordinal pod attaches volume successfully:
   `kubectl get pod <name>-<ordinal> -n <namespace>`
2. Confirm clustered application reaches healthy consensus (e.g. `etcdctl endpoint health` or `nodetool status`).

## 12. Rollback Procedure
If rolling update of StatefulSet fails:
```bash
kubectl rollout undo statefulset/<name> -n <namespace>
```

## 13. Escalation Criteria
- Escalate immediately to Database / Storage Administrator if potential filesystem corruption is suspected.

## 14. Prevention Recommendations
1. Ensure cloud provider CSI drivers are updated to versions supporting automated non-graceful node shutdown volume detachment.
2. Configure `podDisruptionBudget` allowing maximum 1 replica unavailable.

## 15. Related Documentation References
- StatefulSets: `https://kubernetes.io/docs/concepts/workloads/controllers/statefulset/`
- Force Delete StatefulSet Pods: `https://kubernetes.io/docs/tasks/run-application/force-delete-stateful-set-pod/`
