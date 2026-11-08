---
title: "Runbook: Persistent Volume Filesystem Exhaustion"
incident_id: "RB-STO-004"
severity: "P1"
category: "runbooks/storage"
component: "volume-resize"
document_type: "runbook"
doc_type: "runbook"
source_type: "project-authored-sre"
applicable_versions: "Kubernetes 1.28-1.37"
authoring_date: "2026-11-08"
spdx_license: "Apache-2.0"
attribution: "Enterprise SRE Copilot Authors"
content_hash: "9e340b10a126e16335acc4b078dddf6f1c1a3c9aafb0b1ef57c2bc848df4603c"
related_upstream_urls:
  - "https://kubernetes.io/docs/concepts/storage/persistent-volumes/#expanding-persistent-volumes-claims/"
---

# Runbook: Persistent Volume Filesystem Exhaustion

## 1. Incident Identifier
- **ID:** RB-STO-004
- **Severity:** P1 (Critical) — Persistent volume filesystem is 100% full, causing application write failures or crashloops.

## 2. Severity Classification
- **P1:** Database (Postgres/MySQL/MongoDB) entered read-only mode or crashed due to `No space left on device`.
- **P2:** Application logging volume full, degrading performance.
- **P3:** Storage utilization >85% without immediate service crash.

## 3. Symptoms
- Application logs report: `java.io.IOException: No space left on device` or `Database is in read-only mode`.
- Prometheus alert `KubePersistentVolumeFillingUp` firing (>90% full).
- Pod restarts repeatedly with database write errors.

## 4. Common Causes
1. **Unplanned Data Growth:** Unexpected database write volume, heavy transaction log (WAL) accumulation.
2. **Missing Volume Expansion Policy:** StorageClass does not have `allowVolumeExpansion: true`.
3. **Application Temp / Log Accumulation:** Application writing temporary files to data volume instead of ephemeral storage.
4. **Filesystem Inode Exhaustion:** Millions of microscopic files exhausting inode table despite available byte capacity.

## 5. Impact Assessment
- Complete interruption of write transactions; potential database transaction journal corruption if unhandled.

## 6. Diagnostic Procedure
1. Verify filesystem capacity and inode utilization.
2. Check if StorageClass allows online volume expansion.
3. Identify top space-consuming directories inside the volume.

## 7. Read-Only Investigation Commands
```bash
# 1. Check disk space and inode utilization inside container
kubectl exec <pod-name> -n <namespace> -- df -h <mount-path>
kubectl exec <pod-name> -n <namespace> -- df -i <mount-path>

# 2. Identify top 10 largest directories inside the mount
kubectl exec <pod-name> -n <namespace> -- du -ah <mount-path> | sort -rh | head -n 10

# 3. Check if StorageClass permits volume expansion
SC_NAME=$(kubectl get pvc <pvc-name> -n <namespace> -o jsonpath='{.spec.storageClassName}')
kubectl get sc $SC_NAME -o jsonpath='{.allowVolumeExpansion}'
```

## 8. Expected Outputs
- `Use%: 100%`: Volume byte capacity saturated.
- `IUse%: 100%`: Inode capacity saturated (too many small files).
- `allowVolumeExpansion: true`: Online volume resize supported.

## 9. Decision Tree
```
                         [Volume Storage Exhaustion]
                                      |
                          Is Inodes or Bytes Full?
                                      |
         +----------------------------+----------------------------+
         |                                                         |
  [Bytes Full (df -h = 100%)]                              [Inodes Full (df -i = 100%)]
         |                                                         |
  Online Volume Expansion:                                 Clean up orphaned small files
  - Increase PVC storage size in-place                     (temp files, unrotated logs)
  - Verify filesystem expands automatically
```

## 10. Remediation Steps

### Action A: Perform Online Volume Expansion on PVC (Safe / Non-disruptive)
```bash
# 1. Expand PVC capacity from e.g. 50Gi to 100Gi
kubectl patch pvc <pvc-name> -n <namespace> --type=merge -p '{"spec":{"resources":{"requests":{"storage":"100Gi"}}}}'

# 2. Check PVC condition for FileSystemResizePending
kubectl describe pvc <pvc-name> -n <namespace> | grep -E "(FileSystemResizePending|Resizing)"
```

### Action B: Emergency Cleanup of Orphaned Temp Files (Safe)
```bash
# If container is still running, purge temporary dumps or old rotated logs
kubectl exec <pod-name> -n <namespace> -- find <mount-path>/tmp -type f -mtime +2 -delete
```

### Action C: Re-enable Volume Expansion on StorageClass (Safe)
```bash
# If StorageClass lacked expansion, enable it:
kubectl patch sc <storage-class-name> -p '{"allowVolumeExpansion": true}'
```

## 11. Verification After Remediation
1. Verify expanded storage reflects inside container without restarting pod:
   `kubectl exec <pod-name> -n <namespace> -- df -h <mount-path>`
2. Confirm database write operations resume successfully.

## 12. Rollback Procedure
PVC storage sizes cannot be shrunk once expanded (unsupported by underlying cloud block storage).

## 13. Escalation Criteria
- If database crashed during WAL write and cannot start due to corrupted transaction log, escalate to Database SRE for recovery.

## 14. Prevention Recommendations
1. Always enable `allowVolumeExpansion: true` on all production StorageClasses.
2. Establish Prometheus alerts for `predict_linear(kubelet_volume_stats_available_bytes[1h], 8 * 3600) < 0` (predicts disk fill within 8 hours).

## 15. Related Documentation References
- Expanding Persistent Volumes: `https://kubernetes.io/docs/concepts/storage/persistent-volumes/#expanding-persistent-volumes-claims/`
