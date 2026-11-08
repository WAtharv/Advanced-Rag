"""Networking and Storage Operational Runbooks (RB-NET-001 through RB-NET-006, RB-STO-001 through RB-STO-005).

Covers:
- CoreDNS failures, Service connectivity, Ingress 5xx errors
- NetworkPolicy blocking, EndpointSlice desync, Gateway API routing
- PVC Pending, PV binding failures, Volume mount timeouts
- Storage exhaustion, CSI driver provisioning problems
"""

from __future__ import annotations

NETWORKING_RUNBOOKS: list[dict[str, any]] = [
    {
        "category": "runbooks/networking",
        "name": "coredns-failures.md",
        "title": "Runbook: CoreDNS Resolution Failures and DNS Latency Spikes",
        "incident_id": "RB-NET-001",
        "severity": "P1",
        "component": "coredns",
        "applicable_versions": "Kubernetes 1.28-1.37",
        "related_upstream_urls": [
            "https://kubernetes.io/docs/tasks/administer-cluster/dns-debugging-resolution/",
            "https://kubernetes.io/docs/concepts/services-networking/dns-pod-service/",
        ],
        "content": """# Runbook: CoreDNS Resolution Failures and DNS Latency Spikes

## 1. Incident Identifier
- **ID:** RB-NET-001
- **Severity:** P1 (Critical) — Cluster-wide internal or external DNS resolution failing or experiencing extreme latency (>500ms).

## 2. Severity Classification
- **P1:** CoreDNS crashlooping or saturated, causing inter-service connection timeouts across all namespaces.
- **P2:** Intermittent 5-second DNS lookup delays due to Linux conntrack race conditions without NodeLocal DNSCache.
- **P3:** Upstream external DNS resolution failing for non-critical telemetry endpoints.

## 3. Symptoms
- Application logs report `java.net.UnknownHostException`, `dial tcp: lookup <service>: no such host`, or `i/o timeout`.
- HTTP 504 Gateway Timeout across microservices.
- Prometheus alert `CoreDNSLookupsHighLatency` or `CoreDNSErrorsHigh` firing.

## 4. Common Causes
1. **CoreDNS CPU / Memory Saturation:** Too few CoreDNS replicas handling massive request spikes.
2. **Upstream Forwarding Failure:** Upstream resolvers (cloud provider 169.254.169.253 or corporate DNS) throttling or down.
3. **Loop Detection Crash:** CoreDNS enters crashloop after detecting forwarding loop between `/etc/resolv.conf` and `Corefile`.
4. **Linux Conntrack UDP Race Condition:** Kernel dropping simultaneous UDP requests on same 5-tuple without NodeLocal DNS.
5. **ConfigMap Corefile Syntax Error:** Invalid plugin syntax in `coredns` ConfigMap.

## 5. Impact Assessment
- Complete breakdown of service discovery across the cluster; API calls fail to resolve internal service names.

## 6. Diagnostic Procedure
1. Check CoreDNS pod status and restart counts in `kube-system`.
2. Inspect CoreDNS container logs for errors and upstream timeouts.
3. Test DNS resolution from inside a test pod using `dig` or `nslookup`.
4. Check CoreDNS Prometheus metrics for request count and latency.

## 7. Read-Only Investigation Commands
```bash
# 1. Check CoreDNS pod status and distribution across nodes
kubectl get pods -n kube-system -l k8s-app=kube-dns -o wide

# 2. Inspect CoreDNS logs for upstream errors or forwarding loops
kubectl logs -n kube-system -l k8s-app=kube-dns --tail=100

# 3. Test DNS resolution from within cluster using a debug container
kubectl run dns-test --rm -it --image=infoblox/dnstools --restart=Never -- dig kubernetes.default.svc.cluster.local +short

# 4. Test external domain resolution
kubectl run dns-test --rm -it --image=infoblox/dnstools --restart=Never -- dig google.com +short

# 5. PromQL: CoreDNS request rate and latency
# sum(rate(coredns_dns_requests_total[5m])) by (proto)
# histogram_quantile(0.99, sum(rate(coredns_dns_request_duration_seconds_bucket[5m])) by (le))
```

## 8. Expected Outputs
- `[WARNING] plugin/loop: Loop (127.0.0.1:53 -> :53) detected`: Forwarding loop detected.
- `[ERROR] plugin/errors: 2 <domain> A: read udp ...: i/o timeout`: Upstream resolver unreachable.
- `connection timed out; no servers could be reached`: Kubelet DNS IP unreachable from pod network.

## 9. Decision Tree
```
                         [DNS Resolution Failure]
                                     |
                          Test Cluster vs External
                                     |
         +---------------------------+---------------------------+
         |                                                       |
  [Internal Fails (.cluster.local)]                        [External Only Fails]
         |                                                       |
  Check CoreDNS pods & Corefile:                           Check upstream resolver IP:
  - Are CoreDNS pods running?                              - Cloud provider DNS limits?
  - Scale CoreDNS replicas                                 - Corporate firewall blocking port 53
```

## 10. Remediation Steps

### Action A: Scale CoreDNS Replicas Immediately (Safe)
```bash
# Scale CoreDNS to handle sudden traffic load
kubectl scale deployment/coredns -n kube-system --replicas=5
```

### Action B: Fix Corefile ConfigMap Syntax / Upstream Resolver (Safe)
```bash
# Edit CoreDNS configuration
kubectl edit configmap coredns -n kube-system
# After saving, trigger rolling restart of CoreDNS pods:
kubectl rollout restart deployment/coredns -n kube-system
```

### Action C: Deploy NodeLocal DNSCache (Recommended Architecture Solution)
Deploy NodeLocal DNSCache DaemonSet to cache queries locally on each node and prevent UDP conntrack race conditions.

## 11. Verification After Remediation
1. Verify DNS query latency drops below 5ms:
   `kubectl run dns-perf --rm -it --image=infoblox/dnstools --restart=Never -- dig kubernetes.default.svc.cluster.local`
2. Confirm CoreDNS error rate drops to zero in Prometheus metrics.

## 12. Rollback Procedure
Revert Corefile ConfigMap to prior working backup:
```bash
kubectl rollout undo deployment/coredns -n kube-system
```

## 13. Escalation Criteria
- If cloud provider VPC DNS (e.g. AWS Route53 Resolver) is throttling queries (exceeding 1024 packets/sec/ENI), escalate to Network Engineering to provision dedicated caching proxies.

## 14. Prevention Recommendations
1. Deploy NodeLocal DNSCache on all production clusters.
2. Configure CoreDNS Horizontal Pod Autoscaler (or cluster-proportional-autoscaler).
3. Set `ndots: 2` in application pod `dnsConfig` to reduce unnecessary `.cluster.local` search path expansions.

## 15. Related Documentation References
- DNS Debugging Resolution: `https://kubernetes.io/docs/tasks/administer-cluster/dns-debugging-resolution/`
""",
    },
    {
        "category": "runbooks/networking",
        "name": "service-connectivity-failures.md",
        "title": "Runbook: ClusterIP and Service Connectivity Failures",
        "incident_id": "RB-NET-002",
        "severity": "P1",
        "component": "kube-proxy",
        "applicable_versions": "Kubernetes 1.28-1.37",
        "related_upstream_urls": [
            "https://kubernetes.io/docs/tasks/debug/debug-application/debug-service/",
            "https://kubernetes.io/docs/concepts/services-networking/service/",
        ],
        "content": """# Runbook: ClusterIP and Service Connectivity Failures

## 1. Incident Identifier
- **ID:** RB-NET-002
- **Severity:** P1 (Critical) / P2 (High) — Kubernetes Service ClusterIP unreachable or routing traffic to dead backends.

## 2. Severity Classification
- **P1:** Critical API or database Service unreachable cluster-wide.
- **P2:** Single Service failing intermittently on specific worker nodes.
- **P3:** Headless service not publishing SRV records for stopped stateful pods.

## 3. Symptoms
- `curl http://<service-name>:<port>` hangs or returns `Connection refused`.
- Connection succeeds when connecting directly to Pod IP, but fails when using ClusterIP.
- Alert `KubeProxySyncLoopFailed` firing.

## 4. Common Causes
1. **Zero Healthy Endpoints:** Service selector does not match pod labels or all pods failing readiness probes.
2. **Kube-Proxy Out of Sync:** Kube-proxy iptables or IPVS rules not updated on specific nodes.
3. **Port Number / Protocol Mismatch:** Service `targetPort` does not match the actual port listening in container.
4. **CNI Network Partition:** Node overlay routing (Calico, Cilium, Flannel) broken between nodes.

## 5. Impact Assessment
- Total disruption of internal traffic routing between services.

## 6. Diagnostic Procedure
1. Verify Service selector matches running pods.
2. Check Endpoints and EndpointSlice objects.
3. Test connectivity from different nodes to isolate node-specific vs cluster-wide failure.

## 7. Read-Only Investigation Commands
```bash
# 1. Inspect Service configuration and selector
kubectl get service <service-name> -n <namespace> -o wide

# 2. Check healthy endpoints registered for the Service
kubectl get endpoints <service-name> -n <namespace>
kubectl get endpointslices -l kubernetes.io/service-name=<service-name> -n <namespace>

# 3. Verify pod labels match Service selector
kubectl get pods -n <namespace> --show-labels -l <service-selector-key>=<service-selector-val>

# 4. Check kube-proxy logs on the affected node
kubectl logs -n kube-system -l k8s-app=kube-proxy --tail=100
```

## 8. Expected Outputs
- `ENDPOINTS: <none>`: Service selector mismatch or pods unready.
- `targetPort: 8080` while container listens on `3000`: Port configuration error.

## 9. Decision Tree
```
                      [Service Unreachable via ClusterIP]
                                      |
                           Are Endpoints Populated?
                                      |
         +----------------------------+----------------------------+
         |                                                         |
   [Endpoints: <none>]                                     [Endpoints Populated]
         |                                                         |
   Inspect Pod Readiness & Selector:                        Check Kube-Proxy / CNI:
   - Check label exact spelling                             - Test ClusterIP on multiple nodes
   - Check pod readiness probes                             - Restart kube-proxy on broken node
```

## 10. Remediation Steps

### Action A: Correct Service Selector or TargetPort (Safe)
```bash
# Patch service targetPort if misconfigured
kubectl patch service <service-name> -n <namespace> --type=json -p='[{"op": "replace", "path": "/spec/ports/0/targetPort", "value": 8080}]'
```

### Action B: Restart Kube-Proxy DaemonSet (Safe)
```bash
# Rebuild iptables/IPVS rules across all nodes
kubectl rollout restart daemonset/kube-proxy -n kube-system
```

## 11. Verification After Remediation
1. Verify endpoints are populated:
   `kubectl get endpoints <service-name> -n <namespace>`
2. Test HTTP connection:
   `kubectl run test-curl --rm -it --image=curlimages/curl --restart=Never -- curl -m 5 -Iv http://<service-name>.<namespace>.svc.cluster.local:<port>`

## 12. Rollback Procedure
Revert service manifest to prior backup.

## 13. Escalation Criteria
- If CNI IPAM or cross-node VXLAN routing is broken, escalate to Cloud Networking SRE.

## 14. Prevention Recommendations
1. Establish automated CI linting for Kubernetes manifests ensuring Service `targetPort` matches container `containerPort`.
2. Monitor `endpointslice_endpoints_count` metrics in Prometheus.

## 15. Related Documentation References
- Debugging Services: `https://kubernetes.io/docs/tasks/debug/debug-application/debug-service/`
""",
    },
    {
        "category": "runbooks/networking",
        "name": "ingress-5xx-errors.md",
        "title": "Runbook: Ingress HTTP 502, 503, and 504 Upstream Errors",
        "incident_id": "RB-NET-003",
        "severity": "P1",
        "component": "ingress-controller",
        "applicable_versions": "Kubernetes 1.28-1.37",
        "related_upstream_urls": [
            "https://kubernetes.io/docs/concepts/services-networking/ingress/",
            "https://kubernetes.io/docs/concepts/services-networking/ingress-controllers/",
        ],
        "content": """# Runbook: Ingress HTTP 502, 503, and 504 Upstream Errors

## 1. Incident Identifier
- **ID:** RB-NET-003
- **Severity:** P1 (Critical) — External clients receiving HTTP 502 Bad Gateway, 503 Service Unavailable, or 504 Gateway Timeout from Ingress.

## 2. Severity Classification
- **P1:** Customer-facing production ingress returning 5xx on primary domains.
- **P2:** Intermittent 502 errors during deployment rollouts.
- **P3:** Staging ingress failure.

## 3. Symptoms
- Ingress controller logs report: `connect() failed (111: Connection refused) while connecting to upstream` or `upstream timed out (110: Connection timed out)`.
- Client requests fail with HTTP 502/503/504.
- Alert `IngressHigh5xxRate` firing.

## 4. Common Causes
1. **502 Bad Gateway:** Upstream pod terminated abruptly or backend application closed keep-alive connection without notifying ingress.
2. **503 Service Unavailable:** Ingress controller has 0 endpoints registered for target service; all pods unready.
3. **504 Gateway Timeout:** Upstream application took longer to respond than `proxy-read-timeout` (default 60s).
4. **Premature Pod Termination:** Kubelet killed pod immediately before Ingress controller drained active connections (missing `preStop` hook).

## 5. Impact Assessment
- Immediate customer-visible failure; dropped e-commerce transactions, API errors.

## 6. Diagnostic Procedure
1. Check HTTP status code distribution in Ingress controller logs.
2. Check upstream backend pod readiness and endpoint count.
3. Compare connection keepalive and timeout settings between Ingress and backend.

## 7. Read-Only Investigation Commands
```bash
# 1. Inspect recent 5xx error logs in Ingress controller (e.g. ingress-nginx)
kubectl logs -n ingress-nginx -l app.kubernetes.io/name=ingress-nginx --tail=200 | grep -E " (502|503|504) "

# 2. Check Ingress object configuration and backend service binding
kubectl describe ingress <ingress-name> -n <namespace>

# 3. Check endpoint availability for target backend
kubectl get endpoints <backend-service-name> -n <namespace>

# 4. Check upstream response times in ingress controller metrics
# histogram_quantile(0.99, sum(rate(nginx_ingress_controller_request_duration_seconds_bucket{status="502"}[5m])) by (le))
```

## 8. Expected Outputs
- `502: Connection refused`: Backend container not listening or crashed.
- `503: Service Unavailable`: Endpoints count is 0.
- `504: Connection timed out`: Backend processing delay exceeded ingress timeout.

## 9. Decision Tree
```
                         [Ingress 5xx Error Detected]
                                      |
                            Identify Exact Status Code
                                      |
         +----------------------------+----------------------------+
         |                            |                            |
    [HTTP 502]                   [HTTP 503]                   [HTTP 504]
         |                            |                            |
  Pod crashed or race          Zero endpoints ready:        Backend processing slow:
  condition on rollout:        - Check readiness probe      - Check DB locks / latency
  - Add preStop sleep hook     - Scale backend pods         - Increase proxy-read-timeout
```

## 10. Remediation Steps

### Action A: Fix Rolling Update 502 Race Condition (Safe)
Add a `preStop` sleep hook to backend pod so ingress removes endpoint before container stops:
```yaml
lifecycle:
  preStop:
    exec:
      command: ["/bin/sh", "-c", "sleep 10"]
```

### Action B: Increase Ingress Read Timeout for Long-Running APIs (Safe)
```bash
# Add annotation to Ingress object
kubectl annotate ingress <ingress-name> -n <namespace> \
  nginx.ingress.kubernetes.io/proxy-read-timeout="120" \
  nginx.ingress.kubernetes.io/proxy-send-timeout="120" --overwrite
```

### Action C: Emergency Scale-Up of Backend Replicas (Safe)
```bash
kubectl scale deployment/<backend-name> -n <namespace> --replicas=<increased-count>
```

## 11. Verification After Remediation
1. Monitor Ingress access logs for 5 minutes:
   `kubectl logs -n ingress-nginx -l app.kubernetes.io/name=ingress-nginx -f | grep "<host-name>"`
2. Verify HTTP status returns 200 OK.

## 12. Rollback Procedure
Revert Ingress annotations if configuration changes cause issues:
```bash
kubectl apply -f <previous-ingress-manifest.yaml>
```

## 13. Escalation Criteria
- If 504 timeouts are caused by downstream database locks, escalate to Database On-Call.

## 14. Prevention Recommendations
1. Enforce `preStop` hooks on all microservices exposed via Ingress.
2. Configure PodDisruptionBudgets (`minAvailable: 50%`) to prevent rolling deployments from draining too many pods.

## 15. Related Documentation References
- Kubernetes Ingress: `https://kubernetes.io/docs/concepts/services-networking/ingress/`
""",
    },
    {
        "category": "runbooks/networking",
        "name": "networkpolicy-blocking.md",
        "title": "Runbook: Traffic Dropped by NetworkPolicy Rules",
        "incident_id": "RB-NET-004",
        "severity": "P2",
        "component": "cni-networkpolicy",
        "applicable_versions": "Kubernetes 1.28-1.37",
        "related_upstream_urls": [
            "https://kubernetes.io/docs/concepts/services-networking/network-policies/",
            "https://kubernetes.io/docs/tasks/administer-cluster/declare-network-policy/",
        ],
        "content": """# Runbook: Traffic Dropped by NetworkPolicy Rules

## 1. Incident Identifier
- **ID:** RB-NET-004
- **Severity:** P2 (High) — Valid inter-service traffic blocked by restrictive default-deny NetworkPolicy or missing ingress/egress rule.

## 2. Severity Classification
- **P1:** Production inter-service communication severed immediately after deploying security policy.
- **P2:** Single service blocked from accessing external API or database.
- **P3:** Metrics scraper (Prometheus) blocked by pod ingress policy.

## 3. Symptoms
- Application connection attempts hang until timeout: `dial tcp <ip>:<port>: i/o timeout`.
- DNS resolution succeeds, but subsequent TCP connection fails completely.
- Flow logs in CNI (Cilium, Calico) report `Policy drop` or `REJECT`.

## 4. Common Causes
1. **Default-Deny Ingress Applied Without Allow Rules:** Applying `default-deny-ingress` immediately drops all traffic unless explicitly allowed.
2. **Missing Egress Rule for CoreDNS:** Restricting egress traffic without permitting UDP port 53 to `kube-system` breaks DNS lookup.
3. **Pod Label / NamespaceSelector Mismatch:** NetworkPolicy selector does not match the actual labels on client pods or namespace.
4. **Port Number vs Named Port Mismatch:** Policy permits port `8080` but traffic hits port `80` (or named port was changed).

## 5. Impact Assessment
- Applications fail to communicate with databases, caches, or companion services.

## 6. Diagnostic Procedure
1. Identify all NetworkPolicies selecting the target pod and the source pod.
2. Check if default-deny policies are present in the namespace.
3. Inspect CNI policy drop logs.

## 7. Read-Only Investigation Commands
```bash
# 1. List all NetworkPolicies in target namespace
kubectl get networkpolicy -n <namespace> -o wide

# 2. Inspect policies selecting the destination pod
kubectl get networkpolicy -n <namespace> -o json | jq -r '.items[] | select(.spec.podSelector.matchLabels.app=="<app-name>") | .metadata.name'

# 3. Inspect exact rules of the active NetworkPolicy
kubectl get networkpolicy <policy-name> -n <namespace> -o yaml

# 4. Check if namespace has default-deny policies
kubectl get networkpolicy -n <namespace> -o json | jq -r '.items[] | select(.spec.podSelector.matchLabels=={}) | .metadata.name'

# 5. (If Cilium CNI) Check dropped packet monitor
# cilium monitor --type drop
```

## 8. Expected Outputs
- `policyTypes: [Ingress]` with empty `ingress: []`: Blocks 100% of ingress traffic to selected pods.
- `policyTypes: [Egress]` without rule for UDP 53: Blocks DNS resolution.

## 9. Decision Tree
```
                         [Traffic Dropped by NetworkPolicy]
                                         |
                              Is DNS Failing or TCP Port?
                                         |
         +-------------------------------+-------------------------------+
         |                                                               |
    [DNS Times Out]                                             [Port Connection Times Out]
         |                                                               |
  Check Egress rules:                                           Check Ingress rules on server:
  - Add egress rule for UDP 53 to kube-system                   - Verify namespaceSelector labels
                                                                - Verify podSelector labels & port
```

## 10. Remediation Steps

### Action A: Add CoreDNS Egress Rule to Policy (Safe)
```yaml
egress:
- to:
  - namespaceSelector:
      matchLabels:
        kubernetes.io/metadata.name: kube-system
    podSelector:
      matchLabels:
        k8s-app: kube-dns
  ports:
  - protocol: UDP
    port: 53
```

### Action B: Allow Client Namespace / Pod Ingress (Safe)
```yaml
ingress:
- from:
  - namespaceSelector:
      matchLabels:
        kubernetes.io/metadata.name: <client-namespace>
    podSelector:
      matchLabels:
        app: <client-app>
  ports:
  - protocol: TCP
    port: 8080
```

### Action C: Temporarily Delete Errant Policy During Outage (Requires Approval)
```bash
# Export backup first:
kubectl get networkpolicy <policy-name> -n <namespace> -o yaml > /tmp/netpol-backup.yaml
# Delete blocking policy:
kubectl delete networkpolicy <policy-name> -n <namespace>
```

## 11. Verification After Remediation
1. Test connection between client and server pods:
   `kubectl exec <client-pod> -n <namespace> -- nc -zvw3 <server-ip> 8080`
2. Verify exit status returns 0 (connected).

## 12. Rollback Procedure
Re-apply `/tmp/netpol-backup.yaml` once modified.

## 13. Escalation Criteria
- If CNI policy enforcement engine fails internally (e.g. eBPF map full), escalate to Platform Networking Team.

## 14. Prevention Recommendations
1. Test all NetworkPolicies in staging using CNI audit/monitoring mode before enforcing `deny`.
2. Always include standard CoreDNS egress rule in any base egress policy.

## 15. Related Documentation References
- Network Policies: `https://kubernetes.io/docs/concepts/services-networking/network-policies/`
""",
    },
    {
        "category": "runbooks/networking",
        "name": "endpointslice-problems.md",
        "title": "Runbook: EndpointSlice Desynchronization and Stale Backends",
        "incident_id": "RB-NET-005",
        "severity": "P2",
        "component": "endpointslice-controller",
        "applicable_versions": "Kubernetes 1.28-1.37",
        "related_upstream_urls": [
            "https://kubernetes.io/docs/concepts/services-networking/endpoint-slices/",
        ],
        "content": """# Runbook: EndpointSlice Desynchronization and Stale Backends

## 1. Incident Identifier
- **ID:** RB-NET-005
- **Severity:** P2 (High) — EndpointSlice controller fails to update backend endpoints, causing traffic to be routed to terminated or non-existent pods.

## 2. Severity Classification
- **P1:** Outdated endpoints routing customer traffic into dead IP blackholes across multiple services.
- **P2:** Intermittent connection drops after pod rollouts due to stale EndpointSlice references.
- **P3:** Stale EndpointSlice mirror in test namespace.

## 3. Symptoms
- Kube-proxy routes traffic to terminated pod IPs, returning TCP RST or timeout.
- `kubectl get endpointslices` contains addresses of pods that no longer exist in `kubectl get pods`.
- Alert `EndpointSliceMirroringFailed` firing.

## 4. Common Causes
1. **EndpointSlice Controller Backlog / Crash:** Kube-controller-manager saturated, delaying EndpointSlice updates.
2. **Orphaned EndpointSlices:** Rapid creation and deletion of services leaving orphaned slices.
3. **Dual-Stack IPv4/IPv6 Misconfiguration:** Service configured with `IPFamilies: [IPv6, IPv4]` but nodes only support IPv4.
4. **Custom Controller Interference:** Third-party operator manually modifying managed EndpointSlices.

## 5. Impact Assessment
- Traffic blackholing; 5-10% packet loss during scaling or rolling deployment events.

## 6. Diagnostic Procedure
1. List EndpointSlices for the affected service.
2. Compare IP addresses in EndpointSlice with current live pod IPs.
3. Check kube-controller-manager logs for sync errors.

## 7. Read-Only Investigation Commands
```bash
# 1. Inspect EndpointSlices associated with service
kubectl get endpointslices -l kubernetes.io/service-name=<service-name> -n <namespace> -o yaml

# 2. Compare against active running pod IPs
kubectl get pods -n <namespace> -l <service-selector> -o custom-columns=NAME:.metadata.name,STATUS:.status.phase,IP:.status.podIP

# 3. Check endpoint conditions in slice (Ready, Serving, Terminating)
kubectl get endpointslices -l kubernetes.io/service-name=<service-name> -n <namespace> -o jsonpath='{range .items[*].endpoints[*]}{.addresses[*]}{"\tReady:"}{.conditions.ready}{"\tTerminating:"}{.conditions.terminating}{"\n"}{end}'
```

## 8. Expected Outputs
- IP present in EndpointSlice marked `ready: true`, but pod deleted >5 minutes ago: Stale endpoint desynchronization confirmed.

## 9. Decision Tree
```
                         [Stale Endpoints Detected]
                                     |
                          Are Slices Auto-Generated?
                                     |
         +---------------------------+---------------------------+
         |                                                       |
   [Managed by EndpointSlice Controller]                   [Manually Managed]
         |                                                       |
   Delete stale EndpointSlice object;                      Fix external controller
   controller will immediately recreate fresh slice        or update endpoints
```

## 10. Remediation Steps

### Action A: Delete Stale EndpointSlice to Trigger Instant Regeneration (Safe)
```bash
# Deleting the managed EndpointSlice forces the endpointslice-controller to recreate it immediately from live pods
kubectl delete endpointslice <stale-slice-name> -n <namespace>
```

### Action B: Restart Kube-Controller-Manager if Stalled (Control Plane Access)
```bash
# Restart controller-manager static pod on control plane host
sudo kill -s SIGHUP $(pidof kube-controller-manager)
```

## 11. Verification After Remediation
1. Verify new EndpointSlice is generated within 5 seconds:
   `kubectl get endpointslices -l kubernetes.io/service-name=<service-name> -n <namespace>`
2. Confirm all listed IPs correspond to currently active pods in `Running` state.

## 12. Rollback Procedure
N/A — EndpointSlice regeneration is automatic and idempotent.

## 13. Escalation Criteria
- If kube-controller-manager sync loop is permanently stuck across all services, escalate as P1 Control Plane Incident.

## 14. Prevention Recommendations
1. Ensure `kube-controller-manager` has adequate CPU resources and `--concurrent-endpoint-syncs` is tuned for large clusters (>500 nodes).

## 15. Related Documentation References
- EndpointSlices: `https://kubernetes.io/docs/concepts/services-networking/endpoint-slices/`
""",
    },
    {
        "category": "runbooks/networking",
        "name": "gateway-api-routing-failures.md",
        "title": "Runbook: Gateway API HTTPRoute and Gateway Routing Failures",
        "incident_id": "RB-NET-006",
        "severity": "P1",
        "component": "gateway-api",
        "applicable_versions": "Kubernetes 1.28-1.37",
        "related_upstream_urls": [
            "https://gateway-api.sigs.k8s.io/",
            "https://kubernetes.io/docs/concepts/services-networking/gateway/",
        ],
        "content": """# Runbook: Gateway API HTTPRoute and Gateway Routing Failures

## 1. Incident Identifier
- **ID:** RB-NET-006
- **Severity:** P1 (Critical) / P2 (High) — Gateway API Gateway or HTTPRoute failing to program proxy data plane, dropping external ingress.

## 2. Severity Classification
- **P1:** Gateway resource in `Programmed=False` condition, taking down domain routing.
- **P2:** HTTPRoute rejected by Gateway controller due to listener conflict or hostname mismatch.
- **P3:** Cross-namespace route attachment denied by ReferenceGrant policy.

## 3. Symptoms
- `kubectl get httproute` shows `Accepted=False` or `Programmed=False`.
- Clients receive HTTP 404 Not Found from Gateway load balancer.
- Gateway controller events report: `InvalidListenerRef` or `ResolvedRefs=False`.

## 4. Common Causes
1. **Missing ReferenceGrant:** HTTPRoute in namespace `app` references a Gateway or Service in namespace `infra`, but no `ReferenceGrant` authorizes cross-namespace attachment.
2. **Port / Protocol Listener Mismatch:** HTTPRoute specifies `sectionName` that does not match any listener defined in the Gateway.
3. **GatewayClass Not Installed or Unreconciled:** The referenced `gatewayClassName` has no active controller running in the cluster.
4. **Certificate Secret Missing:** Gateway TLS listener references a Secret that does not exist or lacks valid TLS certificate pairs.

## 5. Impact Assessment
- Modern Kubernetes ingress traffic dropped before reaching services.

## 6. Diagnostic Procedure
1. Inspect conditions on the `Gateway` resource.
2. Inspect conditions on the `HTTPRoute` resource.
3. Verify `ReferenceGrant` objects if cross-namespace routing is used.

## 7. Read-Only Investigation Commands
```bash
# 1. Check Gateway status and conditions
kubectl get gateway <gateway-name> -n <namespace> -o yaml

# 2. Check HTTPRoute status and conditions
kubectl get httproute <route-name> -n <namespace> -o yaml

# 3. Check GatewayClass controller status
kubectl get gatewayclass

# 4. Check ReferenceGrants in target namespaces
kubectl get referencegrant -A
```

## 8. Expected Outputs
- `Accepted: False Reason: NoMatchingListener`: HTTPRoute parentRef does not match Gateway listeners.
- `ResolvedRefs: False Reason: RefNotPermitted`: Cross-namespace reference disallowed; missing ReferenceGrant.
- `Programmed: True`: Gateway and routes are healthy.

## 9. Decision Tree
```
                         [Gateway API Routing Failure]
                                       |
                           Check HTTPRoute Conditions
                                       |
         +-----------------------------+-----------------------------+
         |                                                           |
  [RefNotPermitted]                                           [NoMatchingListener]
         |                                                           |
  Cross-namespace denied:                                     Listener mismatch:
  - Create ReferenceGrant in target ns                        - Fix sectionName or port
    permitting HTTPRoute binding                              - Fix hostname rule
```

## 10. Remediation Steps

### Action A: Authorize Cross-Namespace Reference via ReferenceGrant (Safe)
```yaml
apiVersion: gateway.networking.k8s.io/v1beta1
kind: ReferenceGrant
metadata:
  name: allow-app-routes
  namespace: infra
spec:
  from:
  - group: gateway.networking.k8s.io
    kind: HTTPRoute
    namespace: app
  to:
  - group: ""
    kind: Service
```

### Action B: Align HTTPRoute ParentRef with Gateway Listener (Safe)
```yaml
parentRefs:
- name: prod-gateway
  namespace: infra
  sectionName: https-prod
```

## 11. Verification After Remediation
1. Verify HTTPRoute transitions to `Accepted=True` and `Programmed=True`:
   `kubectl get httproute <route-name> -n <namespace>`
2. Test end-to-end HTTPS request through Gateway load balancer.

## 12. Rollback Procedure
Revert HTTPRoute manifest to prior working revision.

## 13. Escalation Criteria
- If Gateway controller (e.g. Envoy Gateway, Istio, Cilium) crashes or enters infinite reconcile loop, escalate to Service Mesh / Ingress SRE.

## 14. Prevention Recommendations
1. Establish automated validation webhooks for Gateway API CRDs.
2. Manage shared Gateways centrally via Infrastructure GitOps repos.

## 15. Related Documentation References
- Kubernetes Gateway API: `https://kubernetes.io/docs/concepts/services-networking/gateway/`
""",
    },
]

STORAGE_RUNBOOKS: list[dict[str, any]] = [
    {
        "category": "runbooks/storage",
        "name": "pvc-pending.md",
        "title": "Runbook: PersistentVolumeClaim Stuck in Pending",
        "incident_id": "RB-STO-001",
        "severity": "P2",
        "component": "pv-controller",
        "applicable_versions": "Kubernetes 1.28-1.37",
        "related_upstream_urls": [
            "https://kubernetes.io/docs/concepts/storage/persistent-volumes/",
            "https://kubernetes.io/docs/tasks/configure-pod-container/configure-persistent-volume-storage/",
        ],
        "content": """# Runbook: PersistentVolumeClaim Stuck in Pending

## 1. Incident Identifier
- **ID:** RB-STO-001
- **Severity:** P2 (High) — PersistentVolumeClaim cannot bind or provision volume, leaving consuming pods stuck in `Pending`.

## 2. Severity Classification
- **P1:** Stateful workload pod (database, queue) unable to schedule due to unbound PVC.
- **P2:** New PVC provisioned during deployment upgrade cannot bind.
- **P3:** Test PVC creation delay.

## 3. Symptoms
- `kubectl get pvc` displays `STATUS: Pending` indefinitely.
- Consuming pod event reports: `pod has unbound immediate PersistentVolumeClaims`.
- Alert `PersistentVolumeClaimPending` firing.

## 4. Common Causes
1. **WaitForFirstConsumer Mode:** StorageClass uses `volumeBindingMode: WaitForFirstConsumer`, waiting for a pod to be scheduled first.
2. **Nonexistent StorageClass:** PVC references `storageClassName: fast-ssd` which does not exist in cluster.
3. **No Available PVs (Static Provisioning):** Capacity or access mode mismatch (e.g. PVC requests 50Gi `ReadWriteMany`, but only 20Gi `ReadWriteOnce` PVs exist).
4. **Cloud Storage Quota Exceeded:** Cloud provider EBS/PD storage quota reached in the region.
5. **CSI Driver Failure:** CSI provisioner container crashed or credentials expired.

## 5. Impact Assessment
- Pods requiring persistent storage cannot start; database failover blocked.

## 6. Diagnostic Procedure
1. Check PVC status, annotations, and events.
2. Check if StorageClass exists and inspect its `volumeBindingMode`.
3. Check CSI provisioner logs in `kube-system` or storage namespace.

## 7. Read-Only Investigation Commands
```bash
# 1. Inspect PVC events and failure reason
kubectl describe pvc <pvc-name> -n <namespace> | grep -A 10 "Events:"

# 2. Check StorageClass configuration
kubectl get sc
kubectl describe sc <storage-class-name>

# 3. Check if any unbound PVs exist in the cluster
kubectl get pv | grep -i available

# 4. Check CSI driver pod status
kubectl get pods -n kube-system -l app.kubernetes.io/component=csi-driver
```

## 8. Expected Outputs
- `waiting for first consumer to be created before binding`: Normal behavior for `WaitForFirstConsumer`; schedule the pod to resolve.
- `storageclass.storage.k8s.io "<name>" not found`: StorageClass name typo.
- `failed to provision volume with StorageClass ... cloud quota exceeded`: Cloud provider API error.

## 9. Decision Tree
```
                         [PVC Stuck in Pending]
                                    |
                           Check Describe Events
                                    |
         +--------------------------+--------------------------+
         |                                                     |
  [WaitForFirstConsumer]                                [Provisioning Failed]
         |                                                     |
  Expected behavior:                                    Check CSI logs & StorageClass:
  - Create/Schedule the consuming pod                   - Does StorageClass exist?
  - Verify node has zone matching StorageClass          - Check cloud storage quota
```

## 10. Remediation Steps

### Action A: Schedule Consuming Pod (If WaitForFirstConsumer) (Safe)
If `WaitForFirstConsumer` is configured, the volume will provision automatically once the pod passes scheduling. Verify pod is not blocked by taints.

### Action B: Fix StorageClass Name on PVC (Safe / Requires PVC Re-creation)
```bash
# PVC spec cannot be edited in-place; export, fix storageClassName, and reapply
kubectl get pvc <pvc-name> -n <namespace> -o yaml > /tmp/pvc.yaml
# Edit /tmp/pvc.yaml to set valid storageClassName
kubectl apply -f /tmp/pvc.yaml
```

### Action C: Restart CSI Provisioner Pod (Safe)
```bash
kubectl rollout restart deployment/ebs-csi-controller -n kube-system
```

## 11. Verification After Remediation
1. Verify PVC transitions to `Bound`:
   `kubectl get pvc <pvc-name> -n <namespace>`
2. Verify PV is automatically provisioned and mapped to the PVC.

## 12. Rollback Procedure
N/A — Delete failed unattached PVC if created in error.

## 13. Escalation Criteria
- If cloud provider volume quota is exhausted, escalate to Cloud Admin for storage limit increase.

## 14. Prevention Recommendations
1. Set a cluster-default StorageClass annotated with `storageclass.kubernetes.io/is-default-class: "true"`.
2. Monitor PVC pending duration alerts.

## 15. Related Documentation References
- Persistent Volumes: `https://kubernetes.io/docs/concepts/storage/persistent-volumes/`
""",
    },
    {
        "category": "runbooks/storage",
        "name": "pv-binding-failures.md",
        "title": "Runbook: PV Binding and StorageClass Mismatches",
        "incident_id": "RB-STO-002",
        "severity": "P2",
        "component": "pv-controller",
        "applicable_versions": "Kubernetes 1.28-1.37",
        "related_upstream_urls": [
            "https://kubernetes.io/docs/concepts/storage/storage-classes/",
        ],
        "content": """# Runbook: PV Binding and StorageClass Mismatches

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
""",
    },
    {
        "category": "runbooks/storage",
        "name": "volume-mount-failures.md",
        "title": "Runbook: VolumeAttachment Timeout and Multi-Attach Errors",
        "incident_id": "RB-STO-003",
        "severity": "P1",
        "component": "volume-attachment",
        "applicable_versions": "Kubernetes 1.28-1.37",
        "related_upstream_urls": [
            "https://kubernetes.io/docs/tasks/administer-cluster/safely-drain-node/",
        ],
        "content": """# Runbook: VolumeAttachment Timeout and Multi-Attach Errors

## 1. Incident Identifier
- **ID:** RB-STO-003
- **Severity:** P1 (Critical) — Pod stuck in `ContainerCreating` due to cloud volume attachment timeout or `Multi-Attach error`.

## 2. Severity Classification
- **P1:** Production database or stateful pod cannot start after node crash.
- **P2:** Delayed rolling deployment of single stateful workload.

## 3. Symptoms
- Pod remains in `ContainerCreating` for >10 minutes.
- Pod events report: `Multi-Attach error for volume ... Volume is already exclusively attached to one node and can't be attached to another`.
- Kubelet event: `MountVolume.SetUp failed for volume ... target path ... device or resource busy`.

## 4. Common Causes
1. **Unclean Node Termination:** Previous host node crashed hard without executing volume detachment API calls.
2. **Cloud Detach Latency:** AWS EBS / GCP PD takes up to 6 minutes to force-detach from unresponsive VM.
3. **Stale VolumeAttachment Object:** Kubernetes API server retains `VolumeAttachment` record for dead node.
4. **Filesystem Mount Lock / Corruption:** Previous container crashed holding exclusive lock or filesystem requires `fsck`.

## 5. Impact Assessment
- Single-replica stateful services suffer extended downtime during failovers.

## 6. Diagnostic Procedure
1. Check `VolumeAttachment` objects for the stuck volume.
2. Verify status of the previous node that held the volume.
3. Check cloud console to verify physical volume attachment state.

## 7. Read-Only Investigation Commands
```bash
# 1. Identify PV name mounted by failing pod
PV_NAME=$(kubectl get pvc <pvc-name> -n <namespace> -o jsonpath='{.spec.volumeName}')

# 2. Check VolumeAttachment objects for this PV
kubectl get volumeattachment | grep $PV_NAME

# 3. Inspect pod events for exact multi-attach error
kubectl describe pod <pod-name> -n <namespace> | grep -E "(Multi-Attach|FailedAttach|MountVolume)"

# 4. Check status of the previous node hosting the attachment
kubectl get node <old-node-name>
```

## 8. Expected Outputs
- `Multi-Attach error for volume "pvc-xxx": Volume is already exclusively attached to node "node-1"`: VolumeAttachment stuck on node-1.

## 9. Decision Tree
```
                         [Multi-Attach Volume Error]
                                      |
                           Is Previous Node Alive?
                                      |
         +----------------------------+----------------------------+
         |                                                         |
    [Node is ALIVE]                                           [Node is DEAD]
         |                                                         |
  DO NOT force detach:                                      Safe to detach:
  - Drain node gracefully                                   - Delete stale VolumeAttachment
  - Restart kubelet on old node                             - Wait for CSI attachment to new node
```

## 10. Remediation Steps

### Action A: Gracefully Drain Previous Node (Safe)
```bash
# If old node is alive, drain it to cleanly detach volume
kubectl drain <old-node-name> --ignore-daemonsets --delete-emptydir-data
```

### Action B: Force Remove VolumeAttachment ONLY AFTER Node Shutdown (Disruptive — Requires L2 Approval)
> [!CAUTION]
> Verify in cloud provider console (AWS/GCP/Azure) that the old VM is completely STOPPED or TERMINATED before proceeding.

```bash
# 1. Confirm VM power state is STOPPED in cloud provider
# 2. Delete the stuck VolumeAttachment object in Kubernetes
kubectl get volumeattachment -o json | jq -r '.items[] | select(.spec.source.persistentVolumeName=="'$PV_NAME'") | .metadata.name' | xargs -r kubectl delete volumeattachment

# 3. Delete and recreate the pending pod to re-trigger attachment
kubectl delete pod <pod-name> -n <namespace> --grace-period=0 --force
```

## 11. Verification After Remediation
1. Verify pod transitions to `Running`:
   `kubectl get pod <pod-name> -n <namespace>`
2. Verify volume mounted inside container:
   `kubectl exec <pod-name> -n <namespace> -- df -h <mount-path>`

## 12. Rollback Procedure
N/A — Volume attachment recovery is forward-reconciling.

## 13. Escalation Criteria
- If cloud volume reports `I/O error` or filesystem is read-only after attachment, escalate to Storage Admin for `fsck` snapshot recovery.

## 14. Prevention Recommendations
1. Enable `NonGracefulNodeShutdown` feature gate (standard in modern Kubernetes) to automate failover fencing.
2. Upgrade cloud CSI drivers to support automated force-detach timeouts.

## 15. Related Documentation References
- Safely Drain Nodes: `https://kubernetes.io/docs/tasks/administer-cluster/safely-drain-node/`
""",
    },
    {
        "category": "runbooks/storage",
        "name": "storage-exhaustion.md",
        "title": "Runbook: Persistent Volume Filesystem Exhaustion",
        "incident_id": "RB-STO-004",
        "severity": "P1",
        "component": "volume-resize",
        "applicable_versions": "Kubernetes 1.28-1.37",
        "related_upstream_urls": [
            "https://kubernetes.io/docs/concepts/storage/persistent-volumes/#expanding-persistent-volumes-claims/",
        ],
        "content": """# Runbook: Persistent Volume Filesystem Exhaustion

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
""",
    },
    {
        "category": "runbooks/storage",
        "name": "csi-provisioning-problems.md",
        "title": "Runbook: CSI Driver Provisioning and Timeout Failures",
        "incident_id": "RB-STO-005",
        "severity": "P1",
        "component": "csi-driver",
        "applicable_versions": "Kubernetes 1.28-1.37",
        "related_upstream_urls": [
            "https://kubernetes.io/docs/concepts/storage/volumes/#csi/",
        ],
        "content": """# Runbook: CSI Driver Provisioning and Timeout Failures

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
""",
    },
]

