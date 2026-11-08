---
title: "Runbook: Secret Decoding and Environment Injection Failures"
incident_id: "RB-SEC-004"
severity: "P2"
category: "runbooks/security"
component: "secrets"
document_type: "runbook"
doc_type: "runbook"
source_type: "project-authored-sre"
applicable_versions: "Kubernetes 1.28-1.37"
authoring_date: "2026-11-08"
spdx_license: "Apache-2.0"
attribution: "Enterprise SRE Copilot Authors"
content_hash: "b68ea4ce73f5a72dd35e0dceff24b881180dd9effd9bf6bdbe54bca05b29e597"
related_upstream_urls:
  - "https://kubernetes.io/docs/concepts/configuration/secret/"
---

# Runbook: Secret Decoding and Environment Injection Failures

## 1. Incident Identifier
- **ID:** RB-SEC-004
- **Severity:** P2 (High) — Container fails to start with `CreateContainerConfigError` due to missing Secret or invalid base64 encoding.

## 2. Severity Classification
- **P1:** Production database credentials missing, halting application startup.
- **P2:** Non-critical secret key missing.

## 3. Symptoms
- `kubectl get pods` shows `STATUS: CreateContainerConfigError`.
- Pod events report: `secret "<name>" not found` or `key "<key>" not found in secret`.

## 4. Common Causes
1. **Secret Key Name Typo:** PodSpec references `DB_PASS` but Secret key is `db-password`.
2. **Missing Secret in Namespace:** Secret created in `default` namespace instead of application namespace.
3. **Invalid Base64 Encoding:** Value corrupted during manual creation.

## 5. Impact Assessment
- Workload cannot start; deployment blocked.

## 6. Diagnostic Procedure
1. Check pod events for exact missing secret or key name.
2. Verify Secret presence in the same namespace.

## 7. Read-Only Investigation Commands
```bash
# 1. Describe pod to get exact missing secret key
kubectl describe pod <pod-name> -n <namespace> | grep -A 5 "CreateContainerConfigError"

# 2. Check if the referenced Secret exists in the namespace
kubectl get secret <secret-name> -n <namespace>

# 3. List keys available inside the Secret
kubectl get secret <secret-name> -n <namespace> -o jsonpath='{.data}' | jq 'keys'
```

## 8. Expected Outputs
- `secret "db-creds" not found`: Secret missing entirely from namespace.
- `key "password" not found`: Key name mismatch.

## 9. Decision Tree
```
                         [CreateContainerConfigError]
                                      |
                            Does Secret Exist?
                                      |
         +----------------------------+----------------------------+
         |                                                         |
    [Secret Missing]                                          [Key Missing]
         |                                                         |
    Create secret in target namespace                         Update key name in PodSpec
    or sync from external secret vault                        or update secret data
```

## 10. Remediation Steps

### Action A: Create or Update Secret (Safe)
```bash
# Create missing secret key
kubectl create secret generic <secret-name> -n <namespace>   --from-literal=<key>=<value> --dry-run=client -o yaml | kubectl apply -f -
```

## 11. Verification After Remediation
1. Verify pod starts immediately:
   `kubectl get pod <pod-name> -n <namespace>`

## 12. Rollback Procedure
Revert secret modification if wrong values applied.

## 13. Escalation Criteria
- If secret management operator (External Secrets / Vault) is failing to sync, escalate to Security Platform Team.

## 14. Prevention Recommendations
1. Manage secrets using External Secrets Operator or Sealed Secrets synchronized with version control.

## 15. Related Documentation References
- Kubernetes Secrets: `https://kubernetes.io/docs/concepts/configuration/secret/`
