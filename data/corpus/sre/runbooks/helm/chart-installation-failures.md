---
title: "Runbook: Helm Chart Installation Failures and Schema Rejections"
incident_id: "RB-HLM-001"
severity: "P2"
category: "runbooks/helm"
component: "helm-install"
document_type: "runbook"
doc_type: "runbook"
source_type: "project-authored-sre"
applicable_versions: "Helm 3.x, Helm 4.x"
authoring_date: "2026-11-08"
spdx_license: "Apache-2.0"
attribution: "Enterprise SRE Copilot Authors"
content_hash: "5a3db5251fc188c69ad965d110118d3939a3daaec52b0b5dedf369cb4ed9e833"
related_upstream_urls:
  - "https://helm.sh/docs/intro/using_helm/"
  - "https://helm.sh/docs/chart_best_practices/schema/"
---

# Runbook: Helm Chart Installation Failures and Schema Rejections

## 1. Incident Identifier
- **ID:** RB-HLM-001
- **Severity:** P2 (High) — Helm chart installation aborted due to values schema validation error, CRD absence, or pre-install hook failure.

## 2. Severity Classification
- **P1:** Production application deployment or hotfix blocked by helm install failure.
- **P2:** New service onboarding delayed due to chart schema rejections.
- **P3:** Staging release installation error.

## 3. Symptoms
- `helm install` exits with code 1 and error: `values don't meet the specifications of the schema(s) in the following chart(s): ...`.
- Error: `rendered manifests contain a resource that already exists`.
- Error: `timed out waiting for the condition`.

## 4. Common Causes
1. **JSON Schema Violation:** Custom `values.yaml` provides invalid type (e.g. integer instead of string, or missing required field).
2. **Resource Already Exists:** A Secret, ConfigMap, or CRD from a previous unmanaged install exists in the namespace without Helm ownership labels.
3. **Pre-Install Hook Timeout / Crash:** A database migration job annotated with `helm.sh/hook: pre-install` failed or timed out.
4. **Missing CRDs:** Chart deploys custom resources, but required CustomResourceDefinitions are not installed in the cluster.

## 5. Impact Assessment
- Deployment aborted; release record marked `failed` in cluster secret storage.

## 6. Diagnostic Procedure
1. Run `helm lint` and dry-run template rendering locally.
2. Check release secret state in the target namespace.
3. Inspect pre-install hook pod logs if hooks are configured.

## 7. Read-Only Investigation Commands
```bash
# 1. Test chart rendering locally with dry-run and debug
helm install <release-name> <chart-path> -n <namespace> --dry-run --debug -f values.yaml

# 2. Check failed release status in namespace
helm status <release-name> -n <namespace>

# 3. Check for existing conflicting resources
kubectl get all,configmap,secret -n <namespace> -l app.kubernetes.io/instance=<release-name>

# 4. Check hook pods that executed during install
kubectl get pods -n <namespace> -l helm.sh/hook
```

## 8. Expected Outputs
- `values don't meet the specifications of the schema`: Pinpoints exact field in `values.yaml` failing validation.
- `rendered manifests contain a resource that already exists`: Conflicting resource name and namespace specified.

## 9. Decision Tree
```
                         [Helm Install Failure]
                                    |
                           Identify Error Message
                                    |
         +--------------------------+--------------------------+
         |                                                     |
  [Schema Validation Error]                             [Resource Already Exists]
         |                                                     |
  Inspect values.schema.json:                           Check resource ownership:
  - Fix type mismatch or missing key                    - Adopt resource with Helm annotations
                                                        - Or delete unmanaged legacy resource
```

## 10. Remediation Steps

### Action A: Fix Value Type Mismatch (Safe)
Correct the value in your custom `values.yaml` and re-run installation:
```bash
helm install <release-name> <chart-path> -n <namespace> -f values.yaml
```

### Action B: Adopt Existing Pre-Created Resource into Helm (Safe)
If a ConfigMap or Secret was manually created, add Helm ownership annotations so Helm can manage it:
```bash
kubectl annotate <resource-type> <resource-name> -n <namespace>   meta.helm.sh/release-name="<release-name>"   meta.helm.sh/release-namespace="<namespace>" --overwrite

kubectl label <resource-type> <resource-name> -n <namespace>   app.kubernetes.io/managed-by="Helm" --overwrite
```

### Action C: Clean Up Failed Release State (Safe)
```bash
# Uninstall the failed release cleanly before retrying
helm uninstall <release-name> -n <namespace>
```

## 11. Verification After Remediation
1. Verify release status reports `deployed`:
   `helm list -n <namespace>`
2. Verify all underlying pods and services are running:
   `kubectl get all -n <namespace> -l app.kubernetes.io/instance=<release-name>`

## 12. Rollback Procedure
If installation fails midway, run `helm uninstall <release-name> -n <namespace>` to clean up partial artifacts.

## 13. Escalation Criteria
- If pre-install database migration fails due to corrupted data, escalate to Database SRE.

## 14. Prevention Recommendations
1. Run `helm lint` and `helm template --dry-run` in CI/CD pipeline before running in production.
2. Maintain strict `values.schema.json` with descriptive error messages.

## 15. Related Documentation References
- Helm Using Guide: `https://helm.sh/docs/intro/using_helm/`
- Core Knowledge Base: `data/corpus/helm/operations/helm-install.md`
