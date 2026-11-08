---
title: "Runbook: Helm Dependency Resolution and Repository Auth Failures"
incident_id: "RB-HLM-006"
severity: "P2"
category: "runbooks/helm"
component: "helm-dependency"
document_type: "runbook"
doc_type: "runbook"
source_type: "project-authored-sre"
applicable_versions: "Helm 3.x, Helm 4.x"
authoring_date: "2026-11-08"
spdx_license: "Apache-2.0"
attribution: "Enterprise SRE Copilot Authors"
content_hash: "d318d9f170dc4ac651272c60a1209bc332f744932e872afe3c9d61704b125737"
related_upstream_urls:
  - "https://helm.sh/docs/helm/helm_dependency/"
---

# Runbook: Helm Dependency Resolution and Repository Auth Failures

## 1. Incident Identifier
- **ID:** RB-HLM-006
- **Severity:** P2 (High) — Helm cannot resolve, download, or authenticate to subchart repositories listed in `Chart.yaml`.

## 2. Severity Classification
- **P1:** Automated deployment pipeline failing for core services relying on umbrella charts.
- **P2:** Local build or staging chart dependency resolution failure.

## 3. Symptoms
- `helm dependency build` or `helm dependency update` fails with: `repository not found: 401 Unauthorized` or `can't get a valid version for subchart`.
- Error: `Chart.lock is out of sync with Chart.yaml`.

## 4. Common Causes
1. **Expired Repository Token:** Helm repository credentials (OCI registry or HTTPS chart repo) expired.
2. **Outdated Chart.lock:** `Chart.yaml` version constraints were updated without regenerating `Chart.lock`.
3. **Repository URL Changed or Offline:** Upstream chart repository deprecated or DNS unreachable.
4. **OCI Registry Login Missing:** Chart uses `oci://` registry without prior `helm registry login`.

## 5. Impact Assessment
- Deployment cannot package or render; blocked release pipeline.

## 6. Diagnostic Procedure
1. Check repository URLs and credentials in `helm repo list`.
2. Verify subchart versions exist in target repository.
3. Compare `Chart.yaml` dependencies with `Chart.lock`.

## 7. Read-Only Investigation Commands
```bash
# 1. List configured Helm repositories
helm repo list

# 2. Check dependency status in chart directory
helm dependency list <chart-path>

# 3. Test access to target repository manually
helm search repo <repo-name>
```

## 8. Expected Outputs
- `401 Unauthorized`: Repository authentication failed.
- `Chart.lock is out of sync`: `Chart.lock` must be updated.

## 9. Decision Tree
```
                         [Dependency Build Error]
                                    |
                           Is it Auth or Version?
                                    |
         +--------------------------+--------------------------+
         |                                                     |
    [401 Auth Error]                                     [Lock Out of Sync]
         |                                                     |
    Update credentials:                                  Run helm dependency update:
    helm repo add --username ...                         re-locks valid versions
```

## 10. Remediation Steps

### Action A: Regenerate Chart.lock (Safe)
```bash
# Update dependencies and generate fresh lockfile
helm dependency update <chart-path>
```

### Action B: Refresh Repository Authentication (Safe)
```bash
# Re-authenticate to OCI or HTTPS repository
helm registry login <registry-url> --username <user> --password <token>
helm repo update
```

## 11. Verification After Remediation
1. Verify `helm dependency list <chart-path>` reports `STATUS: ok` for all dependencies.
2. Verify `helm template <chart-path>` renders subchart templates successfully.

## 12. Rollback Procedure
Revert `Chart.lock` from git version control.

## 13. Escalation Criteria
- If private corporate artifact registry (Harbor/Artifactory) is down, escalate to DevOps Infrastructure Team.

## 14. Prevention Recommendations
1. Commit `Chart.lock` into version control for reproducible builds.
2. Vendor subcharts using `helm dependency build` during CI build artifacts.

## 15. Related Documentation References
- Helm Dependencies: `https://helm.sh/docs/helm/helm_dependency/`
