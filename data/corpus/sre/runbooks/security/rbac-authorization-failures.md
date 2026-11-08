---
title: "Runbook: RBAC Authorization and Permission Denied (403) Failures"
incident_id: "RB-SEC-001"
severity: "P2"
category: "runbooks/security"
component: "rbac-authorizer"
document_type: "runbook"
doc_type: "runbook"
source_type: "project-authored-sre"
applicable_versions: "Kubernetes 1.28-1.37"
authoring_date: "2026-11-08"
spdx_license: "Apache-2.0"
attribution: "Enterprise SRE Copilot Authors"
content_hash: "a6ad85ebd294b11e1707d34aebfb10ae9fb0ba72ceb04725abc24c97e63fb70d"
related_upstream_urls:
  - "https://kubernetes.io/docs/reference/access-authn-authz/rbac/"
---

# Runbook: RBAC Authorization and Permission Denied (403) Failures

## 1. Incident Identifier
- **ID:** RB-SEC-001
- **Severity:** P2 (High) — Application pod, controller, or operator denied API access with `403 Forbidden`.

## 2. Severity Classification
- **P1:** Production operator or ingress controller denied permission to watch Endpoints or Secrets.
- **P2:** Microservice client unable to read ConfigMap.
- **P3:** Developer unable to view staging pods.

## 3. Symptoms
- Application pod logs report: `User "system:serviceaccount:app:my-sa" cannot list resource "pods" in API group "" at the cluster scope`.
- Controller crashes or fails to reconcile resources.

## 4. Common Causes
1. **Missing RoleBinding / ClusterRoleBinding:** ServiceAccount not bound to required Role.
2. **Missing API Group in Rule:** Rule defines `resources: ["deployments"]` but omits `apiGroups: ["apps"]`.
3. **Namespace Scope vs Cluster Scope:** `RoleBinding` used for cluster-scoped resources (e.g. Nodes, PersistentVolumes, Namespaces).

## 5. Impact Assessment
- Application or controller cannot function; reconciliation halted.

## 6. Diagnostic Procedure
1. Test ServiceAccount permissions using `kubectl auth can-i`.
2. Inspect Role and RoleBinding bindings.

## 7. Read-Only Investigation Commands
```bash
# 1. Test ServiceAccount permissions using kubectl auth can-i
kubectl auth can-i get pods --as=system:serviceaccount:<namespace>:<sa-name> -n <namespace>

# 2. Check RoleBindings associated with the ServiceAccount
kubectl get rolebindings,clusterrolebindings -A -o json | jq -r '.items[] | select(.subjects[]?.name=="<sa-name>") | .metadata.name'
```

## 8. Expected Outputs
- `no`: Confirms authorization denial.
- `yes`: Authorization granted.

## 9. Decision Tree
```
                         [RBAC Permission Denied (403)]
                                       |
                          Is Resource Cluster-Scoped?
                                       |
         +-----------------------------+-----------------------------+
         |                                                           |
   [Yes: Cluster-Scoped (Node/PV)]                             [No: Namespace-Scoped]
         |                                                           |
   Use ClusterRole and ClusterRoleBinding                      Use Role and RoleBinding
```

## 10. Remediation Steps

### Action A: Grant Required Role via RoleBinding (Safe)
```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: app-pod-reader
  namespace: app
subjects:
- kind: ServiceAccount
  name: my-sa
  namespace: app
roleRef:
  kind: ClusterRole
  name: view
  apiGroup: rbac.authorization.k8s.io
```

## 11. Verification After Remediation
1. Confirm authorization check passes:
   `kubectl auth can-i get pods --as=system:serviceaccount:<namespace>:<sa-name> -n <namespace>` (returns `yes`).

## 12. Rollback Procedure
Delete applied RoleBinding if granted in error.

## 13. Escalation Criteria
- If permissions require elevated cluster-admin access, escalate to Security Architect.

## 14. Prevention Recommendations
1. Follow principle of least privilege: grant only necessary verbs (`get`, `list`, `watch`).

## 15. Related Documentation References
- Kubernetes RBAC: `https://kubernetes.io/docs/reference/access-authn-authz/rbac/`
