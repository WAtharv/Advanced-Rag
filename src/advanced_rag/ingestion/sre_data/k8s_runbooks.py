"""Kubernetes Workload and Node/Cluster Operational Runbooks (RB-K8S-001 through RB-K8S-014).

Covers:
- CrashLoopBackOff, ImagePullBackOff, OOMKilled, Pending pods, Scheduling failures
- Probe failures, Deployment rollout failures, StatefulSet failures
- NodeNotReady, Node pressure & eviction, Kubelet problems, Resource exhaustion
- Control-plane connectivity, Certificate expiration
"""

from __future__ import annotations

K8S_RUNBOOKS: list[dict[str, any]] = [
    {
        "category": "runbooks/kubernetes",
        "name": "crashloopbackoff.md",
        "title": "Runbook: Pod CrashLoopBackOff Triage and Resolution",
        "incident_id": "RB-K8S-001",
        "severity": "P2",
        "component": "pod-lifecycle",
        "applicable_versions": "Kubernetes 1.28-1.37",
        "related_upstream_urls": [
            "https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/",
            "https://kubernetes.io/docs/tasks/debug/debug-application/determine-pod-failure-reason/",
        ],
        "content": """# Runbook: Pod CrashLoopBackOff Triage and Resolution

## 1. Incident Identifier
- **ID:** RB-K8S-001
- **Severity:** P2 (High) — Workload pod continuously crashing and restarting with exponential backoff delay.

## 2. Severity Classification
- **P1:** If >50% of application replicas in a production service are in `CrashLoopBackOff`, degrading user traffic.
- **P2:** Single replica failure in multi-replica deployment, or background worker pod failure.
- **P3:** Staging or non-critical test namespace pod failure.

## 3. Symptoms
- `kubectl get pods` shows `STATUS: CrashLoopBackOff` with restart count steadily incrementing (e.g., 5, 12, 45).
- Pod status oscillates between `Running` for a few seconds and `CrashLoopBackOff`.
- Alert triggered: `KubePodCrashLooping` firing for target workload.

## 4. Common Causes
1. **Application Runtime Panic / Uncaught Exception:** Code failure on startup, missing dependency, or unhandled null pointer.
2. **Missing Configuration / Secrets:** Missing required environment variables, invalid config file path, or unparsed JSON/YAML.
3. **Database / Downstream Unreachability:** Application startup probe or database connection timeout aborting initialization.
4. **Incorrect Container Command / Entrypoint:** Misconfigured `command` or `args` in PodSpec pointing to a nonexistent binary.
5. **Permission / File Access Denial:** Container running as non-root user attempting to write to root-owned volume or directory.

## 5. Impact Assessment
- Potential reduction in service capacity, increased latency on remaining healthy replicas.
- Risk of cascading failures if remaining replicas take excess traffic.
- Stale connections and incomplete transaction rollbacks.

## 6. Diagnostic Procedure
Follow this sequence strictly:
1. Identify the exact failing container within the pod.
2. Inspect the exit code and termination reason.
3. Retrieve container logs from both the current instance and the previous crashed instance.
4. Inspect recent cluster events affecting the pod.

## 7. Read-Only Investigation Commands
Run these read-only commands in order:

```bash
# 1. Inspect pod status, container exit codes, and restart count
kubectl get pod <pod-name> -n <namespace> -o wide

# 2. Extract detailed container termination state and last state
kubectl get pod <pod-name> -n <namespace> -o jsonpath='{range .status.containerStatuses[*]}{.name}{"\tExitCode:"}{.lastState.terminated.exitCode}{"\tReason:"}{.lastState.terminated.reason}{"\tMessage:"}{.lastState.terminated.message}{"\n"}{end}'

# 3. Retrieve logs from the crashed container instance before restart
kubectl logs <pod-name> -n <namespace> -c <container-name> --previous --tail=100

# 4. Stream current logs if the container runs briefly before exiting
kubectl logs <pod-name> -n <namespace> -c <container-name> --tail=50

# 5. Check Kubernetes events for the pod (OOM, probe failures, killed signals)
kubectl get events -n <namespace> --field-selector involvedObject.name=<pod-name> --sort-by='.lastTimestamp'
```

## 8. Expected Outputs
- **Exit Code 1:** General application error / uncaught runtime exception (inspect `--previous` logs for stack traces).
- **Exit Code 137:** Process received SIGKILL (`kill -9`), typically triggered by kernel OOM killer or liveness probe failure timeout.
- **Exit Code 143:** Process received SIGTERM (`kill -15`), terminated gracefully by Kubernetes or external orchestrator.
- **Exit Code 126 / 127:** Command invoked cannot execute or binary not found in container filesystem.
- **Exit Code 0:** Container completed and exited successfully, but `restartPolicy` is `Always`, causing Kubernetes to restart it.

## 9. Decision Tree
```
                         [Pod in CrashLoopBackOff]
                                     |
                    Inspect Exit Code & Previous Logs
                                     |
        +----------------------------+----------------------------+
        |                            |                            |
  [Exit Code 137]              [Exit Code 127]              [Exit Code 1]
        |                            |                            |
  Check OOMKilled:             Binary not found in           Application stack trace:
  - If OOM: raise memory       image; verify entrypoint      - ConfigMap/Secret missing?
  - If Liveness: check probe   and PodSpec command           - DB connection refused?
```

## 10. Remediation Steps
Choose the path indicated by the decision tree:

### Path A: Missing ConfigMap or Secret Key (Safe / Non-disruptive)
```bash
# 1. Check if referenced ConfigMaps and Secrets exist
kubectl get configmap,secret -n <namespace>

# 2. Update missing key or ConfigMap, then trigger rollout restart
kubectl rollout restart deployment/<deployment-name> -n <namespace>
```

### Path B: Transient Downstream Dependency (Safe)
```bash
# Verify downstream service accessibility from within cluster network
kubectl run netcheck --rm -it --image=curlimages/curl --restart=Never -n <namespace> -- curl -m 5 -Iv http://<service-name>:<port>/health
```

### Path C: Rollback to Last Known Healthy Revision (Disruptive — Requires L2 Approval)
```bash
# 1. Check revision history
kubectl rollout history deployment/<deployment-name> -n <namespace>

# 2. Rollback to previous working revision
kubectl rollout undo deployment/<deployment-name> -n <namespace>
```

## 11. Verification After Remediation
1. Verify pod reaches `Running` state and passes readiness checks:
   ```bash
   kubectl get pods -n <namespace> -l app=<app-label> -w
   ```
2. Confirm restart count does not increment for at least 10 minutes.
3. Validate application health endpoint returns HTTP 200 OK.

## 12. Rollback Procedure
If the remediation fails or introduces new errors:
```bash
kubectl rollout undo deployment/<deployment-name> -n <namespace> --to-revision=<healthy-revision-number>
```

## 13. Escalation Criteria
- If root cause is a database schema incompatibility or code bug, escalate to Application Dev On-Call.
- If storage volume permission prevents startup, escalate to Platform/Infra SRE.
- If pod continues crashlooping after 15 minutes of triage, escalate incident to P1 if user impact is detected.

## 14. Prevention Recommendations
1. Implement robust startup probes (`startupProbe`) with adequate failure threshold for slow-starting applications.
2. Validate environment variables and configuration files at build time or using admission webhooks.
3. Set appropriate `terminationGracePeriodSeconds` to permit in-flight shutdown handlers to exit cleanly.
4. Establish synthetic canary deployments before routing 100% traffic to new container images.

## 15. Related Documentation References
- Kubernetes Pod Lifecycle: `https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/`
- Determine Pod Failure Reason: `https://kubernetes.io/docs/tasks/debug/debug-application/determine-pod-failure-reason/`
- Core Knowledge Base: `data/corpus/kubernetes/troubleshooting/determine-pod-failure-reason.md`
""",
    },
    {
        "category": "runbooks/kubernetes",
        "name": "imagepullbackoff.md",
        "title": "Runbook: ImagePullBackOff and ErrImagePull Triage",
        "incident_id": "RB-K8S-002",
        "severity": "P2",
        "component": "kubelet-cri",
        "applicable_versions": "Kubernetes 1.28-1.37",
        "related_upstream_urls": [
            "https://kubernetes.io/docs/concepts/containers/images/",
            "https://kubernetes.io/docs/tasks/configure-pod-container/pull-image-private-registry/",
        ],
        "content": """# Runbook: ImagePullBackOff and ErrImagePull Triage

## 1. Incident Identifier
- **ID:** RB-K8S-002
- **Severity:** P2 (High) — Container runtime cannot pull the designated image, preventing pod startup.

## 2. Severity Classification
- **P1:** New deployment rollout blocked during emergency patch or automated auto-scaling failure during traffic surge.
- **P2:** Stalled deployment update or canary pod unable to pull image while existing pods remain functional.
- **P3:** Staging or development cluster image pull failure.

## 3. Symptoms
- `kubectl get pods` shows `STATUS: ImagePullBackOff` or `ErrImagePull`.
- Deployment rollouts stall indefinitely waiting for new replica pods to initialize.
- Kubelet event logs report `Failed to pull image ... rpc error: code = NotFound` or `401 Unauthorized`.

## 4. Common Causes
1. **Typo in Image Tag or Name:** Incorrect image registry URL, repository name, or nonexistent tag (e.g., `v1.2.3` instead of `v1.2.4`).
2. **Missing or Expired ImagePullSecret:** PodSpec lacks `imagePullSecrets`, or docker-registry secret credentials have expired.
3. **Registry Outage or Rate Limiting:** Docker Hub / ECR / GCR / Harbor throttling or network outage.
4. **Node Network / DNS Failure:** Node hosting the pod cannot resolve registry hostname or route outbound internet traffic.
5. **Architecture Mismatch:** Container image built for `linux/arm64` scheduled on a `linux/amd64` node or vice versa.

## 5. Impact Assessment
- Deployment rollouts stall, preventing code releases or security patches from propagating.
- Cluster auto-scaler may scale up nodes that cannot start pods, wasting compute resources.

## 6. Diagnostic Procedure
1. Inspect the pod's `Events` block for exact CRI error message.
2. Verify image tag existence in target container registry.
3. Verify presence and validity of `imagePullSecrets`.
4. Verify node outbound connectivity to container registry.

## 7. Read-Only Investigation Commands
```bash
# 1. Retrieve the exact failure reason from pod events
kubectl describe pod <pod-name> -n <namespace> | grep -E "(Image|Failed|Error|Back-off)"

# 2. Inspect the exact image string specified in PodSpec
kubectl get pod <pod-name> -n <namespace> -o jsonpath='{.spec.containers[*].image}'

# 3. Check configured imagePullSecrets on the pod
kubectl get pod <pod-name> -n <namespace> -o jsonpath='{.spec.imagePullSecrets[*].name}'

# 4. Check if the specified Secret exists and is populated
kubectl get secret <secret-name> -n <namespace> -o jsonpath='{.data.\\.dockerconfigjson}' | base64 --decode | jq .

# 5. Check if the default ServiceAccount in the namespace has imagePullSecrets attached
kubectl get serviceaccount default -n <namespace> -o yaml
```

## 8. Expected Outputs
- `rpc error: code = NotFound desc = failed to pull and unpack image ... not found`: The tag or repository does not exist in registry.
- `rpc error: code = Unknown desc = failed to pull and unpack image ... 401 Unauthorized`: Missing or invalid registry credentials.
- `rpc error: code = Unknown desc = failed to pull ... net/http: TLS handshake timeout`: Node DNS or firewall blocking egress to registry.
- `no matching manifest for linux/amd64 in the manifest list entries`: Multi-arch build missing target architecture.

## 9. Decision Tree
```
                    [ImagePullBackOff Detected]
                                 |
                     Inspect Describe Pod Events
                                 |
         +-----------------------+-----------------------+
         |                       |                       |
   [404 Not Found]         [401 Unauthorized]       [TLS/DNS Timeout]
         |                       |                       |
   Verify CI/CD pipeline   Check imagePullSecrets;  Test node egress DNS:
   built & pushed tag      renew registry tokens    check security groups
```

## 10. Remediation Steps

### Action A: Fix Image Tag in Deployment (Safe)
```bash
# Update container image to the correct existing tag
kubectl set image deployment/<deployment-name> <container-name>=<valid-image-url>:<valid-tag> -n <namespace>
```

### Action B: Recreate or Patch ImagePullSecret (Safe)
```bash
# 1. Recreate docker-registry secret with fresh auth credentials
kubectl create secret docker-registry <secret-name> \
  --docker-server=<registry-server> \
  --docker-username=<user> \
  --docker-password=<token> \
  --docker-email=<email> \
  -n <namespace> --dry-run=client -o yaml | kubectl apply -f -

# 2. Patch Deployment or ServiceAccount to utilize the secret
kubectl patch serviceaccount default -n <namespace> -p '{"imagePullSecrets": [{"name": "'<secret-name>'"}]}'
```

### Action C: Force Kubelet Re-evaluation (Safe)
```bash
# Delete the failing pod so deployment controller launches fresh pod with updated secrets
kubectl delete pod <pod-name> -n <namespace>
```

## 11. Verification After Remediation
1. Confirm pod transitions from `ContainerCreating` directly to `Running`:
   ```bash
   kubectl get pod -n <namespace> -l app=<app-label> -w
   ```
2. Verify image pull event reports `Successfully pulled image ... in 4.2s`.

## 12. Rollback Procedure
If newly specified image introduces regression:
```bash
kubectl rollout undo deployment/<deployment-name> -n <namespace>
```

## 13. Escalation Criteria
- If registry is internal (Harbor/Artifactory) and unreachable, escalate to DevOps / Infrastructure team.
- If third-party public registry is throttling entire cluster IP range, escalate to Network/Security team to configure pull-through caching mirror.

## 14. Prevention Recommendations
1. Enforce automated image build & push verification in CI before initiating Kubernetes deployment manifests.
2. Avoid using `latest` tag; always use immutable semantic versions or git commit SHA tags.
3. Configure in-cluster registry mirrors (e.g., Spegel, Harbor pull-through cache) to prevent public registry rate-limiting.

## 15. Related Documentation References
- Kubernetes Container Images: `https://kubernetes.io/docs/concepts/containers/images/`
- Private Registry Auth: `https://kubernetes.io/docs/tasks/configure-pod-container/pull-image-private-registry/`
""",
    },
    {
        "category": "runbooks/kubernetes",
        "name": "oomkilled.md",
        "title": "Runbook: Pod OOMKilled and Memory Pressure Eviction",
        "incident_id": "RB-K8S-003",
        "severity": "P1",
        "component": "cgroups-memory",
        "applicable_versions": "Kubernetes 1.28-1.37",
        "related_upstream_urls": [
            "https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/",
            "https://kubernetes.io/docs/tasks/configure-pod-container/assign-memory-resource/",
        ],
        "content": """# Runbook: Pod OOMKilled and Memory Pressure Eviction

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
kubectl get pod <pod-name> -n <namespace> -o jsonpath='{range .status.containerStatuses[*]}{.name}{"\tExitCode:"}{.lastState.terminated.exitCode}{"\tReason:"}{.lastState.terminated.reason}{"\n"}{end}'

# 2. Inspect configured memory requests and limits
kubectl get pod <pod-name> -n <namespace> -o jsonpath='{range .spec.containers[*]}{.name}{"\tRequests:"}{.resources.requests.memory}{"\tLimits:"}{.resources.limits.memory}{"\n"}{end}'

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
kubectl set resources deployment/<deployment-name> -n <namespace> \
  --limits=memory=1Gi --requests=memory=512Mi
```

### Action B: Tune JVM / Runtime Heap Ergonomics (Safe)
```bash
# Configure JVM MaxRAMPercentage to 75% so JVM reserves headroom for native memory
kubectl set env deployment/<deployment-name> -n <namespace> \
  JAVA_TOOL_OPTIONS="-XX:MaxRAMPercentage=75.0 -XX:+UseG1GC"
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
""",
    },
    {
        "category": "runbooks/kubernetes",
        "name": "pending-pods.md",
        "title": "Runbook: Pod Stuck in Pending State",
        "incident_id": "RB-K8S-004",
        "severity": "P2",
        "component": "kube-scheduler",
        "applicable_versions": "Kubernetes 1.28-1.37",
        "related_upstream_urls": [
            "https://kubernetes.io/docs/concepts/scheduling-eviction/kube-scheduler/",
            "https://kubernetes.io/docs/tasks/debug/debug-application/debug-pods/",
        ],
        "content": """# Runbook: Pod Stuck in Pending State

## 1. Incident Identifier
- **ID:** RB-K8S-004
- **Severity:** P2 (High) — Pod created in API server but cannot be scheduled or initialized on any cluster node.

## 2. Severity Classification
- **P1:** Critical service scaling blocked; cannot provision replacement pods during outage.
- **P2:** Routine deployment rollout delayed because new pods cannot find schedulable nodes.
- **P3:** Development or batch workload pending resource availability.

## 3. Symptoms
- `kubectl get pods` shows `STATUS: Pending` for longer than 60 seconds.
- Pod is not bound to any node (`NODE: <none>`).
- Alert `KubePodPending` firing for prolonged duration.

## 4. Common Causes
1. **Insufficient Cluster Resources:** Total requested CPU or memory exceeds allocatable capacity across all worker nodes.
2. **Node Affinity / NodeSelector Mismatch:** Pod requests labels (e.g. `disktype=ssd`, `topology.kubernetes.io/zone=us-east-1a`) that match 0 ready nodes.
3. **Taints and Tolerations:** Worker nodes have taints (e.g. `dedicated=gpu:NoSchedule`, `node.kubernetes.io/unreachable`) without matching pod tolerations.
4. **PersistentVolumeClaim Pending:** Pod mounts a PVC that has not yet bound to a PV or volume provisioning is blocked.
5. **ResourceQuota Exceeded:** Namespace has hit its quota limit for total CPU, memory, or pod count.

## 5. Impact Assessment
- Deployment updates stall in progress.
- Redundancy compromised if existing pods terminate without replacement pods coming online.

## 6. Diagnostic Procedure
1. Check pod events for scheduler rejection explanation.
2. Check if the pod specifies volume mounts waiting for PVC binding.
3. Inspect namespace ResourceQuota usage.
4. Check node schedulability, taints, and available allocatable capacity.

## 7. Read-Only Investigation Commands
```bash
# 1. Inspect scheduler failure reason from pod events
kubectl describe pod <pod-name> -n <namespace> | grep -A 10 "Events:"

# 2. Check PVC status if pod mounts persistent storage
kubectl get pvc -n <namespace>

# 3. Check ResourceQuota usage in target namespace
kubectl get resourcequota -n <namespace> -o yaml

# 4. Check cluster-wide node allocatable resources vs requested allocations
kubectl describe nodes | grep -A 8 "Allocated resources:"

# 5. Check if nodes are cordoned or have taints
kubectl get nodes -o custom-columns=NAME:.metadata.name,SCHEDULABLE:.spec.unschedulable,TAINTS:.spec.taints
```

## 8. Expected Outputs
- `0/12 nodes are available: 12 Insufficient cpu`: Cluster CPU exhaustion.
- `0/12 nodes are available: 3 node(s) had untolerated taint, 9 node(s) didn't match Pod's node affinity/selector`: Scheduling constraint mismatch.
- `pod has unbound immediate PersistentVolumeClaims`: Storage provisioning issue.
- `exceeded quota: compute-resources, requested: requests.cpu=2, used: requests.cpu=15, limited: requests.cpu=16`: Namespace quota ceiling reached.

## 9. Decision Tree
```
                         [Pod Stuck in Pending]
                                    |
                          Inspect Pod Events
                                    |
      +---------------------+-------+---------------------+
      |                     |                             |
[Insufficient CPU/Mem]  [Taint/Affinity Mismatch]     [PVC Unbound]
      |                     |                             |
Scale Cluster Nodes or  Review nodeSelector/taints;   Inspect StorageClass / CSI
adjust pod requests     add tolerations or fix label  driver provisioning logs
```

## 10. Remediation Steps

### Action A: Right-Size Excessive Pod Requests (Safe)
```bash
# If requested resources are oversized compared to actual usage, adjust downward
kubectl set resources deployment/<deployment-name> -n <namespace> \
  --requests=cpu=250m,memory=512Mi
```

### Action B: Uncordon Schedulable Worker Nodes (Safe)
```bash
# If nodes were left cordoned after maintenance:
kubectl uncordon <node-name>
```

### Action C: Increase Namespace ResourceQuota (Requires Team Lead Approval)
```bash
# Patch namespace resource quota if justified by business requirements
kubectl patch resourcequota <quota-name> -n <namespace> --type=merge -p '{"spec":{"hard":{"requests.cpu":"32"}}}'
```

### Action D: Trigger Cluster Autoscaler Scale-Up (Safe)
```bash
# Verify cluster-autoscaler is running and healthy
kubectl get pods -n kube-system -l app=cluster-autoscaler
```

## 11. Verification After Remediation
1. Verify pod is scheduled to a node:
   ```bash
   kubectl get pod <pod-name> -n <namespace> -o wide
   ```
2. Verify pod transitions through `ContainerCreating` to `Running`.

## 12. Rollback Procedure
If modifying requests or quota causes unwanted cluster contention:
```bash
kubectl apply -f <previous-manifest-backup.yaml>
```

## 13. Escalation Criteria
- If cloud provider rejects cluster autoscaler node requests due to quota/stockout, escalate to Cloud Infrastructure on-call.
- If PVC provisioning is permanently failed, escalate to Storage SRE (see RB-STO-001).

## 14. Prevention Recommendations
1. Establish cluster autoscaling with multiple instance types across availability zones.
2. Implement ResourceQuotas alongside LimitRanges to enforce sensible default requests.
3. Use Pod Topology Spread Constraints instead of hard pod anti-affinities to avoid unresolvable scheduling deadlocks.

## 15. Related Documentation References
- Kubernetes Scheduler Concepts: `https://kubernetes.io/docs/concepts/scheduling-eviction/kube-scheduler/`
- Debugging Pods: `https://kubernetes.io/docs/tasks/debug/debug-application/debug-pods/`
""",
    },
    {
        "category": "runbooks/kubernetes",
        "name": "pod-scheduling-failures.md",
        "title": "Runbook: Pod Scheduling Failures and Affinity/Taint Constraints",
        "incident_id": "RB-K8S-005",
        "severity": "P2",
        "component": "kube-scheduler",
        "applicable_versions": "Kubernetes 1.28-1.37",
        "related_upstream_urls": [
            "https://kubernetes.io/docs/concepts/scheduling-eviction/assign-pod-node/",
            "https://kubernetes.io/docs/concepts/scheduling-eviction/taint-and-toleration/",
        ],
        "content": """# Runbook: Pod Scheduling Failures and Affinity/Taint Constraints

## 1. Incident Identifier
- **ID:** RB-K8S-005
- **Severity:** P2 (High) — Pods fail to schedule due to conflicting node affinity, pod anti-affinity, topology spread, or node taints.

## 2. Severity Classification
- **P1:** Replicas cannot satisfy required anti-affinity across failure domains, causing service outage.
- **P2:** Subset of pods unscheduled; degraded redundancy.
- **P3:** Staging or test environment constraint conflict.

## 3. Symptoms
- Pod events report: `0/N nodes are available: N node(s) didn't match Pod's node affinity/selector, N node(s) had untolerated taint, N node(s) didn't match pod anti-affinity rules`.
- Pod remains in `Pending` state indefinitely despite ample cluster CPU and memory.

## 4. Common Causes
1. **Hard Anti-Affinity Saturation:** `requiredDuringSchedulingIgnoredDuringExecution` anti-affinity configured on `topologyKey: kubernetes.io/hostname`, but replica count exceeds node count.
2. **Missing Node Labels:** Pod requires `topology.kubernetes.io/zone=us-east-1a`, but nodes in that zone were decommissioned or lack the label.
3. **Untolerated Node Taints:** Dedicated node pool tainted with `workload=batch:NoSchedule`, but new pod lacks the toleration.
4. **Topology Spread MaxSkew Violation:** `topologySpreadConstraints` with `whenUnsatisfiable: DoNotSchedule` cannot be fulfilled due to uneven zone distribution.

## 5. Impact Assessment
- Prevented deployment rollouts, scaling events, and high availability distribution.

## 6. Diagnostic Procedure
1. Extract exact affinity and anti-affinity rules from PodSpec.
2. Check existing pod distribution across nodes and topology domains.
3. Inspect taints on all ready worker nodes.

## 7. Read-Only Investigation Commands
```bash
# 1. Inspect affinity, anti-affinity, and topology spread constraints on the failing pod
kubectl get pod <pod-name> -n <namespace> -o jsonpath='{.spec.affinity}' | jq .
kubectl get pod <pod-name> -n <namespace> -o jsonpath='{.spec.topologySpreadConstraints}' | jq .

# 2. Check taints present on all nodes
kubectl get nodes -o custom-columns=NAME:.metadata.name,TAINTS:.spec.taints

# 3. List distribution of existing pods carrying the matching anti-affinity label
kubectl get pods -A -l <affinity-label-key>=<affinity-label-val> -o wide

# 4. Check labels on nodes in target pool
kubectl get nodes -l <nodepool-label> --show-labels
```

## 8. Expected Outputs
- `node(s) didn't match pod anti-affinity rules`: Every available node already hosts a replica matching the anti-affinity label selector.
- `node(s) had untolerated taint`: Nodes have specific taints that the pod does not tolerate.

## 9. Decision Tree
```
                      [Scheduling Constraint Failure]
                                     |
                         Identify Constraint Type
                                     |
         +---------------------------+---------------------------+
         |                                                       |
  [Hard Anti-Affinity]                                    [Untolerated Taint]
         |                                                       |
  More pods than nodes?                                   Is taint intentional?
  - Switch to soft anti-affinity                          - Yes: add toleration to pod
    (preferredDuringScheduling)                           - No: remove taint from node
```

## 10. Remediation Steps

### Action A: Convert Hard Anti-Affinity to Soft Anti-Affinity (Safe)
Modify PodSpec from `requiredDuringScheduling` to `preferredDuringScheduling`:
```yaml
affinity:
  podAntiAffinity:
    preferredDuringSchedulingIgnoredDuringExecution:
    - weight: 100
      podAffinityTerm:
        labelSelector:
          matchExpressions:
          - key: app
            operator: In
            values:
            - <app-name>
        topologyKey: "kubernetes.io/hostname"
```

### Action B: Add Missing Toleration to Pod (Safe)
```yaml
tolerations:
- key: "workload"
  operator: "Equal"
  value: "specialized"
  effect: "NoSchedule"
```

### Action C: Remove Stale Taint from Node (Disruptive — Requires Node Approval)
```bash
# Remove taint if left behind by failed maintenance job
kubectl taint nodes <node-name> <taint-key>:<taint-effect>-
```

## 11. Verification After Remediation
1. Verify pod binds immediately to an eligible node.
2. Verify topology spread constraint score remains balanced across failure domains.

## 12. Rollback Procedure
Re-apply previous deployment manifest if modified scheduling affects traffic distribution:
```bash
kubectl rollout undo deployment/<deployment-name> -n <namespace>
```

## 13. Escalation Criteria
- If hardware constraint requires dedicated node pool expansion, escalate to Capacity / Cloud Engineering.

## 14. Prevention Recommendations
1. Prefer `preferredDuringSchedulingIgnoredDuringExecution` for pod anti-affinity unless strict physical isolation is legally required.
2. Set `whenUnsatisfiable: ScheduleAnyway` in `topologySpreadConstraints` to permit degradation during maintenance.

## 15. Related Documentation References
- Kubernetes Assigning Pods to Nodes: `https://kubernetes.io/docs/concepts/scheduling-eviction/assign-pod-node/`
- Taints and Tolerations: `https://kubernetes.io/docs/concepts/scheduling-eviction/taint-and-toleration/`
""",
    },
    {
        "category": "runbooks/kubernetes",
        "name": "probe-failures.md",
        "title": "Runbook: Failed Liveness, Readiness, and Startup Probes",
        "incident_id": "RB-K8S-006",
        "severity": "P2",
        "component": "kubelet-probes",
        "applicable_versions": "Kubernetes 1.28-1.37",
        "related_upstream_urls": [
            "https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/",
        ],
        "content": """# Runbook: Failed Liveness, Readiness, and Startup Probes

## 1. Incident Identifier
- **ID:** RB-K8S-006
- **Severity:** P2 (High) — Pod probes failing, leading to traffic withdrawal (readiness) or container restart loops (liveness).

## 2. Severity Classification
- **P1:** Liveness probe killing all healthy pods during traffic spike due to slow response times.
- **P2:** Readiness probe removing pods from Service Endpoints, reducing serving capacity.
- **P3:** Startup probe failing on slow batch init container.

## 3. Symptoms
- Pod status shows `0/1 READY` while container status is `Running`.
- Pod restarts periodically with `Liveness probe failed: HTTP probe failed with statuscode: 500`.
- Service endpoints dropped: `kubectl get endpoints <service-name>` shows 0 healthy IP addresses.

## 4. Common Causes
1. **Liveness Probe Endpoint Doing Heavy Work:** Probe invokes database queries or heavy external dependency checks instead of checking local process health.
2. **Timeout Too Aggressive:** `timeoutSeconds: 1` too short under load, timing out transiently.
3. **Slow Application Initialization:** Application takes 45 seconds to boot, but liveness probe begins after 10 seconds without a `startupProbe`.
4. **Port or Path Mismatch:** Probe checks HTTP `/healthz` on port 8080, but application listens on port 8081 or path `/actuator/health`.
5. **Deadlock / Event Loop Blocked:** Application event loop saturated or thread pool exhausted, failing to respond to probe requests.

## 5. Impact Assessment
- Readiness failure: Zero endpoints available, causing Ingress to return HTTP 503 Service Unavailable.
- Liveness failure: Infinite restart cascade preventing cache warming or connection pool recovery.

## 6. Diagnostic Procedure
1. Identify which probe is failing (Startup, Liveness, or Readiness).
2. Check probe failure count, timeout, and consecutive failure threshold.
3. Test the probe endpoint manually from inside the cluster network.

## 7. Read-Only Investigation Commands
```bash
# 1. Inspect probe failure events on the pod
kubectl get events -n <namespace> --field-selector involvedObject.name=<pod-name> | grep -i probe

# 2. Inspect exact probe configuration in PodSpec
kubectl get pod <pod-name> -n <namespace> -o jsonpath='{range .spec.containers[*]}{.name}{"\nLiveness:"}{.livenessProbe}{"\nReadiness:"}{.readinessProbe}{"\nStartup:"}{.startupProbe}{"\n\n"}{end}'

# 3. Check container logs immediately preceding probe failure
kubectl logs <pod-name> -n <namespace> -c <container-name> --tail=100

# 4. Probe the endpoint manually using curl from a debug container
kubectl run probe-test --rm -it --image=curlimages/curl --restart=Never -n <namespace> -- curl -m 5 -Iv http://<pod-ip>:<probe-port><probe-path>
```

## 8. Expected Outputs
- `Liveness probe failed: Get "http://10.244.1.45:8080/health": context deadline exceeded`: Timeout reached.
- `Readiness probe failed: HTTP probe failed with statuscode: 503`: Backend dependency marked unhealthy.
- `Readiness probe failed: connection refused`: Application not yet listening on target port.

## 9. Decision Tree
```
                         [Probe Failure Detected]
                                     |
                            Identify Failing Probe
                                     |
        +----------------------------+----------------------------+
        |                            |                            |
  [Startup Probe]              [Liveness Probe]             [Readiness Probe]
        |                            |                            |
  App boots slowly:            Process alive?               Traffic dropped:
  - Increase failureThreshold  - Yes: increase timeout      - Check dependency health
  - Increase periodSeconds     - No: profile thread dump    - Separate shallow / deep health
```

## 10. Remediation Steps

### Action A: Implement or Tune Startup Probe (Safe)
```yaml
# Add startupProbe to protect slow-initializing applications
startupProbe:
  httpGet:
    path: /healthz
    port: 8080
  initialDelaySeconds: 5
  periodSeconds: 5
  failureThreshold: 30 # Allows up to 150 seconds to initialize
```

### Action B: Increase Liveness Probe Timeout & Threshold (Safe)
```yaml
livenessProbe:
  httpGet:
    path: /livez
    port: 8080
  timeoutSeconds: 3
  periodSeconds: 10
  failureThreshold: 5 # Prevent single-spike kills
```

### Action C: Temporarily Remove Liveness Probe During Incident (Requires Approval)
```bash
# If liveness probe is repeatedly killing pods in an emergency:
kubectl patch deployment <deployment-name> -n <namespace> --type=json -p='[{"op": "remove", "path": "/spec/template/spec/containers/0/livenessProbe"}]'
```

## 11. Verification After Remediation
1. Verify pod achieves `1/1 READY`.
2. Confirm pod endpoints are restored in the Service:
   ```bash
   kubectl get endpoints <service-name> -n <namespace>
   ```

## 12. Rollback Procedure
```bash
kubectl rollout undo deployment/<deployment-name> -n <namespace>
```

## 13. Escalation Criteria
- If thread deadlocks are observed in application thread dumps, escalate to Software Engineering.

## 14. Prevention Recommendations
1. Separate liveness (process alive?) from readiness (ready to serve traffic?). Never check external dependencies (DB/Redis) in liveness probes.
2. Always pair slow-booting applications with dedicated `startupProbe`.
3. Set `timeoutSeconds` to at least 2-3 seconds for non-trivial HTTP endpoints.

## 15. Related Documentation References
- Configure Probes: `https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/`
""",
    },
    {
        "category": "runbooks/kubernetes",
        "name": "deployment-rollout-failures.md",
        "title": "Runbook: Deployment Rollout Stalled or Failed",
        "incident_id": "RB-K8S-007",
        "severity": "P1",
        "component": "deployment-controller",
        "applicable_versions": "Kubernetes 1.28-1.37",
        "related_upstream_urls": [
            "https://kubernetes.io/docs/concepts/workloads/controllers/deployment/",
        ],
        "content": """# Runbook: Deployment Rollout Stalled or Failed

## 1. Incident Identifier
- **ID:** RB-K8S-007
- **Severity:** P1 (Critical) / P2 (High) — Automated deployment rollout progress is blocked, leaving workload in mixed revision state.

## 2. Severity Classification
- **P1:** Rollout failure during emergency fix or production release with customer-facing downtime.
- **P2:** Rollout timeout exceeded (`ProgressDeadlineExceeded`) while old revision continues serving.
- **P3:** Staging environment rollout failure.

## 3. Symptoms
- `kubectl rollout status deployment/<name>` reports: `Waiting for deployment "<name>" rollout to finish: 1 out of 3 new replicas have been updated...`
- `ProgressDeadlineExceeded` condition in Deployment status.
- New ReplicaSet cannot scale up while old ReplicaSet cannot scale down.

## 4. Common Causes
1. **New Pods Failing Startup / Probes:** New pods enter `CrashLoopBackOff`, `ImagePullBackOff`, or fail readiness checks.
2. **Quota or Scheduling Blockage:** Insufficient node resources to satisfy `maxSurge` allocation.
3. **PDB (PodDisruptionBudget) Blocking Eviction:** Strict PDB preventing old pods from being drained.
4. **Hook or Init Container Failure:** Database migration init container crashing or timing out.

## 5. Impact Assessment
- Incomplete deployment; cluster running split-brain code versions simultaneously.
- CI/CD pipeline blocked waiting for deployment confirmation.

## 6. Diagnostic Procedure
1. Inspect deployment rollout status and conditions.
2. Identify new vs old ReplicaSet.
3. Check pod status under the new ReplicaSet.

## 7. Read-Only Investigation Commands
```bash
# 1. Check current rollout status
kubectl rollout status deployment/<deployment-name> -n <namespace>

# 2. List ReplicaSets associated with deployment
kubectl get rs -n <namespace> -l app=<app-label>

# 3. Check deployment conditions for ProgressDeadlineExceeded
kubectl get deployment <deployment-name> -n <namespace> -o jsonpath='{.status.conditions[*]}' | jq .

# 4. Check pods belonging to the new ReplicaSet
kubectl get pods -n <namespace> -l app=<app-label> --sort-by=.metadata.creationTimestamp
```

## 8. Expected Outputs
- `ProgressDeadlineExceeded: Deployment does not have minimum availability`: Indicates new revision could not become ready within `progressDeadlineSeconds`.

## 9. Decision Tree
```
                     [Deployment Rollout Stalled]
                                  |
                      Check New Pod Replica Status
                                  |
        +-------------------------+-------------------------+
        |                                                   |
  [New Pods Crashing / Unready]                       [Cannot Schedule (Pending)]
        |                                                   |
  Immediate Mitigation:                               Check Resource Quota / Nodes
  Execute Rollout Undo to prior revision              Adjust maxSurge / maxUnavailable
```

## 10. Remediation Steps

### Action A: Immediate Rollback to Previous Stable Revision (Safe / Recommended)
```bash
# Rollback immediately to restore pipeline and cluster consistency
kubectl rollout undo deployment/<deployment-name> -n <namespace>
```

### Action B: Pause Rollout for In-Place Investigation (Safe)
```bash
# Pause rollout so deployment controller stops attempting further pod churn
kubectl rollout pause deployment/<deployment-name> -n <namespace>
```

### Action C: Resume Rollout after Fixing Dependency (Safe)
```bash
kubectl rollout resume deployment/<deployment-name> -n <namespace>
```

## 11. Verification After Remediation
1. Verify rollout status confirms complete success:
   ```bash
   kubectl rollout status deployment/<deployment-name> -n <namespace>
   # Output: deployment "<name>" successfully rolled out
   ```
2. Confirm old ReplicaSet is scaled to 0 and all target replicas run on the desired revision.

## 12. Rollback Procedure
If rollback command itself fails, explicitly target revision number:
```bash
kubectl rollout history deployment/<deployment-name> -n <namespace>
kubectl rollout undo deployment/<deployment-name> -n <namespace> --to-revision=<known-healthy-rev>
```

## 13. Escalation Criteria
- If database schema migration was partially applied, escalate to Application DBA on-call before rolling back application.

## 14. Prevention Recommendations
1. Set `progressDeadlineSeconds` appropriately (e.g. 600s).
2. Configure `maxSurge: 25%` and `maxUnavailable: 0` for zero-downtime rolling deployments.
3. Run canary deployments or staging validation before production promotion.

## 15. Related Documentation References
- Kubernetes Deployments: `https://kubernetes.io/docs/concepts/workloads/controllers/deployment/`
""",
    },
    {
        "category": "runbooks/kubernetes",
        "name": "statefulset-failures.md",
        "title": "Runbook: StatefulSet Pod Partitioning and Volume Attachment Failures",
        "incident_id": "RB-K8S-008",
        "severity": "P1",
        "component": "statefulset-controller",
        "applicable_versions": "Kubernetes 1.28-1.37",
        "related_upstream_urls": [
            "https://kubernetes.io/docs/concepts/workloads/controllers/statefulset/",
            "https://kubernetes.io/docs/tasks/run-application/run-replicated-stateful-application/",
        ],
        "content": """# Runbook: StatefulSet Pod Partitioning and Volume Attachment Failures

## 1. Incident Identifier
- **ID:** RB-K8S-008
- **Severity:** P1 (Critical) — StatefulSet ordinal pod cannot progress, attach persistent volume, or achieve quorum.

## 2. Severity Classification
- **P1:** Primary database / messaging broker pod (e.g., PostgreSQL, Kafka, Elasticsearch) down or split-brain.
- **P2:** Secondary replica in clustered StatefulSet down; cluster operating without redundancy.
- **P3:** StatefulSet maintenance rollout paused.

## 3. Symptoms
- StatefulSet rollout blocked at ordinal `N` (e.g., `kafka-0` running, `kafka-1` pending).
- Pod events report: `Multi-Attach error for volume ... Volume is already exclusively attached to one node`.
- Storage volume cannot detach from terminated node.

## 4. Common Causes
1. **Volume Multi-Attach Conflict:** Node holding previous pod crashed uncleanly; cloud storage controller has not detached EBS/PD volume.
2. **Strict OrderedReady Policy:** `podManagementPolicy: OrderedReady` blocks ordinal `N+1` if ordinal `N` is not ready.
3. **PVC Provisioning Failure for Specific Ordinal:** Dedicated volume claim template failed to bind PV.
4. **Network Partition / Quorum Loss:** Clustered software refuses to start because majority consensus cannot be established.

## 5. Impact Assessment
- Stateful database or queue downtime; possible data inconsistency if forced deletion is performed improperly.

## 6. Diagnostic Procedure
1. Check StatefulSet status and current ordinal progress.
2. Inspect VolumeAttachment objects for stuck attachments.
3. Inspect pod logs for application quorum state.

## 7. Read-Only Investigation Commands
```bash
# 1. Check StatefulSet rollout and replica status
kubectl get statefulset <name> -n <namespace> -o wide

# 2. Inspect specific failed ordinal pod and events
kubectl describe pod <name>-<ordinal> -n <namespace> | grep -E "(FailedAttach|Multi-Attach|VolumeAttachment)"

# 3. Check VolumeAttachment objects across the cluster
kubectl get volumeattachment | grep <pv-name>

# 4. Check PVCs bound to this StatefulSet
kubectl get pvc -n <namespace> -l app=<app-label>
```

## 8. Expected Outputs
- `VolumeAttachment has not been deleted after 10m`: Cloud provider storage detach timeout.
- `OrderedReady`: Preceding ordinal pod not ready, blocking progress.

## 9. Decision Tree
```
                         [StatefulSet Ordinal Blocked]
                                       |
                           Is it a VolumeAttach Error?
                                       |
         +-----------------------------+-----------------------------+
         |                                                           |
   [Yes: Multi-Attach]                                       [No: App Quorum]
         |                                                           |
   Verify old node is DEAD:                                   Inspect cluster peers:
   - Safe detach if node confirmed down                       - Check headless service DNS
   - NEVER force delete on live node!                         - Resolve quorum split
```

## 10. Remediation Steps

### Action A: Safe Recovery from Stuck VolumeAttachment (Disruptive — Requires L2 Approval)
> [!CAUTION]
> NEVER force-delete a StatefulSet pod while the underlying node is still alive. Doing so can cause dual-mount data corruption (split-brain).

```bash
# 1. Verify old node is cordoned and powered off
kubectl describe node <old-node>

# 2. If node is confirmed terminated in cloud provider, delete the stuck VolumeAttachment
kubectl get volumeattachment -o json | jq -r '.items[] | select(.spec.nodeName=="<old-node>") | .metadata.name' | xargs -r kubectl delete volumeattachment

# 3. If pod remains Terminating on dead node, force-delete ONLY AFTER confirming node is dead:
kubectl delete pod <name>-<ordinal> -n <namespace> --force --grace-period=0
```

### Action B: Switch Pod Management Policy to Parallel for Non-Ordered Workloads (Safe)
```yaml
# In StatefulSet spec:
spec:
  podManagementPolicy: Parallel
```

## 11. Verification After Remediation
1. Verify ordinal pod attaches volume successfully:
   `kubectl get pod <name>-<ordinal> -n <namespace>`
2. Confirm clustered application reaches healthy consensus (e.g. `etcdctl endpoint health` or `nodetool status`).

## 12. Rollback Procedure
If rolling update of StatefulSet fails:
```bash
kubectl rollout undo statefulset/<name> -n <namespace>
```

## 13. Escalation Criteria
- Escalate immediately to Database / Storage Administrator if potential filesystem corruption is suspected.

## 14. Prevention Recommendations
1. Ensure cloud provider CSI drivers are updated to versions supporting automated non-graceful node shutdown volume detachment.
2. Configure `podDisruptionBudget` allowing maximum 1 replica unavailable.

## 15. Related Documentation References
- StatefulSets: `https://kubernetes.io/docs/concepts/workloads/controllers/statefulset/`
- Force Delete StatefulSet Pods: `https://kubernetes.io/docs/tasks/run-application/force-delete-stateful-set-pod/`
""",
    },
    {
        "category": "runbooks/kubernetes",
        "name": "nodenotready.md",
        "title": "Runbook: NodeNotReady and Node Heartbeat Timeout",
        "incident_id": "RB-K8S-009",
        "severity": "P1",
        "component": "node-controller",
        "applicable_versions": "Kubernetes 1.28-1.37",
        "related_upstream_urls": [
            "https://kubernetes.io/docs/concepts/architecture/nodes/",
            "https://kubernetes.io/docs/tasks/debug/debug-cluster/monitor-node-health/",
        ],
        "content": """# Runbook: NodeNotReady and Node Heartbeat Timeout

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
""",
    },
    {
        "category": "runbooks/kubernetes",
        "name": "node-pressure-eviction.md",
        "title": "Runbook: Node Pressure and Eviction Triage",
        "incident_id": "RB-K8S-010",
        "severity": "P2",
        "component": "kubelet-eviction",
        "applicable_versions": "Kubernetes 1.28-1.37",
        "related_upstream_urls": [
            "https://kubernetes.io/docs/concepts/scheduling-eviction/node-pressure-eviction/",
        ],
        "content": """# Runbook: Node Pressure and Eviction Triage

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
""",
    },
    {
        "category": "runbooks/kubernetes",
        "name": "kubelet-problems.md",
        "title": "Runbook: Kubelet Service Degradation and SyncLoop Hangs",
        "incident_id": "RB-K8S-011",
        "severity": "P1",
        "component": "kubelet",
        "applicable_versions": "Kubernetes 1.28-1.37",
        "related_upstream_urls": [
            "https://kubernetes.io/docs/reference/command-line-tools-reference/kubelet/",
        ],
        "content": """# Runbook: Kubelet Service Degradation and SyncLoop Hangs

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
""",
    },
    {
        "category": "runbooks/kubernetes",
        "name": "resource-exhaustion.md",
        "title": "Runbook: Cluster Resource Exhaustion and Quota Limits",
        "incident_id": "RB-K8S-012",
        "severity": "P1",
        "component": "quota-admission",
        "applicable_versions": "Kubernetes 1.28-1.37",
        "related_upstream_urls": [
            "https://kubernetes.io/docs/concepts/policy/resource-quotas/",
            "https://kubernetes.io/docs/concepts/policy/limit-range/",
        ],
        "content": """# Runbook: Cluster Resource Exhaustion and Quota Limits

## 1. Incident Identifier
- **ID:** RB-K8S-012
- **Severity:** P1 (Critical) / P2 (High) — Namespace or cluster-wide compute resources exhausted, blocking all pod creation.

## 2. Severity Classification
- **P1:** Production namespace cannot scale up during traffic spike due to ResourceQuota exhaustion.
- **P2:** Non-production namespace blocked from deployments.
- **P3:** LimitRange mismatch on single container.

## 3. Symptoms
- `kubectl create` or rollout fails with `admission webhook or ResourceQuota denied the request: exceeded quota: ...`.
- Horizontal Pod Autoscaler (HPA) cannot provision new replicas.
- Prometheus alert `KubeResourceQuotaExhausted` firing.

## 4. Common Causes
1. **Unbounded HPA Scaling:** HPA scaled up pods to maximum limit, consuming all quota allocated to namespace.
2. **Oversized Resource Requests:** Workloads requesting 4 CPU cores while actually utilizing 100 millicores.
3. **Orphaned Pods / Jobs:** Completed Jobs or dead pods holding quota allocations.
4. **Cluster Node Pool Capacity Limit:** Cloud autoscaler reached maximum allowed nodes (`max_size`).

## 5. Impact Assessment
- Inability to deploy updates, scale out services, or recover failed replicas.

## 6. Diagnostic Procedure
1. Check target namespace ResourceQuota status (Used vs Hard).
2. Check total cluster CPU and memory commit ratio.
3. Identify top consuming deployments in the namespace.

## 7. Read-Only Investigation Commands
```bash
# 1. Check ResourceQuota usage in target namespace
kubectl get resourcequota -n <namespace> -o yaml

# 2. List top pods by CPU and Memory usage
kubectl top pods -n <namespace> --sort-by=memory

# 3. List top deployments by requested resources
kubectl get deployment -n <namespace> -o custom-columns=NAME:.metadata.name,CPU_REQ:.spec.template.spec.containers[*].resources.requests.cpu,MEM_REQ:.spec.template.spec.containers[*].resources.requests.memory

# 4. Check cluster-autoscaler status configmap
kubectl get configmap cluster-autoscaler-status -n kube-system -o yaml
```

## 8. Expected Outputs
- `used: requests.cpu: 64, hard: requests.cpu: 64`: Namespace quota ceiling saturated.
- `cluster-autoscaler-status` reporting `MaxNodesReached`: Cloud provider cluster size ceiling hit.

## 9. Decision Tree
```
                         [Resource Quota Exhaustion]
                                      |
                         Where is the bottleneck?
                                      |
         +----------------------------+----------------------------+
         |                                                         |
  [Namespace ResourceQuota]                                 [Cluster Node Autoscaler]
         |                                                         |
  Oversized requests or dead pods?                          Max nodes reached?
  - Delete failed jobs/pods                                 - Increase autoscaler max_size
  - Right-size requests                                     - Request cloud quota increase
  - Increase ResourceQuota                                  
```

## 10. Remediation Steps

### Action A: Clean Up Completed Jobs and Failed Pods (Safe)
```bash
# Free up quota reserved by completed jobs
kubectl delete jobs --field-selector status.successful=1 -n <namespace>
kubectl delete pods --field-selector status.phase=Failed -n <namespace>
```

### Action B: Temporarily Increase Namespace Quota (Requires Approval)
```bash
# Patch quota to permit emergency scale-up
kubectl patch resourcequota <quota-name> -n <namespace> --type=merge -p '{"spec":{"hard":{"requests.cpu":"128"}}}'
```

### Action C: Right-Size Workload Requests (Safe)
```bash
# Downsize idle deployment requests to match realistic usage
kubectl set resources deployment/<deployment-name> -n <namespace> --requests=cpu=500m,memory=1Gi
```

## 11. Verification After Remediation
1. Verify quota usage reflects headroom:
   `kubectl get resourcequota -n <namespace>`
2. Confirm stalled pods transition to `Running`.

## 12. Rollback Procedure
Revert quota manifest once emergency passes.

## 13. Escalation Criteria
- If cloud provider instance quota (e.g. AWS vCPU limits) is reached, escalate to Cloud FinOps / Platform Lead.

## 14. Prevention Recommendations
1. Implement Vertical Pod Autoscaler (VPA) in recommendation mode to guide right-sizing.
2. Configure automatic cleanup of completed jobs via `ttlSecondsAfterFinished`.

## 15. Related Documentation References
- Resource Quotas: `https://kubernetes.io/docs/concepts/policy/resource-quotas/`
""",
    },
    {
        "category": "runbooks/kubernetes",
        "name": "control-plane-connectivity.md",
        "title": "Runbook: Control-Plane and Kube-APIServer Unreachability",
        "incident_id": "RB-K8S-013",
        "severity": "P1",
        "component": "kube-apiserver",
        "applicable_versions": "Kubernetes 1.28-1.37",
        "related_upstream_urls": [
            "https://kubernetes.io/docs/concepts/architecture/control-plane-node-communication/",
        ],
        "content": """# Runbook: Control-Plane and Kube-APIServer Unreachability

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
sudo etcdctl --cacert=/etc/kubernetes/pki/etcd/ca.crt \
  --cert=/etc/kubernetes/pki/etcd/server.crt \
  --key=/etc/kubernetes/pki/etcd/server.key \
  --endpoints=https://127.0.0.1:2379 endpoint health

# 3. Check etcd alarms (e.g. NOSPACE)
sudo etcdctl --cacert=/etc/kubernetes/pki/etcd/ca.crt \
  --cert=/etc/kubernetes/pki/etcd/server.crt \
  --key=/etc/kubernetes/pki/etcd/server.key \
  --endpoints=https://127.0.0.1:2379 alarm list

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
""",
    },
    {
        "category": "runbooks/kubernetes",
        "name": "certificate-expiration.md",
        "title": "Runbook: Cluster Certificate Expiration and Renewal",
        "incident_id": "RB-K8S-014",
        "severity": "P1",
        "component": "tls-certificates",
        "applicable_versions": "Kubernetes 1.28-1.37",
        "related_upstream_urls": [
            "https://kubernetes.io/docs/tasks/administer-cluster/kubeadm/kubeadm-certs/",
        ],
        "content": """# Runbook: Cluster Certificate Expiration and Renewal

## 1. Incident Identifier
- **ID:** RB-K8S-014
- **Severity:** P1 (Critical) — Kubernetes internal PKI certificates expired, severing authentication between control plane components.

## 2. Severity Classification
- **P1:** Expired API server or Kubelet client certificate; cluster completely unresponsive to commands.
- **P2:** Certificate expiring in <7 days.
- **P3:** Certificate expiring in <30 days.

## 3. Symptoms
- `kubectl` commands return: `Unable to connect to the server: x509: certificate has expired or is not yet valid`.
- Kubelet logs report: `part of the existing bootstrap client certificate is expired`.
- Nodes transition to `NotReady` simultaneously.

## 4. Common Causes
1. **1-Year Kubeadm Certificate Lifetime Exceeded:** Default certificates generated by `kubeadm` expire after 365 days if cluster is not upgraded annually.
2. **Kubelet Client Certificate Auto-Renewal Failed:** Kubelet server certificate rotation failed or CSR was not approved.
3. **NTP Time Drift:** System clock on worker node or master node jumped forward in time.

## 5. Impact Assessment
- Control plane components refuse to communicate; worker nodes cannot renew leases.

## 6. Diagnostic Procedure
1. Check certificate expiration dates across all cluster PKI files.
2. Verify host system clocks across all nodes.

## 7. Read-Only Investigation Commands
```bash
# 1. Check certificate expiration using kubeadm (on control-plane host)
sudo kubeadm certs check-expiration

# 2. Inspect specific certificate validity using OpenSSL
sudo openssl x509 -in /etc/kubernetes/pki/apiserver.crt -noout -dates

# 3. Check kubelet client certificate expiration on worker node
sudo openssl x509 -in /var/lib/kubelet/pki/kubelet-client-current.pem -noout -dates

# 4. Check system time synchronization
timedatectl status
```

## 8. Expected Outputs
- `CERTIFICATE EXPIRED`: Certificate validity period has lapsed (`notAfter` in the past).
- `NTP synchronized: no`: System clock drift detected.

## 9. Decision Tree
```
                         [Certificate Expired / Excluded]
                                        |
                            Which Certificate Expired?
                                        |
         +------------------------------+------------------------------+
         |                                                             |
  [Control Plane (apiserver, etcd)]                             [Kubelet Client]
         |                                                             |
  Renew with kubeadm:                                           Re-bootstrap Kubelet:
  sudo kubeadm certs renew all                                  Regenerate kubelet.conf from
  Restart static pods                                           bootstrap-kubelet.conf
```

## 10. Remediation Steps

### Action A: Renew Control Plane Certificates via Kubeadm (Requires Host Access)
```bash
# 1. On all control plane nodes, renew all certificates
sudo kubeadm certs renew all

# 2. Restart control plane components to load new certificates
sudo kill -s SIGHUP $(pidof kube-apiserver)
sudo kill -s SIGHUP $(pidof kube-controller-manager)
sudo kill -s SIGHUP $(pidof kube-scheduler)

# 3. Update admin kubeconfig file
sudo cp /etc/kubernetes/admin.conf ~/.kube/config
sudo chown $(id -u):$(id -g) ~/.kube/config
```

### Action B: Re-bootstrap Expired Worker Node Kubelet Certificate (Safe)
```bash
# On the affected worker node:
sudo rm -f /var/lib/kubelet/pki/kubelet-client-current.pem
sudo systemctl restart kubelet
```

## 11. Verification After Remediation
1. Verify certificate expiration output reports ~365 days remaining:
   `sudo kubeadm certs check-expiration`
2. Test `kubectl get nodes` to confirm authentication succeeds.

## 12. Rollback Procedure
Backup `/etc/kubernetes/pki` before renewing:
```bash
sudo cp -r /etc/kubernetes/pki /etc/kubernetes/pki.bak.$(date +%F)
```

## 13. Escalation Criteria
- If root CA (`/etc/kubernetes/pki/ca.crt`) itself has expired, escalate to Lead Infrastructure Architect (requires cluster-wide CA regeneration).

## 14. Prevention Recommendations
1. Establish automated Prometheus alerting for `cert_exporter` or `kubeadm_certificate_expiration_days < 30`.
2. Perform annual Kubernetes minor version upgrades, which automatically renew all certificates.

## 15. Related Documentation References
- Kubeadm Certificate Management: `https://kubernetes.io/docs/tasks/administer-cluster/kubeadm/kubeadm-certs/`
""",
    },
]

