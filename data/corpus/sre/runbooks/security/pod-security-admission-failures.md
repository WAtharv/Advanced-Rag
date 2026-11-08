---
title: "Runbook: Pod Security Standards (PSA/PSS) Admission Rejections"
incident_id: "RB-SEC-003"
severity: "P2"
category: "runbooks/security"
component: "pod-security-admission"
document_type: "runbook"
doc_type: "runbook"
source_type: "project-authored-sre"
applicable_versions: "Kubernetes 1.28-1.37"
authoring_date: "2026-11-08"
spdx_license: "Apache-2.0"
attribution: "Enterprise SRE Copilot Authors"
content_hash: "85087a4659557d164ef41d2b04fac6f8063446c4a74b55c07ce63fbe2bf0d6dc"
related_upstream_urls:
  - "https://kubernetes.io/docs/concepts/security/pod-security-standards/"
  - "https://kubernetes.io/docs/tasks/configure-pod-container/enforce-standards-admission-controller/"
---

# Runbook: Pod Security Standards (PSA/PSS) Admission Rejections

## 1. Incident Identifier
- **ID:** RB-SEC-003
- **Severity:** P2 (High) — Pod creation rejected by Pod Security Admission controller due to violation of `baseline` or `restricted` standards.

## 2. Severity Classification
- **P1:** Production deployment rollout blocked after namespace security policy hardening.
- **P2:** New service onboarding rejected.

## 3. Symptoms
- Deployment rollout fails with: `admission webhook "pod-security.kubernetes.io" denied the request: violates PodSecurity "restricted:latest": allowPrivilegeEscalation != false, runAsNonRoot != true`.

## 4. Common Causes
1. **Container Running as Root:** Missing `runAsNonRoot: true` in `securityContext`.
2. **Missing SeccompProfile:** Restricted standard requires `seccompProfile: {type: RuntimeDefault}`.
3. **HostPort / HostNetwork Specified:** Violates baseline standard.

## 5. Impact Assessment
- Deployment cannot schedule pods in hardened namespaces.

## 6. Diagnostic Procedure
1. Check namespace PSA labels (`pod-security.kubernetes.io/enforce`).
2. Identify specific violated field from admission rejection message.

## 7. Read-Only Investigation Commands
```bash
# 1. Check namespace Pod Security Admission labels
kubectl get namespace <namespace> --show-labels | grep pod-security

# 2. Check deployment rollout status and replica error events
kubectl get rs -n <namespace> -l app=<app-name>
```

## 8. Expected Outputs
- `violates PodSecurity "restricted:latest"`: Specific securityContext fields missing.

## 9. Decision Tree
```
                         [PSA Admission Rejection]
                                     |
                          Can App Run Non-Root?
                                     |
         +---------------------------+---------------------------+
         |                                                       |
   [Yes: Standard App]                                     [No: Requires Root/Host]
         |                                                       |
   Add compliant securityContext                           Exempt namespace or move
   (runAsNonRoot, drop ALL)                                to baseline namespace
```

## 10. Remediation Steps

### Action A: Apply Compliant SecurityContext (Safe)
```yaml
securityContext:
  runAsNonRoot: true
  runAsUser: 10001
  allowPrivilegeEscalation: false
  capabilities:
    drop: ["ALL"]
  seccompProfile:
    type: RuntimeDefault
```

## 11. Verification After Remediation
1. Verify pod creates successfully:
   `kubectl get pods -n <namespace> -l app=<app-name>`

## 12. Rollback Procedure
Revert PodSpec manifest if securityContext breaks application functionality.

## 13. Escalation Criteria
- If application strictly requires root (e.g. storage or network daemon), request security exception from Infosec.

## 14. Prevention Recommendations
1. Enforce non-root execution in Dockerfile builds (`USER 10001`).

## 15. Related Documentation References
- Pod Security Standards: `https://kubernetes.io/docs/concepts/security/pod-security-standards/`
