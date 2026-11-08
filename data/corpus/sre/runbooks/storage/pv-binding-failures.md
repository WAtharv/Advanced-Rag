---
title: "Runbook: PV Binding and StorageClass Mismatches"
incident_id: "RB-STO-002"
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
content_hash: "6ad14fb3a2cc16b51634f5fd246c0cda84fdd96299a4f19e12dbce4ca7e47ca5"
related_upstream_urls:
  - "https://kubernetes.io/docs/concepts/storage/storage-classes/"
---

# Runbook: PV Binding and StorageClass Mismatches

## 1. Incident Identifier
- **ID:** RB-STO-002
- **Severity:** P2 (High) — Pre-created PersistentVolume rejected during binding due to attribute mismatch with PersistentVolumeClaim.

## 2. Severity Classification
- **P1:** Data migration or database restore blocked by static PV binding failure.
- **P2:** Static PV fails to bind to target PVC; capacity idle.

## 3. Symptoms
- PVC remains `Pending` despite an `Available` PV existing with matching storage capacity.
- PV events report: `cannot match volume: access modes, storage class, or selectors do not match`.

## 4. Common Causes
1. **AccessModes Incompatibility:** PVC requests `ReadWriteMany` (RWX), but PV only supports `ReadWriteOnce` (RWO).
2. **StorageClass Mismatch:** PV defines `storageClassName: manual`, but PVC specifies empty string or different class name.
3. **VolumeMode Incompatibility:** PV is `Block` mode while PVC requests `Filesystem` mode.
4. **Capacity Insufficiency:** PVC requests `100Gi`, but available PV is only `99Gi`.

## 5. Impact Assessment
- Static storage pre-allocated for database restoration cannot be mounted.

## 6. Diagnostic Procedure
1. Compare attributes of the unattached PV and the pending PVC side-by-side.
2. Check access modes, capacity, storageClassName, and volumeMode.

## 7. Read-Only Investigation Commands
```bash
# 1. Compare PV and PVC specifications
kubectl get pv <pv-name> -o yaml
kubectl get pvc <pvc-name> -n <namespace> -o yaml

# 2. Check access modes of all Available PVs
kubectl get pv -o custom-columns=NAME:.metadata.name,CAPACITY:.spec.capacity.storage,CLASS:.spec.storageClassName,ACCESS:.spec.accessModes[*],STATUS:.status.phase
```

## 8. Expected Outputs
- PV `accessModes: [ReadWriteOnce]` vs PVC `accessModes: [ReadWriteMany]`: Incompatible access mode.
- PV `storageClassName: standard` vs PVC `storageClassName: ""`: Mismatched class.

## 9. Decision Tree
```
                         [PV Binding Mismatch]
                                   |
                       Compare PV vs PVC Specs
                                   |
         +-------------------------+-------------------------+
         |                                                   |
  [AccessMode Mismatch]                               [StorageClass Mismatch]
         |                                                   |
  Block storage cannot do RWX:                        Align storageClassName on
  - Use NFS / Ceph / EFS CSI for RWX                  both objects to exact string match
```

## 10. Remediation Steps

### Action A: Update PV StorageClassName or Labels (Safe)
```bash
# Patch PV storageClassName if manual binding is required
kubectl patch pv <pv-name> -p '{"spec":{"storageClassName":"<target-class>"}}'
```

### Action B: Explicitly Bind PVC to Specific PV (Safe)
```yaml
# In PVC spec, specify exact volumeName:
spec:
  volumeName: <exact-pv-name>
  storageClassName: <matching-class>
  resources:
    requests:
      storage: 100Gi
```

## 11. Verification After Remediation
1. Verify both PV and PVC enter `Bound` status:
   `kubectl get pv <pv-name>`
   `kubectl get pvc <pvc-name> -n <namespace>`

## 12. Rollback Procedure
Release PV claimRef if binding needs to be retried:
```bash
kubectl patch pv <pv-name> -p '{"spec":{"claimRef": null}}'
```

## 13. Escalation Criteria
- Escalate to Storage Administrator if multi-node write (RWX) storage backend needs provisioning.

## 14. Prevention Recommendations
1. Use dynamic storage provisioning via StorageClasses instead of manual static PV management.

## 15. Related Documentation References
- Storage Classes: `https://kubernetes.io/docs/concepts/storage/storage-classes/`
