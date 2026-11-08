---
title: "Runbook: ServiceAccount Token Mount and Auto-Mount Failures"
incident_id: "RB-SEC-002"
severity: "P2"
category: "runbooks/security"
component: "serviceaccount"
document_type: "runbook"
doc_type: "runbook"
source_type: "project-authored-sre"
applicable_versions: "Kubernetes 1.28-1.37"
authoring_date: "2026-11-08"
spdx_license: "Apache-2.0"
attribution: "Enterprise SRE Copilot Authors"
content_hash: "baddeb927426a76a96ec6acca6e8ec10feeaa29761fc948c64337c7dc8c991ae"
related_upstream_urls:
  - "https://kubernetes.io/docs/tasks/configure-pod-container/configure-service-account/"
---

# Runbook: ServiceAccount Token Mount and Auto-Mount Failures

## 1. Incident Identifier
- **ID:** RB-SEC-002
- **Severity:** P2 (High) — Application cannot authenticate to Kubernetes API due to missing `/var/run/secrets/kubernetes.io/serviceaccount/token`.

## 2. Severity Classification
- **P1:** Core monitoring agent or CNI daemon unable to authenticate to API.
- **P2:** Single application pod failing in-cluster API calls.

## 3. Symptoms
- Application logs report: `open /var/run/secrets/kubernetes.io/serviceaccount/token: no such file or directory`.
- Pod fails to initialize in-cluster client SDK.

## 4. Common Causes
1. **automountServiceAccountToken: false:** Pod or ServiceAccount explicitly sets automount to false.
2. **Projected Token Expiration:** Short token audience expiration without automatic reload by application SDK.

## 5. Impact Assessment
- Inability of pod to communicate with Kubernetes API server.

## 6. Diagnostic Procedure
1. Check `automountServiceAccountToken` on both ServiceAccount and PodSpec.
2. Check token projection configuration in PodSpec.

## 7. Read-Only Investigation Commands
```bash
# 1. Check ServiceAccount automount setting
kubectl get sa <sa-name> -n <namespace> -o jsonpath='{.automountServiceAccountToken}'

# 2. Check PodSpec automount setting
kubectl get pod <pod-name> -n <namespace> -o jsonpath='{.spec.automountServiceAccountToken}'
```

## 8. Expected Outputs
- `false`: Token mounting explicitly disabled.

## 9. Decision Tree
```
                         [Token File Missing]
                                  |
                      Check automount Setting
                                  |
         +------------------------+------------------------+
         |                                                 |
  [automount: false]                                [automount: true]
         |                                                 |
  Enable automount on PodSpec                       Check volumeMounts path
                                                    overriding /var/run/secrets
```

## 10. Remediation Steps

### Action A: Enable Token Mounting on PodSpec (Safe)
```yaml
spec:
  automountServiceAccountToken: true
```

## 11. Verification After Remediation
1. Verify token file exists inside container:
   `kubectl exec <pod-name> -n <namespace> -- ls -la /var/run/secrets/kubernetes.io/serviceaccount/token`

## 12. Rollback Procedure
Revert PodSpec manifest if required.

## 13. Escalation Criteria
- If token projection controller is failing in kube-controller-manager, escalate to Control Plane SRE.

## 14. Prevention Recommendations
1. Standardize base pod templates with explicit `automountServiceAccountToken: true` for API-consuming pods.

## 15. Related Documentation References
- Configure Service Accounts: `https://kubernetes.io/docs/tasks/configure-pod-container/configure-service-account/`
