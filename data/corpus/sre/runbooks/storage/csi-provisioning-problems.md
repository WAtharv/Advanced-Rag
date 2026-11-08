---
title: "Runbook: CSI Driver Provisioning and Timeout Failures"
incident_id: "RB-STO-005"
severity: "P1"
category: "runbooks/storage"
component: "csi-driver"
document_type: "runbook"
doc_type: "runbook"
source_type: "project-authored-sre"
applicable_versions: "Kubernetes 1.28-1.37"
authoring_date: "2026-11-08"
spdx_license: "Apache-2.0"
attribution: "Enterprise SRE Copilot Authors"
content_hash: "4c173eca849b5c0168b06c33809ccf2b281cd64e501df77d61905b31325a659d"
related_upstream_urls:
  - "https://kubernetes.io/docs/concepts/storage/volumes/#csi/"
---

# Runbook: CSI Driver Provisioning and Timeout Failures

## 1. Incident Identifier
- **ID:** RB-STO-005
- **Severity:** P1 (Critical) / P2 (High) — Container Storage Interface (CSI) driver controller or node agent failing to provision, attach, or format storage volumes.

## 2. Severity Classification
- **P1:** CSI controller down cluster-wide; all new stateful workloads failing.
- **P2:** Single worker node CSI node-driver-registrar crashed; volumes failing to attach on that host.
- **P3:** CSI metric exporter timeout.

## 3. Symptoms
- PVC events show: `Failed to provision volume with StorageClass ... rpc error: code = DeadlineExceeded desc = context deadline exceeded`.
- Pod events report: `Driver <driver-name> not found in the list of registered CSI drivers`.
- CSI controller logs report HTTP 403 or Unauthorized calling cloud storage APIs.

## 4. Common Causes
1. **CSI IAM / RBAC Credential Expiration:** IRSA (IAM Roles for Service Accounts) or cloud service account key expired.
2. **CSI Node DaemonSet Pod Dead on Host:** Node plugin socket (`/var/lib/kubelet/plugins_registry`) missing or disconnected.
3. **Cloud Storage Rate Limiting:** Cloud provider throttling volume creation API calls during bulk scale-up.
4. **CSI Driver CRD Version Incompatibility:** Cluster upgraded without upgrading corresponding CSI driver CRDs.

## 5. Impact Assessment
- Complete failure of dynamic storage provisioning cluster-wide.

## 6. Diagnostic Procedure
1. Check CSIDriver objects registered in the cluster.
2. Inspect CSI controller Deployment and Node DaemonSet pod health.
3. Inspect CSI controller logs for cloud provider API rejection reasons.

## 7. Read-Only Investigation Commands
```bash
# 1. Verify CSIDriver registration
kubectl get csidrivers

# 2. Check CSI controller and node daemonset pod status
kubectl get pods -n kube-system -l app.kubernetes.io/component=csi-driver
kubectl get pods -A | grep -i csi

# 3. Check logs of CSI provisioner sidecar container
kubectl logs -n kube-system -l app=ebs-csi-controller -c csi-provisioner --tail=100

# 4. Check CSI node plugin logs on failing node
kubectl logs -n kube-system <csi-node-pod-name> -c csi-node-driver-registrar --tail=50
```

## 8. Expected Outputs
- `CSIDriver not found`: CSI driver DaemonSet not running on target node.
- `UnauthorizedOperation / AccessDenied`: Cloud IAM role permissions missing for CSI controller.
- `DeadlineExceeded`: Cloud storage API timeout.

## 9. Decision Tree
```
                          [CSI Provisioning Error]
                                     |
                          Check CSI Controller Logs
                                     |
         +---------------------------+---------------------------+
         |                                                       |
  [IAM / Auth Error]                                      [Pod / DaemonSet Dead]
         |                                                       |
  Update cloud IAM role / ServiceAccount                  Restart CSI controller / Node
  annotations with proper storage policies                DaemonSets across kube-system
```

## 10. Remediation Steps

### Action A: Restart CSI Controller Deployment (Safe)
```bash
kubectl rollout restart deployment -n kube-system -l app.kubernetes.io/name=aws-ebs-csi-driver
```

### Action B: Refresh CSI Driver IAM Credentials (Safe)
```bash
# Verify ServiceAccount has valid IAM annotation
kubectl get sa -n kube-system ebs-csi-controller-sa -o yaml
```

### Action C: Re-register CSI Node Plugin (Safe on host)
```bash
# On affected node host:
sudo systemctl restart kubelet
```

## 11. Verification After Remediation
1. Verify dynamic provisioning test succeeds:
   ```bash
   kubectl run csi-test --rm -it --image=busybox --restart=Never --overrides='{"spec":{"containers":[{"name":"test","image":"busybox","command":["touch","/data/ok"],"volumeMounts":[{"name":"vol","mountPath":"/data"}]}],"volumes":[{"name":"vol","ephemeral":{"volumeClaimTemplate":{"spec":{"accessModes":["ReadWriteOnce"],"resources":{"requests":{"storage":"1Gi"}}}}}}]}}'
   ```
2. Verify test pod completes and terminates cleanly.

## 12. Rollback Procedure
Revert CSI driver helm release or manifest if an upgrade caused the regression:
```bash
helm rollback aws-ebs-csi-driver -n kube-system
```

## 13. Escalation Criteria
- Escalate to Cloud Infrastructure Architect if cloud provider storage control plane has an active outage in the region.

## 14. Prevention Recommendations
1. Manage CSI driver installations via Helm with pinned, tested driver versions.
2. Monitor `csi_operations_seconds_bucket` metrics for API call failure rate.

## 15. Related Documentation References
- CSI Volumes: `https://kubernetes.io/docs/concepts/storage/volumes/#csi/`
