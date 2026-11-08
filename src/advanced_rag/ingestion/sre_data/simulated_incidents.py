"""Educational Simulated Incident Postmortems and Case Studies.

All scenarios are strictly educational simulations and prominently labeled:
SIMULATED INCIDENT — NOT A REAL PRODUCTION EVENT.

Covers:
- Postmortems: OOMKilled cascade, CoreDNS saturation, Helm failed upgrade, Prometheus TSDB high cardinality
- Case Studies: StatefulSet storage detachment delay, Kubelet client certificate expiration outage
"""

from __future__ import annotations

SIMULATED_POSTMORTEMS: list[dict[str, any]] = [
    {
        "category": "postmortems",
        "name": "simulated-incident-oomkilled-cascade.md",
        "title": "Postmortem: Memory Leak and OOMKilled Cascade in Payment Processing Pods",
        "incident_id": "SIM-PM-001",
        "severity": "P1",
        "component": "workload-memory",
        "applicable_versions": "Kubernetes 1.28-1.37",
        "related_upstream_urls": [
            "https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/",
        ],
        "content": """# Postmortem: Memory Leak and OOMKilled Cascade in Payment Processing Pods

> [!IMPORTANT]
> **SIMULATED INCIDENT — NOT A REAL PRODUCTION EVENT.**
> This incident postmortem is an educational case study created for operational training and automated SRE guidance. All timestamps, incident logs, service names, and metrics are simulated.

## 1. Incident Summary
On 2026-09-14 at 14:12 UTC, the `payment-gateway` service experienced a cascading failure across all 12 replica pods in production cluster `us-east-prod`. Incoming checkout requests failed with HTTP 502 Bad Gateway. Total customer downtime was 28 minutes. The incident was mitigated by rolling back the application deployment from v2.4.1 to v2.4.0.

## 2. Customer and Service Impact
- **Impacted Service:** `payment-gateway` (Order processing and authorization).
- **Duration:** 28 minutes (14:12 UTC to 14:40 UTC).
- **Failed Requests:** Approximately 8,400 checkout transactions failed.
- **SLO Breach:** Consumed 34% of monthly 30-day availability error budget.

## 3. Incident Timeline (UTC)
- **13:45 UTC:** Release v2.4.1 deployed to production via automated CD pipeline.
- **14:02 UTC:** Application memory consumption began climbing linearly across all replicas at ~45MB/min.
- **14:12 UTC:** First replica hit container limit (1Gi) and was terminated by kernel OOM killer (exit code 137).
- **14:14 UTC:** Remaining replicas absorbed redirected traffic, accelerating memory growth. All replicas entered `CrashLoopBackOff` in cascade.
- **14:16 UTC:** PagerDuty P1 alert `PaymentGatewayHigh5xxErrorRate` fired; on-call engineer acknowledged page.
- **14:20 UTC:** Incident declared; Incident Commander opened incident bridge.
- **14:25 UTC:** Ops Lead inspected previous container logs and pod termination reasons, confirming `OOMKilled exit code 137`.
- **14:28 UTC:** Decision taken to rollback to v2.4.0 immediately.
- **14:32 UTC:** `kubectl rollout undo deployment/payment-gateway -n payments` executed.
- **14:36 UTC:** New v2.4.0 pods passed readiness probes; traffic resumed.
- **14:40 UTC:** Error rate dropped below 0.05%; mitigation verified.
- **15:10 UTC:** 30-minute observational soak period ended; incident closed.

## 4. Root Cause
In release v2.4.1, an internal caching layer was introduced to store idempotency keys. The cache implementation utilized an unbounded Go `sync.Map` without an eviction policy (TTL or LRU). Under peak production load, the map accumulated 2.5 million keys in 25 minutes, exhausting container memory limits.

## 5. Contributing Factors
1. **Oversized Max Surge:** Deployment configured with `maxSurge: 50%`, which scheduled new pods rapidly without allowing adequate canary soak time.
2. **Missing Memory Headroom Alerting:** Alerting rules checked for node memory pressure but lacked container-level saturation alerts (`container_memory_working_set_bytes / container_spec_memory_limit_bytes > 0.85`).
3. **Staging Traffic Discrepancy:** Staging environment received low-frequency synthetic requests which did not trigger the memory leak prior to release.

## 6. Detection Gaps
- The memory leak was not detected during automated integration tests because test runs concluded in 3 minutes, well before the 1Gi memory ceiling was reached.

## 7. Recovery Actions Taken
1. Executed `kubectl rollout undo deployment/payment-gateway -n payments`.
2. Verified all 12 replicas achieved `1/1 READY`.
3. Verified payment authorization endpoints returned HTTP 200 OK.

## 8. Corrective and Preventive Measures
- [x] Revert code change and replace unbounded map with LRU cache with strict 100k item capacity (PR #409).
- [x] Configure Prometheus alert `ContainerMemoryApproachingLimit` firing when working set exceeds 85% of limit for >5 minutes.
- [x] Mandate minimum 30-minute canary deployment bake period for core transaction services.

## 9. Lessons Learned
- Unbounded in-memory collections are the single most common cause of sudden OOM cascades in microservices.
- Rolling back the deployment immediately was the correct decision; attempting in-place debugging during customer downtime would have prolonged the outage.
""",
    },
    {
        "category": "postmortems",
        "name": "simulated-incident-coredns-saturation.md",
        "title": "Postmortem: CoreDNS Exhaustion and Inter-Service DNS Resolution Failure",
        "incident_id": "SIM-PM-002",
        "severity": "P1",
        "component": "networking-dns",
        "applicable_versions": "Kubernetes 1.28-1.37",
        "related_upstream_urls": [
            "https://kubernetes.io/docs/tasks/administer-cluster/dns-debugging-resolution/",
        ],
        "content": """# Postmortem: CoreDNS Exhaustion and Inter-Service DNS Resolution Failure

> [!IMPORTANT]
> **SIMULATED INCIDENT — NOT A REAL PRODUCTION EVENT.**
> This incident postmortem is an educational case study created for operational training and automated SRE guidance. All timestamps, incident logs, service names, and metrics are simulated.

## 1. Incident Summary
On 2026-10-02 at 09:18 UTC, cluster-wide DNS lookup failures occurred across `prod-cluster-01`. Internal microservice calls timed out resolving `.cluster.local` domain names, causing widespread 504 Gateway Timeouts across the mobile API gateway. Duration was 22 minutes. The incident was mitigated by scaling CoreDNS from 2 replicas to 8 replicas and deploying NodeLocal DNSCache.

## 2. Customer and Service Impact
- **Impacted Services:** All microservices performing service discovery.
- **Duration:** 22 minutes (09:18 UTC to 09:40 UTC).
- **User Impact:** Mobile app users experienced repeated connection timeout banners.

## 3. Incident Timeline (UTC)
- **09:15 UTC:** Flash marketing campaign launched, driving a 400% surge in user logins.
- **09:18 UTC:** CoreDNS CPU utilization spiked to 100% on both existing replicas.
- **09:20 UTC:** DNS queries began timing out (>2000ms latency); applications experienced connection pool exhaustion.
- **09:22 UTC:** P1 alert `CoreDNSLookupsHighLatency` fired; incident declared.
- **09:26 UTC:** SRE observed CoreDNS dropping UDP packets and scaling delayed due to missing HPA.
- **09:28 UTC:** SRE executed manual emergency scale-up: `kubectl scale deployment/coredns -n kube-system --replicas=8`.
- **09:33 UTC:** Additional CoreDNS pods became ready across worker nodes.
- **09:36 UTC:** DNS query latency dropped from 2400ms to 3.2ms.
- **09:40 UTC:** Microservice error rates returned to 0%; incident mitigated.

## 4. Root Cause
Default Kubernetes cluster deployment provided only 2 CoreDNS replicas with CPU limits set to 100m. The sudden 4x traffic surge generated over 45,000 DNS queries per second, causing CPU throttling, UDP packet drops, and kernel conntrack exhaustion on the two host nodes.

## 5. Contributing Factors
1. **High Default ndots Setting:** Application default `ndots: 5` resulted in up to 4 redundant lookup attempts for every external API call.
2. **Absence of NodeLocal DNSCache:** All cluster DNS traffic had to traverse virtual overlay network to two centralized pods.
3. **Absence of CoreDNS Autoscaling:** CoreDNS was configured with static 2 replicas rather than cluster-proportional or horizontal autoscaling.

## 6. Corrective Actions Taken
- [x] Scaled CoreDNS to 8 replicas.
- [x] Deployed NodeLocal DNSCache DaemonSet across all worker nodes.
- [x] Configured `cluster-proportional-autoscaler` to maintain 1 CoreDNS replica per 16 cluster nodes.
- [x] Configured `ndots: 2` in application pod templates.
""",
    },
    {
        "category": "postmortems",
        "name": "simulated-incident-helm-failed-upgrade.md",
        "title": "Postmortem: Failed Helm Upgrade Leading to Ingress Misrouting and 502 Bad Gateway",
        "incident_id": "SIM-PM-003",
        "severity": "P1",
        "component": "helm-ingress",
        "applicable_versions": "Helm 3.x, Helm 4.x",
        "related_upstream_urls": [
            "https://helm.sh/docs/helm/helm_upgrade/",
        ],
        "content": """# Postmortem: Failed Helm Upgrade Leading to Ingress Misrouting and 502 Bad Gateway

> [!IMPORTANT]
> **SIMULATED INCIDENT — NOT A REAL PRODUCTION EVENT.**
> This incident postmortem is an educational case study created for operational training and automated SRE guidance. All timestamps, incident logs, service names, and metrics are simulated.

## 1. Incident Summary
On 2026-10-19 at 16:04 UTC, a production Helm upgrade of the `customer-portal` chart failed midway due to an immutable field conflict on a Service resource. Ingress controller retained routes to old deleted endpoints while new pods failed to start, returning HTTP 502 Bad Gateway. Total duration was 19 minutes. Mitigated by executing `helm rollback customer-portal 14 -n portal`.

## 2. Customer and Service Impact
- **Impacted Domain:** `portal.example.com`
- **Duration:** 19 minutes (16:04 UTC to 16:23 UTC).
- **User Impact:** 100% of web portal users received HTTP 502 error pages.

## 3. Incident Timeline (UTC)
- **16:02 UTC:** CD pipeline triggered `helm upgrade customer-portal ./charts/customer-portal -n portal -f values-prod.yaml`.
- **16:04 UTC:** Helm failed with error: `cannot patch "customer-portal-svc" with kind Service: field is immutable: spec.clusterIP`.
- **16:06 UTC:** Release entered `failed` state; Ingress controller reloaded configuration pointing to orphaned port.
- **16:08 UTC:** On-call alerted to 502 Bad Gateway errors; P1 declared.
- **16:14 UTC:** SRE investigated revision history: `helm history customer-portal -n portal` showed revision 15 `failed`, revision 14 `deployed`.
- **16:17 UTC:** SRE executed emergency rollback: `helm rollback customer-portal 14 -n portal`.
- **16:21 UTC:** Revision 14 restored healthy Service and Ingress configuration.
- **16:23 UTC:** Traffic verified healthy; incident closed after soak period.

## 4. Root Cause
In chart version v3.2.0, a developer attempted to clear `spec.clusterIP` in the Service template to allow Kubernetes to regenerate it. In Kubernetes, `spec.clusterIP` is an immutable field that cannot be changed once allocated. The API server rejected the patch, causing Helm upgrade to terminate abruptly.

## 5. Corrective Actions Taken
- [x] Restored service via `helm rollback`.
- [x] Fixed chart template to preserve existing `.spec.clusterIP` or omit field during upgrades.
- [x] Added `helm diff upgrade` step to CI pipeline to fail builds that attempt to alter immutable fields.
""",
    },
    {
        "category": "postmortems",
        "name": "simulated-incident-prometheus-cardinality-tsdb-oom.md",
        "title": "Postmortem: High-Cardinality Label Injection Causing Prometheus TSDB OOM CrashLoop",
        "incident_id": "SIM-PM-004",
        "severity": "P1",
        "component": "prometheus-tsdb",
        "applicable_versions": "Prometheus 2.x, Prometheus 3.x",
        "related_upstream_urls": [
            "https://prometheus.io/docs/practices/histograms/",
        ],
        "content": """# Postmortem: High-Cardinality Label Injection Causing Prometheus TSDB OOM CrashLoop

> [!IMPORTANT]
> **SIMULATED INCIDENT — NOT A REAL PRODUCTION EVENT.**
> This incident postmortem is an educational case study created for operational training and automated SRE guidance. All timestamps, incident logs, service names, and metrics are simulated.

## 1. Incident Summary
On 2026-11-01 at 11:24 UTC, the production Prometheus monitoring server crashed and entered an infinite `CrashLoopBackOff` loop after exhausting its 32Gi memory limit. All cluster alerts ceased functioning and Grafana monitoring became completely unavailable. Total duration was 35 minutes. Mitigated by applying a temporary metric relabeling configuration dropping the offending label and deleting the corrupted WAL head chunk.

## 2. Customer and Service Impact
- **Impacted Systems:** Production Prometheus, Alertmanager, Grafana.
- **Duration:** 35 minutes (11:24 UTC to 11:59 UTC).
- **Impact:** Total monitoring blackout; engineers blind to cluster state for 35 minutes.

## 3. Incident Timeline (UTC)
- **10:45 UTC:** A new release of `search-service` went live containing a new metric: `search_requests_total{query="...", user_id="..."}`.
- **11:00 UTC:** Query label produced 1.8 million unique series in 15 minutes.
- **11:24 UTC:** Prometheus memory working set spiked from 14Gi to 32Gi; kernel OOM killer terminated the process.
- **11:26 UTC:** Prometheus restarted; during WAL replay, it re-loaded the massive series head into memory and OOMKilled repeatedly.
- **11:32 UTC:** P1 declared; SRE investigated using `crictl` and Prometheus logs.
- **11:42 UTC:** SRE patched `search-service` ServiceMonitor with `metricRelabelings` to drop `query` and `user_id` labels.
- **11:50 UTC:** SRE temporarily expanded Prometheus memory limit to 48Gi to allow WAL replay completion.
- **11:55 UTC:** Prometheus replayed WAL successfully, applied drop rule, and memory stabilized at 16Gi.
- **11:59 UTC:** Alerting and Grafana dashboards restored.

## 4. Root Cause
`search-service` developers added un-sanitized user query strings and user IDs directly as Prometheus metric labels. Prometheus TSDB is designed for dimensional indexing, not high-cardinality transaction logging. The sudden explosion of millions of unique label combinations overwhelmed TSDB head index memory.

## 5. Corrective Actions Taken
- [x] Applied `action: labeldrop` regex rule in ServiceMonitor.
- [x] Configured `sample_limit: 50000` on all application scrape configurations.
- [x] Implemented automated CI metric linter blocking PRs containing `user_id`, `email`, or dynamic text labels.
""",
    },
]

SIMULATED_CASE_STUDIES: list[dict[str, any]] = [
    {
        "category": "incidents",
        "name": "simulated-case-study-split-brain-statefulset.md",
        "title": "Incident Case Study: Storage Detach Delay and StatefulSet Split-Brain Mitigation",
        "incident_id": "SIM-INC-001",
        "severity": "P1",
        "component": "statefulset-storage",
        "applicable_versions": "Kubernetes 1.28-1.37",
        "related_upstream_urls": [
            "https://kubernetes.io/docs/tasks/run-application/force-delete-stateful-set-pod/",
        ],
        "content": """# Incident Case Study: Storage Detach Delay and StatefulSet Split-Brain Mitigation

> [!IMPORTANT]
> **SIMULATED INCIDENT — NOT A REAL PRODUCTION EVENT.**
> This incident case study is an educational scenario created for operational training and automated SRE guidance. All timestamps, incident logs, service names, and metrics are simulated.

## 1. Scenario Overview
During a scheduled cloud provider network maintenance event on 2026-08-11, worker node `worker-node-14` lost network connectivity to the Kubernetes control plane. The node hosted `postgres-0`, the primary database pod of a stateful PostgreSQL cluster.

The node controller marked `worker-node-14` as `NotReady` after 40 seconds. Following the default pod eviction timeout (300 seconds), Kubernetes attempted to recreate `postgres-0` on `worker-node-22`. However, the underlying cloud block volume (EBS volume `vol-0492819`) remained attached to `worker-node-14`.

## 2. Operational Challenge: The Danger of Premature Force-Deletion
An inexperienced on-call engineer suggested executing:
`kubectl delete pod postgres-0 -n database --force --grace-period=0`

> [!CAUTION]
> **Why this would have caused catastrophic data corruption (Split-Brain):**
> If `worker-node-14` was merely partitioned from the control plane but still running locally, the original PostgreSQL process on `worker-node-14` would continue writing to disk. Force-deleting the pod in Kubernetes does NOT stop the running container on the disconnected host. If the storage driver detached the volume and attached it to `worker-node-22`, TWO running database processes would attempt to write to the same filesystem simultaneously, permanently corrupting the database.

## 3. Correct SRE Mitigation Sequence
The Incident Commander intervened and enforced the safe fencing protocol:
1. **Fencing (Node Power Off):** Before any forced deletion, the engineer used the cloud provider API to hard-terminate `worker-node-14`:
   `aws ec2 stop-instances --instance-ids i-082498192841`
2. **Attachment Verification:** Verified instance state was `stopped`.
3. **Delete VolumeAttachment:** Safely deleted the stuck Kubernetes `VolumeAttachment` object.
4. **Trigger Safe Pod Recreation:** Deleted the pod, allowing `postgres-0` to mount cleanly on `worker-node-22`.

## 4. Key Takeaways
- Never force-delete a StatefulSet pod until physical node power termination is 100% verified.
- Modern Kubernetes clusters should enable the `NonGracefulNodeShutdown` taint feature (`node.kubernetes.io/out-of-service`) to automate safe fencing.
""",
    },
    {
        "category": "incidents",
        "name": "simulated-case-study-cert-expiration-outage.md",
        "title": "Incident Case Study: Kubelet Client Certificate Expiration Outage and Recovery",
        "incident_id": "SIM-INC-002",
        "severity": "P1",
        "component": "pki-certificates",
        "applicable_versions": "Kubernetes 1.28-1.37",
        "related_upstream_urls": [
            "https://kubernetes.io/docs/tasks/administer-cluster/kubeadm/kubeadm-certs/",
        ],
        "content": """# Incident Case Study: Kubelet Client Certificate Expiration Outage and Recovery

> [!IMPORTANT]
> **SIMULATED INCIDENT — NOT A REAL PRODUCTION EVENT.**
> This incident case study is an educational scenario created for operational training and automated SRE guidance. All timestamps, incident logs, service names, and metrics are simulated.

## 1. Scenario Overview
On 2026-07-22 at exactly 00:00 UTC, all 48 worker nodes in production cluster `k8s-prod-eu` simultaneously transitioned to `NotReady`. The API server refused all lease renewal requests from Kubelets. Running user traffic continued for 5 minutes until node controller started evicting pods, resulting in cluster-wide degradation.

## 2. Investigation and Findings
1. Inspection of Kubelet logs on `node-01`:
   `journalctl -u kubelet -n 50` revealed:
   `x509: certificate has expired or is not yet valid: current time 2026-07-22T00:03:12Z is after 2026-07-22T00:00:00Z`
2. The cluster was initially provisioned with `kubeadm` exactly 365 days prior.
3. Kubelet client certificate auto-rotation (`RotateKubeletClientCertificate`) was enabled, but automatic CSR approval controller had been accidentally disabled during a previous control plane hardening task.

## 3. Emergency Recovery Execution
1. On control plane nodes, renewed control plane certificates:
   `sudo kubeadm certs renew all`
2. Restarted control plane components.
3. Approved the pending worker node CSRs:
   `kubectl get csr -o name | xargs -r kubectl certificate approve`
4. On stubborn nodes where bootstrap token had also expired:
   Regenerated bootstrap token: `kubeadm token create` and re-bootstrapped Kubelet client certificates.
5. All 48 nodes returned to `Ready` within 18 minutes.

## 4. Key Takeaways
- Certificate expiration must be monitored with high-priority Prometheus alerts at 30, 14, and 7 days remaining.
- Do not disable `kubelet-client-approver` in `kube-controller-manager` unless an external automated PKI operator handles CSR approvals.
""",
    },
]

