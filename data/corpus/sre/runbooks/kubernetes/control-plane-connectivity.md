---
title: "Runbook: Control-Plane and Kube-APIServer Unreachability"
incident_id: "RB-K8S-013"
severity: "P1"
category: "runbooks/kubernetes"
component: "kube-apiserver"
document_type: "runbook"
doc_type: "runbook"
source_type: "project-authored-sre"
applicable_versions: "Kubernetes 1.28-1.37"
authoring_date: "2026-11-08"
spdx_license: "Apache-2.0"
attribution: "Enterprise SRE Copilot Authors"
content_hash: "6f834c961a8416c479be7f40a6839832cee023cd3348197a2c0da4e3ffaf0f6f"
related_upstream_urls:
  - "https://kubernetes.io/docs/concepts/architecture/control-plane-node-communication/"
---

# Runbook: Control-Plane and Kube-APIServer Unreachability

## 1. Incident Identifier
- **ID:** RB-K8S-013
- **Severity:** P1 (Critical) — Kubernetes API server unreachable or returning 500/504 errors; cluster management offline.

## 2. Severity Classification
- **P1:** Total loss of `kubectl` and API access across the entire cluster.
- **P2:** Intermittent API latency or single API server replica degraded in HA setup.

## 3. Symptoms
- `kubectl` commands fail with: `Unable to connect to the server: dial tcp <ip>:6443: connect: connection refused` or `i/o timeout`.
- Cloud load balancer health checks failing for port 6443.
- Prometheus alert `KubeAPIErrorBudgetBurn` firing.

## 4. Common Causes
1. **Etcd Quorum Loss / Performance Saturation:** Etcd leader lost, slow disk fsync latency causing leader elections, or etcd out of space.
2. **API Server OOM / CPU Starvation:** Aggressive controllers or unindexed queries overwhelming kube-apiserver memory.
3. **Control Plane Load Balancer Failure:** Cloud LB health check failure dropping all control plane instances.
4. **Certificate Expiration:** Kubernetes API server or etcd peer certificates expired.

## 5. Impact Assessment
- Complete loss of cluster orchestration; existing running pods continue to serve, but no new pods, auto-scaling, or rollouts can occur.

## 6. Diagnostic Procedure
1. Check control plane host reachability.
2. Verify etcd cluster health.
3. Check kube-apiserver container status on master nodes.

## 7. Read-Only Investigation Commands
```bash
# 1. Test direct API server endpoint bypassing load balancer
curl -k -m 5 -Iv https://<control-plane-node-ip>:6443/healthz

# 2. Check etcd health (on control-plane host)
sudo etcdctl --cacert=/etc/kubernetes/pki/etcd/ca.crt   --cert=/etc/kubernetes/pki/etcd/server.crt   --key=/etc/kubernetes/pki/etcd/server.key   --endpoints=https://127.0.0.1:2379 endpoint health

# 3. Check etcd alarms (e.g. NOSPACE)
sudo etcdctl --cacert=/etc/kubernetes/pki/etcd/ca.crt   --cert=/etc/kubernetes/pki/etcd/server.crt   --key=/etc/kubernetes/pki/etcd/server.key   --endpoints=https://127.0.0.1:2379 alarm list

# 4. Check container status of static control plane pods on host
sudo crictl ps | grep -E "(kube-apiserver|etcd)"
```

## 8. Expected Outputs
- `endpoint health: healthy`: Etcd is operational.
- `alarm:NOSPACE`: Etcd has hit its database quota (default 2GB or 8GB).
- `connection refused` on 6443: kube-apiserver process is dead.

## 9. Decision Tree
```
                         [Kube-APIServer Unreachable]
                                       |
                              Check Etcd Health
                                       |
         +-----------------------------+-----------------------------+
         |                                                           |
  [Etcd Alarm: NOSPACE]                                       [Etcd Healthy]
         |                                                           |
  Compact & defrag etcd:                                      Check Kube-APIServer logs:
  - etcdctl defrag                                            - Check OOM / CPU saturation
  - etcdctl alarm disarm                                      - Verify certificates
```

## 10. Remediation Steps

### Action A: Defragment Etcd and Clear NOSPACE Alarm (Safe on Control Plane)
```bash
# 1. Compact etcd revision
REV=$(sudo etcdctl --cacert=/etc/kubernetes/pki/etcd/ca.crt --cert=/etc/kubernetes/pki/etcd/server.crt --key=/etc/kubernetes/pki/etcd/server.key endpoint status --write-out=json | jq -r '.[0].Status.header.revision')
sudo etcdctl --cacert=/etc/kubernetes/pki/etcd/ca.crt --cert=/etc/kubernetes/pki/etcd/server.crt --key=/etc/kubernetes/pki/etcd/server.key compact $REV

# 2. Defragment etcd database
sudo etcdctl --cacert=/etc/kubernetes/pki/etcd/ca.crt --cert=/etc/kubernetes/pki/etcd/server.crt --key=/etc/kubernetes/pki/etcd/server.key defrag

# 3. Disarm alarm
sudo etcdctl --cacert=/etc/kubernetes/pki/etcd/ca.crt --cert=/etc/kubernetes/pki/etcd/server.crt --key=/etc/kubernetes/pki/etcd/server.key alarm disarm
```

### Action B: Restart Control Plane Static Pods (Safe)
```bash
# Moving manifest out and back triggers kubelet to recreate static pod
sudo mv /etc/kubernetes/manifests/kube-apiserver.yaml /tmp/
sleep 5
sudo mv /tmp/kube-apiserver.yaml /etc/kubernetes/manifests/
```

## 11. Verification After Remediation
1. Verify API server health:
   `kubectl get --raw /healthz` (returns `ok`).
2. Verify node status across cluster:
   `kubectl get nodes`.

## 12. Rollback Procedure
Restore etcd snapshot if database corruption occurs.

## 13. Escalation Criteria
- If etcd quorum is irrevocably lost, declare P1 Critical Incident and initiate Disaster Recovery Etcd Restore from backup.

## 14. Prevention Recommendations
1. Deploy 3 or 5 control plane replicas distributed across availability zones.
2. Place etcd data directory on dedicated high-IOPS NVMe disks with write barrier enabled.
3. Configure daily automated etcd snapshot backups with automated restore testing.

## 15. Related Documentation References
- Control Plane Communication: `https://kubernetes.io/docs/concepts/architecture/control-plane-node-communication/`
