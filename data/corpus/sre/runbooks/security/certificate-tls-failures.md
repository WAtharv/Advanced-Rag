---
title: "Runbook: TLS Handshake Failures and Ingress Certificate Mismatches"
incident_id: "RB-SEC-005"
severity: "P1"
category: "runbooks/security"
component: "ingress-tls"
document_type: "runbook"
doc_type: "runbook"
source_type: "project-authored-sre"
applicable_versions: "Kubernetes 1.28-1.37"
authoring_date: "2026-11-08"
spdx_license: "Apache-2.0"
attribution: "Enterprise SRE Copilot Authors"
content_hash: "6813eacfbd46a3e7cb7e96899d78390946a369ea62bd0e26eb7eeaca7863e4ea"
related_upstream_urls:
  - "https://kubernetes.io/docs/concepts/services-networking/ingress/#tls"
---

# Runbook: TLS Handshake Failures and Ingress Certificate Mismatches

## 1. Incident Identifier
- **ID:** RB-SEC-005
- **Severity:** P1 (Critical) — Ingress serving expired TLS certificate or default fake certificate, triggering browser security warnings.

## 2. Severity Classification
- **P1:** Production domain serving invalid or expired certificate to public users.
- **P2:** Internal TLS certificate mismatch on secondary endpoint.

## 3. Symptoms
- Web browsers display `Your connection is not private (NET::ERR_CERT_COMMON_NAME_INVALID)`.
- TLS handshake serves `Kubernetes Ingress Controller Fake Certificate`.

## 4. Common Causes
1. **Cert-Manager Renewal Failure:** ACME HTTP-01 or DNS-01 challenge failed.
2. **Missing TLS Secret in Ingress:** Secret name in Ingress `spec.tls[*].secretName` does not exist or lacks `tls.crt`.

## 5. Impact Assessment
- Browser security blocks preventing all customer web traffic.

## 6. Diagnostic Procedure
1. Check served certificate using OpenSSL s_client.
2. Inspect Cert-Manager Certificate and Order status.

## 7. Read-Only Investigation Commands
```bash
# 1. Test served certificate remotely
echo | openssl s_client -servername <domain> -connect <ingress-ip>:443 2>/dev/null | openssl x509 -noout -dates -subject -issuer

# 2. Check cert-manager certificate status in namespace
kubectl get certificate -n <namespace>
kubectl describe certificate <cert-name> -n <namespace>
```

## 8. Expected Outputs
- `issuer=O = Acorns, CN = Kubernetes Ingress Controller Fake Certificate`: TLS secret missing in Ingress.
- `notAfter` in the past: Certificate expired.

## 9. Decision Tree
```
                         [TLS Certificate Error]
                                    |
                           Is it Fake or Expired?
                                    |
         +--------------------------+--------------------------+
         |                                                     |
    [Fake Certificate]                                   [Expired Certificate]
         |                                                     |
    Check Ingress secretName match                       Trigger cert-manager renew:
    and verify secret has tls.crt                        cmctl renew <cert-name>
```

## 10. Remediation Steps

### Action A: Trigger Immediate Cert-Manager Renewal (Safe)
```bash
# Trigger renewal via cert-manager
kubectl cert-manager renew <cert-name> -n <namespace>
```

### Action B: Apply Emergency Manual Certificate Secret (Disruptive — Requires Approval)
```bash
kubectl create secret tls <secret-name> -n <namespace>   --cert=fullchain.pem --key=privkey.pem --dry-run=client -o yaml | kubectl apply -f -
```

## 11. Verification After Remediation
1. Verify OpenSSL s_client returns valid certificate:
   `echo | openssl s_client -servername <domain> -connect <ingress-ip>:443 2>/dev/null | openssl x509 -noout -dates`
2. Confirm web browsers connect without security warnings.

## 12. Rollback Procedure
Revert Ingress TLS secret if newly applied certificate is invalid.

## 13. Escalation Criteria
- If DNS-01 ACME challenge is blocked by cloud DNS provider API rate limiting, escalate to Cloud Admin.

## 14. Prevention Recommendations
1. Alert on certificate expiration at 30 days remaining using `cert_exporter`.

## 15. Related Documentation References
- Ingress TLS: `https://kubernetes.io/docs/concepts/services-networking/ingress/#tls`
