---
title: "Runbook: PersistentVolumeClaim Stuck in Pending"
incident_id: "RB-STO-001"
severity: "P2"
category: "runbooks/storage"
component: "pv-controller"
document_type: "runbook"
doc_type: "runbook"
source_type: "project-authored-sre"
applicable_versions: "Kubernetes 1.28-1.37"
authoring_date: "2026-11-08"
spdx_license: "Apache-2.0"
attribution: "Enterprise SRE Copilot Authors"
content_hash: "06acf8884db1986dc142ed18d3db34862ca470bbb78ebd251726e0f0ff4b1345"
related_upstream_urls:
  - "https://kubernetes.io/docs/concepts/storage/persistent-volumes/"
  - "https://kubernetes.io/docs/tasks/configure-pod-container/configure-persistent-volume-storage/"
---

# Runbook: PersistentVolumeClaim Stuck in Pending

## 1. Incident Identifier
- **ID:** RB-STO-001
- **Severity:** P2 (High) — PersistentVolumeClaim cannot bind or provision volume, leaving consuming pods stuck in `Pending`.

## 2. Severity Classification
- **P1:** Stateful workload pod (database, queue) unable to schedule due to unbound PVC.
- **P2:** New PVC provisioned during deployment upgrade cannot bind.
- **P3:** Test PVC creation delay.

## 3. Symptoms
- `kubectl get pvc` displays `STATUS: Pending` indefinitely.
- Consuming pod event reports: `pod has unbound immediate PersistentVolumeClaims`.
- Alert `PersistentVolumeClaimPending` firing.

## 4. Common Causes
1. **WaitForFirstConsumer Mode:** StorageClass uses `volumeBindingMode: WaitForFirstConsumer`, waiting for a pod to be scheduled first.
2. **Nonexistent StorageClass:** PVC references `storageClassName: fast-ssd` which does not exist in cluster.
3. **No Available PVs (Static Provisioning):** Capacity or access mode mismatch (e.g. PVC requests 50Gi `ReadWriteMany`, but only 20Gi `ReadWriteOnce` PVs exist).
4. **Cloud Storage Quota Exceeded:** Cloud provider EBS/PD storage quota reached in the region.
5. **CSI Driver Failure:** CSI provisioner container crashed or credentials expired.

## 5. Impact Assessment
- Pods requiring persistent storage cannot start; database failover blocked.

## 6. Diagnostic Procedure
1. Check PVC status, annotations, and events.
2. Check if StorageClass exists and inspect its `volumeBindingMode`.
3. Check CSI provisioner logs in `kube-system` or storage namespace.

## 7. Read-Only Investigation Commands
```bash
# 1. Inspect PVC events and failure reason
kubectl describe pvc <pvc-name> -n <namespace> | grep -A 10 "Events:"

# 2. Check StorageClass configuration
kubectl get sc
kubectl describe sc <storage-class-name>

# 3. Check if any unbound PVs exist in the cluster
kubectl get pv | grep -i available

# 4. Check CSI driver pod status
kubectl get pods -n kube-system -l app.kubernetes.io/component=csi-driver
```

## 8. Expected Outputs
- `waiting for first consumer to be created before binding`: Normal behavior for `WaitForFirstConsumer`; schedule the pod to resolve.
- `storageclass.storage.k8s.io "<name>" not found`: StorageClass name typo.
- `failed to provision volume with StorageClass ... cloud quota exceeded`: Cloud provider API error.

## 9. Decision Tree
```
                         [PVC Stuck in Pending]
                                    |
                           Check Describe Events
                                    |
         +--------------------------+--------------------------+
         |                                                     |
  [WaitForFirstConsumer]                                [Provisioning Failed]
         |                                                     |
  Expected behavior:                                    Check CSI logs & StorageClass:
  - Create/Schedule the consuming pod                   - Does StorageClass exist?
  - Verify node has zone matching StorageClass          - Check cloud storage quota
```

## 10. Remediation Steps

### Action A: Schedule Consuming Pod (If WaitForFirstConsumer) (Safe)
If `WaitForFirstConsumer` is configured, the volume will provision automatically once the pod passes scheduling. Verify pod is not blocked by taints.

### Action B: Fix StorageClass Name on PVC (Safe / Requires PVC Re-creation)
```bash
# PVC spec cannot be edited in-place; export, fix storageClassName, and reapply
kubectl get pvc <pvc-name> -n <namespace> -o yaml > /tmp/pvc.yaml
# Edit /tmp/pvc.yaml to set valid storageClassName
kubectl apply -f /tmp/pvc.yaml
```

### Action C: Restart CSI Provisioner Pod (Safe)
```bash
kubectl rollout restart deployment/ebs-csi-controller -n kube-system
```

## 11. Verification After Remediation
1. Verify PVC transitions to `Bound`:
   `kubectl get pvc <pvc-name> -n <namespace>`
2. Verify PV is automatically provisioned and mapped to the PVC.

## 12. Rollback Procedure
N/A — Delete failed unattached PVC if created in error.

## 13. Escalation Criteria
- If cloud provider volume quota is exhausted, escalate to Cloud Admin for storage limit increase.

## 14. Prevention Recommendations
1. Set a cluster-default StorageClass annotated with `storageclass.kubernetes.io/is-default-class: "true"`.
2. Monitor PVC pending duration alerts.

## 15. Related Documentation References
- Persistent Volumes: `https://kubernetes.io/docs/concepts/storage/persistent-volumes/`
