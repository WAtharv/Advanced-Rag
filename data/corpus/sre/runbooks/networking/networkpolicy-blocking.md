---
title: "Runbook: Traffic Dropped by NetworkPolicy Rules"
incident_id: "RB-NET-004"
severity: "P2"
category: "runbooks/networking"
component: "cni-networkpolicy"
document_type: "runbook"
doc_type: "runbook"
source_type: "project-authored-sre"
applicable_versions: "Kubernetes 1.28-1.37"
authoring_date: "2026-11-08"
spdx_license: "Apache-2.0"
attribution: "Enterprise SRE Copilot Authors"
content_hash: "6e13923027779617130591bf7f0532059fda64462eeacbb08af9adccda541567"
related_upstream_urls:
  - "https://kubernetes.io/docs/concepts/services-networking/network-policies/"
  - "https://kubernetes.io/docs/tasks/administer-cluster/declare-network-policy/"
---

# Runbook: Traffic Dropped by NetworkPolicy Rules

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
