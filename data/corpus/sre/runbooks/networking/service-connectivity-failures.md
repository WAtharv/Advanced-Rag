---
title: "Runbook: ClusterIP and Service Connectivity Failures"
incident_id: "RB-NET-002"
severity: "P1"
category: "runbooks/networking"
component: "kube-proxy"
document_type: "runbook"
doc_type: "runbook"
source_type: "project-authored-sre"
applicable_versions: "Kubernetes 1.28-1.37"
authoring_date: "2026-11-08"
spdx_license: "Apache-2.0"
attribution: "Enterprise SRE Copilot Authors"
content_hash: "d6ebc3a4a5fc5025dba13746affb2c98acea19caab999848c40c79d38dbda6cb"
related_upstream_urls:
  - "https://kubernetes.io/docs/tasks/debug/debug-application/debug-service/"
  - "https://kubernetes.io/docs/concepts/services-networking/service/"
---

# Runbook: ClusterIP and Service Connectivity Failures

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
