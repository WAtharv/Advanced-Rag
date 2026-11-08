---
title: "Runbook: NodeNotReady and Node Heartbeat Timeout"
incident_id: "RB-K8S-009"
severity: "P1"
category: "runbooks/kubernetes"
component: "node-controller"
document_type: "runbook"
doc_type: "runbook"
source_type: "project-authored-sre"
applicable_versions: "Kubernetes 1.28-1.37"
authoring_date: "2026-11-08"
spdx_license: "Apache-2.0"
attribution: "Enterprise SRE Copilot Authors"
content_hash: "ff8afc94718f3ca8b48548f51a43b198ccfbb4a0e47c0f9085cfb49e42cd4744"
related_upstream_urls:
  - "https://kubernetes.io/docs/concepts/architecture/nodes/"
  - "https://kubernetes.io/docs/tasks/debug/debug-cluster/monitor-node-health/"
---

# Runbook: NodeNotReady and Node Heartbeat Timeout

## 1. Incident Identifier
- **ID:** RB-K8S-009
- **Severity:** P1 (Critical) / P2 (High) — Kubernetes worker node reported `NotReady`, risking pod eviction and capacity loss.

## 2. Severity Classification
- **P1:** Multiple worker nodes `NotReady` simultaneously; cluster-wide outage imminent.
- **P2:** Single worker node `NotReady`; pods evicting to remaining nodes.
- **P3:** Ephemeral spot/preemptible node transitioning during scheduled maintenance.

## 3. Symptoms
- `kubectl get nodes` displays `STATUS: NotReady`.
- Prometheus alert `KubeNodeNotReady` firing.
- Pods on the node transition to `Terminating` or `Unknown` after `node-monitor-grace-period` (default 40s).

## 4. Common Causes
1. **Kubelet Crash or Stop:** `systemd` stopped `kubelet` service due to panic, OOM, or configuration fault.
2. **Container Runtime Failure:** containerd / CRI-O hung or unresponsive to gRPC health checks.
3. **Kernel Hang / System Deadlock:** Heavy I/O blocking kernel threads or host out-of-memory.
4. **Node Network Partition:** Loss of network connectivity between worker node and kube-apiserver.
5. **Disk Full on Root Filesystem:** Root disk (`/`) at 100%, preventing kubelet from writing lease objects or logs.

## 5. Impact Assessment
- Workloads on the affected node are evicted after pod eviction timeout (default 300s).
- Remaining nodes experience increased CPU/memory pressure from evicted workloads.

## 6. Diagnostic Procedure
1. Check node conditions and exact transition timestamp.
2. Check node lease renewals in `kube-node-lease` namespace.
3. Access node host OS (via SSH or cloud serial console) to inspect system services.

## 7. Read-Only Investigation Commands
```bash
# 1. Inspect node status and conditions
kubectl describe node <node-name> | grep -A 8 "Conditions:"

# 2. Check node lease renewal timestamp
kubectl get lease <node-name> -n kube-node-lease -o yaml

# 3. Check pods currently running on the NotReady node
kubectl get pods -A --field-selector spec.nodeName=<node-name> -o wide

# 4. (From host) Check kubelet and containerd systemd service status
systemctl status kubelet
systemctl status containerd

# 5. (From host) Check disk space on root and container storage filesystems
df -h
```

## 8. Expected Outputs
- `Kubeletstopped posting node status`: Kubelet or container runtime process is down or partitioned.
- `DiskPressure: True`: Filesystem threshold breached.
- `systemctl status containerd` shows `active (running)` or `failed`.

## 9. Decision Tree
```
                         [Node in NotReady State]
                                     |
                          Check Host Reachability
                                     |
        +----------------------------+----------------------------+
        |                                                         |
  [Host Reachable via SSH]                                  [Host Unreachable]
        |                                                         |
  Check Services & Disk:                                    Cloud VM / Hardware failure:
  - Restart kubelet / containerd                            - Terminate & replace node via ASG
  - Clean disk space if full                                - Verify cloud status check
```

## 10. Remediation Steps

### Action A: Restart Kubelet and Container Runtime (Safe on affected host)
```bash
# On the affected node host:
sudo systemctl restart containerd
sudo systemctl restart kubelet
```

### Action B: Free Space on Exhausted Root Disk (Safe)
```bash
# Clean unused container images and stopped containers
sudo crictl rmi --prune
sudo journalctl --vacuum-time=1d
```

### Action C: Drain Node and Replace in Cloud Provider (Disruptive — Requires Approval)
```bash
# 1. Cordon the node immediately to prevent new scheduling
kubectl cordon <node-name>

# 2. If node remains dead, trigger replacement in Cloud Autoscaling Group
```

## 11. Verification After Remediation
1. Verify node status returns to `Ready`:
   `kubectl get nodes <node-name>`
2. Check conditions: `Ready=True`, `MemoryPressure=False`, `DiskPressure=False`.

## 12. Rollback Procedure
If node cannot be restored within 15 minutes, terminate the VM and let autoscaler launch a fresh instance.

## 13. Escalation Criteria
- If multiple nodes enter `NotReady` simultaneously, escalate as P1 Major Incident (likely network partition, control plane failure, or certificate expiration).

## 14. Prevention Recommendations
1. Configure Node Problem Detector (NPD) to remediate common kernel deadlocks.
2. Allocate adequate Kubelet and System Reserved resources (`--system-reserved`, `--kube-reserved`).
3. Set up disk space monitoring alerts at 80% utilization on root filesystems.

## 15. Related Documentation References
- Node Architecture: `https://kubernetes.io/docs/concepts/architecture/nodes/`
- Monitor Node Health: `https://kubernetes.io/docs/tasks/debug/debug-cluster/monitor-node-health/`
