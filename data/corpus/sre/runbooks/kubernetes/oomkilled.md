---
title: "Runbook: Pod OOMKilled and Memory Pressure Eviction"
incident_id: "RB-K8S-003"
severity: "P1"
category: "runbooks/kubernetes"
component: "cgroups-memory"
document_type: "runbook"
doc_type: "runbook"
source_type: "project-authored-sre"
applicable_versions: "Kubernetes 1.28-1.37"
authoring_date: "2026-11-08"
spdx_license: "Apache-2.0"
attribution: "Enterprise SRE Copilot Authors"
content_hash: "615a209cc670c8b6a5ecd0745f25f1bec153e15bfeb3fa6cafe42b8a412c3c8b"
related_upstream_urls:
  - "https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/"
  - "https://kubernetes.io/docs/tasks/configure-pod-container/assign-memory-resource/"
---

# Runbook: Pod OOMKilled and Memory Pressure Eviction

## 1. Incident Identifier
- **ID:** RB-K8S-003
- **Severity:** P1 (Critical) / P2 (High) — Container terminated by Linux kernel OOM killer due to exceeding memory limit or node memory exhaustion.

## 2. Severity Classification
- **P1:** Multiple core production workload pods OOMKilled in succession due to traffic spike or memory leak.
- **P2:** Occasional background batch job or single pod OOMKilled without impacting customer requests.
- **P3:** Staging environment memory limit tuning.

## 3. Symptoms
- `kubectl get pods` displays `STATUS: OOMKilled` or `CrashLoopBackOff` with exit code `137`.
- Prometheus alert `KubePodOOMKilled` or `ContainerOOMKilled` firing.
- Kernel dmesg logs report `Memory cgroup out of memory: Kill process ... score ...`.

## 4. Common Causes
1. **Unrealistic Memory Limits:** Container memory `limits` configured too low for real production traffic heap size.
2. **Application Memory Leak:** In-memory cache accumulation, unclosed connections, or growing object graphs.
3. **JVM / Runtime Heap vs Container Boundary:** Java/Node.js unaware of container cgroup limits, allocating heap beyond container boundary.
4. **Sudden Concurrency Burst:** Large spike in concurrent requests increasing working set memory faster than garbage collection.
5. **Node-Level Memory Starvation:** Node memory exhaustion leading kubelet to evict pods (`Evicted` status due to `The node had condition: [MemoryPressure]`).

## 5. Impact Assessment
- Abrupt process termination dropping active TCP connections and transactions.
- Potential cluster-wide cascade if traffic fails over to remaining replicas, causing them to OOMKill in turn.

## 6. Diagnostic Procedure
1. Confirm termination reason is specifically `OOMKilled`.
2. Inspect current `requests` and `limits` memory allocations.
3. Review Prometheus historical memory usage leading up to termination.
4. Check whether eviction was container-level (cgroup limit) or node-level (node memory exhaustion).

## 7. Read-Only Investigation Commands
```bash
# 1. Verify OOMKilled status and exit code 137
kubectl get pod <pod-name> -n <namespace> -o jsonpath='{range .status.containerStatuses[*]}{.name}{"	ExitCode:"}{.lastState.terminated.exitCode}{"	Reason:"}{.lastState.terminated.reason}{"
"}{end}'

# 2. Inspect configured memory requests and limits
kubectl get pod <pod-name> -n <namespace> -o jsonpath='{range .spec.containers[*]}{.name}{"	Requests:"}{.resources.requests.memory}{"	Limits:"}{.resources.limits.memory}{"
"}{end}'

# 3. Check node memory pressure condition
kubectl get node <node-name> -o jsonpath='{.status.conditions[?(@.type=="MemoryPressure")].status}'

# 4. Check kernel dmesg for OOM killer invocations on the host node (if node access available)
kubectl describe node <node-name> | grep -A 5 "Allocated resources"

# 5. PromQL query: Historical working set memory usage leading to crash
# container_memory_working_set_bytes{namespace="<namespace>",pod="<pod-name>"}
```

## 8. Expected Outputs
- `ExitCode: 137 Reason: OOMKilled`: Confirms cgroup memory boundary exceeded.
- `MemoryPressure: True`: Confirms node-level memory exhaustion triggering evictions.
- `container_memory_working_set_bytes` steadily climbing linearly over hours indicates a memory leak.
- `container_memory_working_set_bytes` surging instantaneously during traffic spike indicates undersized limit.

## 9. Decision Tree
```
                         [Container Exited 137 / OOMKilled]
                                         |
                       Check Scope: Container vs Node
                                         |
         +-------------------------------+-------------------------------+
         |                                                               |
  [Container cgroup OOM]                                        [Node MemoryPressure]
         |                                                               |
  Inspect Memory Trend:                                          Identify High-Memory Pods:
  - Linear steady growth -> Memory Leak (Profile App)            - Move batch jobs off node
  - Spike during load -> Increase Memory Limit                   - Add cluster node capacity
```

## 10. Remediation Steps

### Action A: Increase Container Memory Limit (Safe / Requires Pod Restart)
```bash
# Patch deployment to increase memory limit from e.g. 512Mi to 1Gi
kubectl set resources deployment/<deployment-name> -n <namespace>   --limits=memory=1Gi --requests=memory=512Mi
```

### Action B: Tune JVM / Runtime Heap Ergonomics (Safe)
```bash
# Configure JVM MaxRAMPercentage to 75% so JVM reserves headroom for native memory
kubectl set env deployment/<deployment-name> -n <namespace>   JAVA_TOOL_OPTIONS="-XX:MaxRAMPercentage=75.0 -XX:+UseG1GC"
```

### Action C: Emergency Horizontal Scaling to Shed Load (Safe)
```bash
# Scale up replicas to divide incoming request load across more containers
kubectl scale deployment/<deployment-name> -n <namespace> --replicas=<increased-count>
```

## 11. Verification After Remediation
1. Verify new pods run without restarts under current traffic:
   ```bash
   kubectl get pods -n <namespace> -l app=<app-label>
   ```
2. Monitor memory working set via PromQL:
   `container_memory_working_set_bytes{namespace="<namespace>",pod=~"<deployment-name>-.*"} / container_spec_memory_limit_bytes`
   Ensure utilization remains below 80%.

## 12. Rollback Procedure
If increasing memory causes node scheduling failures due to insufficient node allocatable memory:
```bash
kubectl rollout undo deployment/<deployment-name> -n <namespace>
```

## 13. Escalation Criteria
- If memory leak is confirmed in application code, escalate to App Development team with memory dump / profiling data.
- If cluster nodes are entirely out of allocatable memory, escalate to Infra SRE to provision additional worker nodes.

## 14. Prevention Recommendations
1. Always set memory `requests` equal to memory `limits` for critical latency-sensitive workloads to guarantee QoS class `Guaranteed`.
2. Configure Vertical Pod Autoscaler (VPA) in recommendation mode to analyze real-world memory utilization.
3. Configure alert rules for `container_memory_working_set_bytes / container_spec_memory_limit_bytes > 0.85` to catch saturation before OOM.

## 15. Related Documentation References
- Managing Memory Resources: `https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/`
- Core Knowledge Base: `data/corpus/kubernetes/troubleshooting/determine-pod-failure-reason.md`
