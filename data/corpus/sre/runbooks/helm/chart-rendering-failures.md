---
title: "Runbook: Helm Template Rendering Errors and Nil Pointer Panics"
incident_id: "RB-HLM-005"
severity: "P2"
category: "runbooks/helm"
component: "helm-template"
document_type: "runbook"
doc_type: "runbook"
source_type: "project-authored-sre"
applicable_versions: "Helm 3.x, Helm 4.x"
authoring_date: "2026-11-08"
spdx_license: "Apache-2.0"
attribution: "Enterprise SRE Copilot Authors"
content_hash: "fc9843c6d6efd64b5e16eaaa270668d64d242c09f92d06773ebdf3c046388e5a"
related_upstream_urls:
  - "https://helm.sh/docs/chart_template_guide/"
---

# Runbook: Helm Template Rendering Errors and Nil Pointer Panics

## 1. Incident Identifier
- **ID:** RB-HLM-005
- **Severity:** P2 (High) — Helm cannot render chart manifests due to Go template syntax errors or nil pointer evaluation.

## 2. Severity Classification
- **P1:** Production release pipeline blocked by template rendering panic.
- **P2:** Pull request / staging chart rendering failure.

## 3. Symptoms
- `helm template` or `helm upgrade` fails with: `nil pointer evaluating interface {}.fieldName` or `function "xyz" not defined`.
- Error: `parse error at (templates/deployment.yaml:45): unexpected bad character`.

## 4. Common Causes
1. **Missing Parent Key in Values:** Template references `{{ .Values.database.credentials.password }}` but `credentials` block was omitted from custom `values.yaml`.
2. **Indentation / Formatting Error:** `nindent` function applied with incorrect indentation level, causing invalid YAML parsing.
3. **Type Mismatch in Template Helpers:** Passing string to a function expecting a dictionary or list.
4. **Reserved Keyword Conflict:** Using unquoted keywords in YAML mappings.

## 5. Impact Assessment
- Helm halts before any cluster mutation; no cluster downtime, but deployments blocked.

## 6. Diagnostic Procedure
1. Run `helm template` locally to reproduce the exact line number of the failure.
2. Inspect the offending template line in `templates/`.
3. Check `values.yaml` for missing nested keys.

## 7. Read-Only Investigation Commands
```bash
# 1. Render template locally with debug output to identify failing file & line
helm template <release-name> <chart-path> -f values.yaml --debug

# 2. Check chart linting errors
helm lint <chart-path> -f values.yaml
```

## 8. Expected Outputs
- `at <.Values.ingress.tls.secretName>: nil pointer evaluating interface {}.secretName`: `ingress.tls` was not defined.

## 9. Decision Tree
```
                         [Template Rendering Error]
                                     |
                           Is it a Nil Pointer?
                                     |
         +---------------------------+---------------------------+
         |                                                       |
   [Nil Pointer Error]                                     [Syntax / Indent Error]
         |                                                       |
   Check values.yaml structure;                            Check template line number;
   use default or with block in template                   fix nindent or YAML quotes
```

## 10. Remediation Steps

### Action A: Add Missing Key to Custom Values (Safe)
```yaml
# Add missing block to values.yaml:
database:
  credentials:
    password: "secretPassword"
```

### Action B: Use Defensive Template Navigation in Chart (Safe)
```yaml
# Use default or with block to guard against nil pointers:
{{- with .Values.database }}
password: {{ .credentials.password | default "none" | quote }}
{{- end }}
```

## 11. Verification After Remediation
1. Verify `helm template` outputs valid, complete Kubernetes YAML without errors.
2. Verify `helm lint` returns `0 chart(s) failed`.

## 12. Rollback Procedure
Revert git commit in chart repository.

## 13. Escalation Criteria
- Escalate to Chart Author if third-party upstream chart has a release bug.

## 14. Prevention Recommendations
1. Enforce defensive Go templating practices using `with`, `default`, and `dig` functions.
2. Run automated `helm lint` and `helm template` in CI on all PRs.

## 15. Related Documentation References
- Helm Template Guide: `https://helm.sh/docs/chart_template_guide/`
