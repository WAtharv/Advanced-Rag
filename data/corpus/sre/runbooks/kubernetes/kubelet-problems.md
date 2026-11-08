---
title: "Runbook: Kubelet Service Degradation and SyncLoop Hangs"
incident_id: "RB-K8S-011"
severity: "P1"
category: "runbooks/kubernetes"
component: "kubelet"
document_type: "runbook"
doc_type: "runbook"
source_type: "project-authored-sre"
applicable_versions: "Kubernetes 1.28-1.37"
authoring_date: "2026-11-08"
spdx_license: "Apache-2.0"
attribution: "Enterprise SRE Copilot Authors"
content_hash: "04d31efbbca5458391053407bf518fcb7598e2104c40226f9c49ea2c1cb49415"
related_upstream_urls:
  - "https://kubernetes.io/docs/reference/command-line-tools-reference/kubelet/"
---

# Runbook: Kubelet Service Degradation and SyncLoop Hangs

## 1. Incident Identifier
- **ID:** RB-K8S-011
- **Severity:** P1 (Critical) / P2 (High) — Kubelet daemon unresponsive, failing to sync pod status or communicate with container runtime.

## 2. Severity Classification
- **P1:** Multiple nodes experiencing Kubelet SyncLoop hangs, freezing pod deployments and health updates.
- **P2:** Single node kubelet degraded.
- **P3:** High latency on kubelet `/metrics` endpoint.

## 3. Symptoms
- Kubelet log warning: `Skipping pod synchronization: PLEG is not healthy`.
- Pod status updates delayed by several minutes.
- `kubectl logs` and `kubectl exec` time out with `Error from server (ServiceUnavailable): the server is currently unable to handle the request`.

## 4. Common Causes
1. **PLEG (Pod Lifecycle Event Generator) Timeout:** Kubelet PLEG loop blocked waiting for container runtime response (containerd hung).
2. **Zombie or Deadlocked Containers:** Container stuck in D-state (uninterruptible disk sleep) due to NFS or network storage lock.
3. **Cgroup Driver Mismatch:** Kubelet configured with `cgroupfs` while containerd uses `systemd`.
4. **Certificate Expiration:** Kubelet client certificate rejected by kube-apiserver.

## 5. Impact Assessment
- Inability to execute commands, view logs, or update pod states on the affected node.

## 6. Diagnostic Procedure
1. Check kubelet systemd service status and journal logs.
2. Check PLEG metrics in Prometheus: `kubelet_pleg_relist_duration_seconds`.
3. Check container runtime responsiveness.

## 7. Read-Only Investigation Commands
```bash
# 1. (From host) Inspect kubelet journal logs for PLEG warnings
sudo journalctl -u kubelet -n 100 --no-pager | grep -i "pleg"

# 2. (From host) Test containerd responsiveness directly
sudo crictl ps -q

# 3. (PromQL) Check PLEG duration latency
# histogram_quantile(0.99, sum(rate(kubelet_pleg_relist_duration_seconds_bucket[5m])) by (le, instance))

# 4. Check API server to kubelet connectivity
kubectl get --raw "/api/v1/nodes/<node-name>/proxy/healthz"
```

## 8. Expected Outputs
- `PLEG is not healthy: pleg was last seen active 3m20s ago; threshold is 3m0s`: Confirms PLEG lockup.
- `crictl ps` hangs indefinitely: Container runtime unresponsive.

## 9. Decision Tree
```
                         [Kubelet PLEG / Sync Issue]
                                      |
                           Is crictl responsive?
                                      |
         +----------------------------+----------------------------+
         |                                                         |
    [crictl Hangs]                                           [crictl OK]
         |                                                         |
  Containerd deadlocked:                                    Check Kubelet config,
  - Inspect D-state storage processes                       cgroup driver, and
  - Restart containerd then kubelet                         memory allocation
```

## 10. Remediation Steps

### Action A: Restart Containerd and Kubelet (Safe on degraded node)
```bash
# On the node host:
sudo systemctl restart containerd
sudo systemctl restart kubelet
```

### Action B: Terminate Stuck I/O Mount Processes (Safe)
```bash
# Find processes in D-state
ps aux | awk '$8 ~ /D/ {print $0}'
```

## 11. Verification After Remediation
1. Verify kubelet health endpoint returns `ok`:
   `curl -s -k https://localhost:10250/healthz`
2. Verify node status in cluster:
   `kubectl get node <node-name>`

## 12. Rollback Procedure
If restarting services fails, cordon and reboot the host:
```bash
kubectl cordon <node-name>
sudo reboot
```

## 13. Escalation Criteria
- Escalate to Kernel / Linux OS engineering if D-state processes cannot be cleared without hard host reset.

## 14. Prevention Recommendations
1. Ensure `cgroupDriver: systemd` is consistently configured in both Kubelet and Containerd.
2. Avoid hosting NFS/CIFS mounts directly inside container rootfs; use official CSI drivers with connection timeouts.

## 15. Related Documentation References
- Kubelet Reference: `https://kubernetes.io/docs/reference/command-line-tools-reference/kubelet/`
