---
title: "Runbook: Gateway API HTTPRoute and Gateway Routing Failures"
incident_id: "RB-NET-006"
severity: "P1"
category: "runbooks/networking"
component: "gateway-api"
document_type: "runbook"
doc_type: "runbook"
source_type: "project-authored-sre"
applicable_versions: "Kubernetes 1.28-1.37"
authoring_date: "2026-11-08"
spdx_license: "Apache-2.0"
attribution: "Enterprise SRE Copilot Authors"
content_hash: "382da6e8828390b858dd500db06cca8a98df44366b293f3d694d602e4243440d"
related_upstream_urls:
  - "https://gateway-api.sigs.k8s.io/"
  - "https://kubernetes.io/docs/concepts/services-networking/gateway/"
---

# Runbook: Gateway API HTTPRoute and Gateway Routing Failures

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
