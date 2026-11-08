---
title: "Runbook: Node Pressure and Eviction Triage"
incident_id: "RB-K8S-010"
severity: "P2"
category: "runbooks/kubernetes"
component: "kubelet-eviction"
document_type: "runbook"
doc_type: "runbook"
source_type: "project-authored-sre"
applicable_versions: "Kubernetes 1.28-1.37"
authoring_date: "2026-11-08"
spdx_license: "Apache-2.0"
attribution: "Enterprise SRE Copilot Authors"
content_hash: "19836a8824f465a8faf63499c81c807a504dfb5d6030fca03a99011544c7afbe"
related_upstream_urls:
  - "https://kubernetes.io/docs/concepts/scheduling-eviction/node-pressure-eviction/"
---

# Runbook: Node Pressure and Eviction Triage

## 1. Incident Identifier
- **ID:** RB-K8S-010
- **Severity:** P2 (High) — Node has entered DiskPressure, MemoryPressure, or PIDPressure; kubelet is actively evicting pods.

## 2. Severity Classification
- **P1:** Cascading node evictions across multiple nodes causing service outage.
- **P2:** Single node evicting lower-priority pods.
- **P3:** Temporary disk pressure clearing automatically via image garbage collection.

## 3. Symptoms
- `kubectl get nodes` displays `DiskPressure: True` or `MemoryPressure: True`.
- `kubectl get pods` shows multiple pods with `STATUS: Evicted`.
- Kubelet logs report: `eviction manager: attempting to reclaim resources`.

## 4. Common Causes
1. **Disk Inode or Space Exhaustion:** Container application writing unrotated logs or temp files to container writable layer instead of external volume.
2. **Container Image Accumulation:** High deployment frequency downloading many large container images without prompt garbage collection.
3. **PID Starvation:** Application fork-bomb or runaway thread creation hitting `--pod-max-pids`.
4. **Memory Leak Consuming Node Buffers:** Unbounded memory growth exceeding `imagefs.available` or `memory.available` eviction thresholds.

## 5. Impact Assessment
- BestEffort and Burstable pods terminated abruptly.
- Node unschedulable for new workloads until pressure condition is resolved.

## 6. Diagnostic Procedure
1. Determine which pressure condition is active (`DiskPressure`, `MemoryPressure`, or `PIDPressure`).
2. Identify pods with `Evicted` status and inspect termination messages.
3. Identify disk or memory hog processes on the node.

## 7. Read-Only Investigation Commands
```bash
# 1. Identify active pressure condition on node
kubectl describe node <node-name> | grep -E "(DiskPressure|MemoryPressure|PIDPressure)"

# 2. List all evicted pods in the cluster
kubectl get pods -A --field-selector status.phase=Failed | grep -i evicted

# 3. Check eviction message on an evicted pod
kubectl get pod <evicted-pod-name> -n <namespace> -o jsonpath='{.status.message}'

# 4. Check disk usage per container via crictl (on node)
sudo crictl stats
```

## 8. Expected Outputs
- `The node had condition: [DiskPressure]`: Root or image filesystem fell below configured eviction threshold.
- `The node had condition: [PIDPressure]`: System PID count exhausted.

## 9. Decision Tree
```
                         [Node Pressure Condition]
                                     |
                          Identify Pressure Type
                                     |
         +---------------------------+---------------------------+
         |                                                       |
  [DiskPressure]                                          [PIDPressure]
         |                                                       |
  Clean container logs & images                           Find runaway process:
  - Prune unused images                                   - Increase pids-limit
  - Check /var/log & ephemeral-storage                    - Fix process thread leak
```

## 10. Remediation Steps

### Action A: Clean Up Evicted Pod Objects (Safe)
```bash
# Evicted pods consume API server memory; delete failed pods safely
kubectl delete pods -A --field-selector status.phase=Failed
```

### Action B: Prune Unused Images Manually on Node (Safe)
```bash
# On the node host:
sudo crictl rmi --prune
```

### Action C: Increase Node Disk Size or Scale Additional Nodes (Safe)
```bash
# Cordon node temporarily until pressure clears
kubectl cordon <node-name>
```

## 11. Verification After Remediation
1. Verify node conditions clear:
   `kubectl get node <node-name> -o jsonpath='{.status.conditions}' | jq .`
2. Uncordon the node once all pressures show `False`:
   `kubectl uncordon <node-name>`

## 12. Rollback Procedure
N/A — Eviction cleanup is non-reversive.

## 13. Escalation Criteria
- If persistent container logging causes repeat DiskPressure, escalate to Application Developers to route logs to stdout instead of local filesystem.

## 14. Prevention Recommendations
1. Enforce `resources.limits.ephemeral-storage` in container PodSpecs.
2. Configure log rotation (`max-size`, `max-file`) in container runtime config (`/etc/containerd/config.toml`).
3. Set `imageGCHighThresholdPercent: 80` and `imageGCLowThresholdPercent: 70` in KubeletConfiguration.

## 15. Related Documentation References
- Node-pressure Eviction: `https://kubernetes.io/docs/concepts/scheduling-eviction/node-pressure-eviction/`
