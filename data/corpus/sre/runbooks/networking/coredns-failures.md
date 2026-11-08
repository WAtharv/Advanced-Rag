---
title: "Runbook: CoreDNS Resolution Failures and DNS Latency Spikes"
incident_id: "RB-NET-001"
severity: "P1"
category: "runbooks/networking"
component: "coredns"
document_type: "runbook"
doc_type: "runbook"
source_type: "project-authored-sre"
applicable_versions: "Kubernetes 1.28-1.37"
authoring_date: "2026-11-08"
spdx_license: "Apache-2.0"
attribution: "Enterprise SRE Copilot Authors"
content_hash: "6044b20d37b3dcf19a02728426f948ecc1ad6fa5c256326787457d48277a98e9"
related_upstream_urls:
  - "https://kubernetes.io/docs/tasks/administer-cluster/dns-debugging-resolution/"
  - "https://kubernetes.io/docs/concepts/services-networking/dns-pod-service/"
---

# Runbook: CoreDNS Resolution Failures and DNS Latency Spikes

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
