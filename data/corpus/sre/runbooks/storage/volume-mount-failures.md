---
title: "Runbook: VolumeAttachment Timeout and Multi-Attach Errors"
incident_id: "RB-STO-003"
severity: "P1"
category: "runbooks/storage"
component: "volume-attachment"
document_type: "runbook"
doc_type: "runbook"
source_type: "project-authored-sre"
applicable_versions: "Kubernetes 1.28-1.37"
authoring_date: "2026-11-08"
spdx_license: "Apache-2.0"
attribution: "Enterprise SRE Copilot Authors"
content_hash: "79ec70da9c41682ab28f3f97ee28ce29a416496aa9a3374dd19e3216ad493b49"
related_upstream_urls:
  - "https://kubernetes.io/docs/tasks/administer-cluster/safely-drain-node/"
---

# Runbook: VolumeAttachment Timeout and Multi-Attach Errors

## 1. Incident Identifier
- **ID:** RB-STO-003
- **Severity:** P1 (Critical) — Pod stuck in `ContainerCreating` due to cloud volume attachment timeout or `Multi-Attach error`.

## 2. Severity Classification
- **P1:** Production database or stateful pod cannot start after node crash.
- **P2:** Delayed rolling deployment of single stateful workload.

## 3. Symptoms
- Pod remains in `ContainerCreating` for >10 minutes.
- Pod events report: `Multi-Attach error for volume ... Volume is already exclusively attached to one node and can't be attached to another`.
- Kubelet event: `MountVolume.SetUp failed for volume ... target path ... device or resource busy`.

## 4. Common Causes
1. **Unclean Node Termination:** Previous host node crashed hard without executing volume detachment API calls.
2. **Cloud Detach Latency:** AWS EBS / GCP PD takes up to 6 minutes to force-detach from unresponsive VM.
3. **Stale VolumeAttachment Object:** Kubernetes API server retains `VolumeAttachment` record for dead node.
4. **Filesystem Mount Lock / Corruption:** Previous container crashed holding exclusive lock or filesystem requires `fsck`.

## 5. Impact Assessment
- Single-replica stateful services suffer extended downtime during failovers.

## 6. Diagnostic Procedure
1. Check `VolumeAttachment` objects for the stuck volume.
2. Verify status of the previous node that held the volume.
3. Check cloud console to verify physical volume attachment state.

## 7. Read-Only Investigation Commands
```bash
# 1. Identify PV name mounted by failing pod
PV_NAME=$(kubectl get pvc <pvc-name> -n <namespace> -o jsonpath='{.spec.volumeName}')

# 2. Check VolumeAttachment objects for this PV
kubectl get volumeattachment | grep $PV_NAME

# 3. Inspect pod events for exact multi-attach error
kubectl describe pod <pod-name> -n <namespace> | grep -E "(Multi-Attach|FailedAttach|MountVolume)"

# 4. Check status of the previous node hosting the attachment
kubectl get node <old-node-name>
```

## 8. Expected Outputs
- `Multi-Attach error for volume "pvc-xxx": Volume is already exclusively attached to node "node-1"`: VolumeAttachment stuck on node-1.

## 9. Decision Tree
```
                         [Multi-Attach Volume Error]
                                      |
                           Is Previous Node Alive?
                                      |
         +----------------------------+----------------------------+
         |                                                         |
    [Node is ALIVE]                                           [Node is DEAD]
         |                                                         |
  DO NOT force detach:                                      Safe to detach:
  - Drain node gracefully                                   - Delete stale VolumeAttachment
  - Restart kubelet on old node                             - Wait for CSI attachment to new node
```

## 10. Remediation Steps

### Action A: Gracefully Drain Previous Node (Safe)
```bash
# If old node is alive, drain it to cleanly detach volume
kubectl drain <old-node-name> --ignore-daemonsets --delete-emptydir-data
```

### Action B: Force Remove VolumeAttachment ONLY AFTER Node Shutdown (Disruptive — Requires L2 Approval)
> [!CAUTION]
> Verify in cloud provider console (AWS/GCP/Azure) that the old VM is completely STOPPED or TERMINATED before proceeding.

```bash
# 1. Confirm VM power state is STOPPED in cloud provider
# 2. Delete the stuck VolumeAttachment object in Kubernetes
kubectl get volumeattachment -o json | jq -r '.items[] | select(.spec.source.persistentVolumeName=="'$PV_NAME'") | .metadata.name' | xargs -r kubectl delete volumeattachment

# 3. Delete and recreate the pending pod to re-trigger attachment
kubectl delete pod <pod-name> -n <namespace> --grace-period=0 --force
```

## 11. Verification After Remediation
1. Verify pod transitions to `Running`:
   `kubectl get pod <pod-name> -n <namespace>`
2. Verify volume mounted inside container:
   `kubectl exec <pod-name> -n <namespace> -- df -h <mount-path>`

## 12. Rollback Procedure
N/A — Volume attachment recovery is forward-reconciling.

## 13. Escalation Criteria
- If cloud volume reports `I/O error` or filesystem is read-only after attachment, escalate to Storage Admin for `fsck` snapshot recovery.

## 14. Prevention Recommendations
1. Enable `NonGracefulNodeShutdown` feature gate (standard in modern Kubernetes) to automate failover fencing.
2. Upgrade cloud CSI drivers to support automated force-detach timeouts.

## 15. Related Documentation References
- Safely Drain Nodes: `https://kubernetes.io/docs/tasks/administer-cluster/safely-drain-node/`
