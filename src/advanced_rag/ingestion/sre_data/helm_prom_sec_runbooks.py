"""Helm, Prometheus, and Security Operational Runbooks.

Covers:
- Helm: Chart installation failures, Upgrade failures, Pending release state,
  Rollback failures, Chart rendering errors, Dependency problems
- Prometheus: Targets down, Missing metrics, Scrape timeouts, High cardinality,
  PromQL performance, Alertmanager notification failures
- Security: RBAC authorization failures, ServiceAccount token errors,
  Pod Security Admission (PSA/PSS) rejections, Secret injection failures,
  Certificate and TLS failures
"""

from __future__ import annotations

HELM_RUNBOOKS: list[dict[str, any]] = [
    {
        "category": "runbooks/helm",
        "name": "chart-installation-failures.md",
        "title": "Runbook: Helm Chart Installation Failures and Schema Rejections",
        "incident_id": "RB-HLM-001",
        "severity": "P2",
        "component": "helm-install",
        "applicable_versions": "Helm 3.x, Helm 4.x",
        "related_upstream_urls": [
            "https://helm.sh/docs/intro/using_helm/",
            "https://helm.sh/docs/chart_best_practices/schema/",
        ],
        "content": """# Runbook: Helm Chart Installation Failures and Schema Rejections

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
kubectl annotate <resource-type> <resource-name> -n <namespace> \
  meta.helm.sh/release-name="<release-name>" \
  meta.helm.sh/release-namespace="<namespace>" --overwrite

kubectl label <resource-type> <resource-name> -n <namespace> \
  app.kubernetes.io/managed-by="Helm" --overwrite
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
""",
    },
    {
        "category": "runbooks/helm",
        "name": "upgrade-failures.md",
        "title": "Runbook: Helm Upgrade Failures and Resource Conflicts",
        "incident_id": "RB-HLM-002",
        "severity": "P1",
        "component": "helm-upgrade",
        "applicable_versions": "Helm 3.x, Helm 4.x",
        "related_upstream_urls": [
            "https://helm.sh/docs/helm/helm_upgrade/",
        ],
        "content": """# Runbook: Helm Upgrade Failures and Resource Conflicts

## 1. Incident Identifier
- **ID:** RB-HLM-002
- **Severity:** P1 (Critical) / P2 (High) — Helm upgrade failed, leaving release in `failed` state or blocking service updates.

## 2. Severity Classification
- **P1:** Helm upgrade failed during production release, leaving partial resources or broken deployment.
- **P2:** Upgrade aborted cleanly before mutating running pods.
- **P3:** Staging release upgrade failure.

## 3. Symptoms
- `helm upgrade` returns error: `UPGRADE FAILED: cannot patch ... with kind Deployment: field is immutable`.
- Release status in `helm list` shows `STATUS: failed`.
- Error: `upgrade failed: timed out waiting for the condition`.

## 4. Common Causes
1. **Immutable Field Modification:** Attempting to modify immutable fields in Kubernetes objects (e.g. `spec.clusterIP` in Service or `spec.selector` in Deployment).
2. **Timeout on Readiness:** New pods failed readiness checks, causing `--wait --timeout` threshold to lapse.
3. **Pre-Upgrade Hook Failure:** Hook job failed, aborting the upgrade transaction before resources applied.
4. **CRD Schema Version Mismatch:** Upgraded chart expects updated CRD schemas that were not installed.

## 5. Impact Assessment
- Release state marked `failed`; subsequent upgrades blocked until rolled back or resolved.

## 6. Diagnostic Procedure
1. Check release revision history and status.
2. Identify which resource failed to apply.
3. Inspect pod logs of the new revision.

## 7. Read-Only Investigation Commands
```bash
# 1. Check release history and status of all revisions
helm history <release-name> -n <namespace>

# 2. Check detailed status of the failed revision
helm status <release-name> -n <namespace>

# 3. Check Kubernetes events for rejection reasons
kubectl get events -n <namespace> --sort-by=.lastTimestamp | grep -i failed

# 4. Dry-run the upgrade with debug output to pinpoint schema rejection
helm upgrade <release-name> <chart-path> -n <namespace> -f values.yaml --dry-run --debug
```

## 8. Expected Outputs
- `The Deployment "xyz" is invalid: spec.selector: Invalid value: field is immutable`: Selector change requires recreate.
- `timed out waiting for the condition`: Pods under new revision failed to reach ready state.

## 9. Decision Tree
```
                         [Helm Upgrade Failed]
                                   |
                       Is Running Service Impacted?
                                   |
         +-------------------------+-------------------------+
         |                                                   |
   [Yes: User Traffic Impacted]                       [No: Old Revision Serving]
         |                                                   |
   Execute Immediate Rollback:                         Investigate & Fix Values:
   helm rollback <release> <last-good-rev>             - Check immutable fields
                                                       - Fix readiness probes
```

## 10. Remediation Steps

### Action A: Immediate Rollback to Previous Healthy Revision (Safe / Recommended)
```bash
# 1. Identify previous deployed revision number (e.g., rev 4)
helm history <release-name> -n <namespace>

# 2. Rollback immediately
helm rollback <release-name> <healthy-revision-number> -n <namespace>
```

### Action B: Fix Immutable Selector in Chart Manifest (Requires Controlled Deployment)
If a Deployment selector must be changed, the existing Deployment must be deleted without cascading to pods, or replaced cleanly:
```bash
# If selector must change, delete deployment retaining pods:
kubectl delete deployment <deployment-name> -n <namespace> --cascade=orphan
# Immediately apply the upgraded chart:
helm upgrade <release-name> <chart-path> -n <namespace> -f values.yaml
```

## 11. Verification After Remediation
1. Verify `helm status <release-name> -n <namespace>` reports `STATUS: deployed`.
2. Confirm healthy revision is active and all pods are `Running`.

## 12. Rollback Procedure
Always rollback using `helm rollback <release-name> <rev> -n <namespace>`.

## 13. Escalation Criteria
- If upgrade failed midway through a stateful database schema migration, escalate to Database On-Call before executing rollback.

## 14. Prevention Recommendations
1. Never modify `spec.selector` on existing Deployments or StatefulSets.
2. Always execute `helm diff upgrade` in CI/CD pipeline before applying in production.

## 15. Related Documentation References
- Helm Upgrade: `https://helm.sh/docs/helm/helm_upgrade/`
- Core Knowledge Base: `data/corpus/helm/operations/helm-upgrade.md`
""",
    },
    {
        "category": "runbooks/helm",
        "name": "pending-release-state.md",
        "title": "Runbook: Helm Release Stuck in pending-install or pending-upgrade",
        "incident_id": "RB-HLM-003",
        "severity": "P2",
        "component": "helm-storage",
        "applicable_versions": "Helm 3.x, Helm 4.x",
        "related_upstream_urls": [
            "https://helm.sh/docs/topics/advanced/",
        ],
        "content": """# Runbook: Helm Release Stuck in pending-install or pending-upgrade

## 1. Incident Identifier
- **ID:** RB-HLM-003
- **Severity:** P2 (High) — Helm release locked in `pending-install`, `pending-upgrade`, or `pending-rollback` state; all future operations blocked.

## 2. Severity Classification
- **P1:** Automated CI/CD pipeline blocked on production release due to locked release secret.
- **P2:** Manual helm upgrade blocked by stale lock.

## 3. Symptoms
- `helm upgrade` or `helm rollback` returns: `Error: UPGRADE FAILED: another operation (install/upgrade/rollback) is in progress`.
- `helm list` shows `STATUS: pending-upgrade` or `pending-install`.

## 4. Common Causes
1. **CI/CD Job Abruptly Terminated:** GitLab runner / GitHub Actions runner killed or timed out while Helm was running `--wait`.
2. **Network Timeout During Helm CLI Execution:** Client dropped connection to API server while lock secret was held.
3. **Hook Job Hung Indefinitely:** Helm waiting for a pre-upgrade hook that never exited.

## 5. Impact Assessment
- Helm cannot perform any operations on the release until the lock is cleared.

## 6. Diagnostic Procedure
1. Verify if any active Helm CLI process or CI runner is still genuinely running.
2. Inspect the latest Helm release secret in the namespace.

## 7. Read-Only Investigation Commands
```bash
# 1. Check release status and active revision
helm list -n <namespace> --filter <release-name>

# 2. List all release secrets in the namespace
kubectl get secrets -n <namespace> -l owner=helm,name=<release-name> --sort-by=.metadata.creationTimestamp

# 3. Inspect the status recorded inside the latest release secret
LATEST_SECRET=$(kubectl get secrets -n <namespace> -l owner=helm,name=<release-name> --sort-by=.metadata.creationTimestamp -o jsonpath='{.items[-1].metadata.name}')
echo "Latest secret: $LATEST_SECRET"
```

## 8. Expected Outputs
- `STATUS: pending-upgrade`: Release locked in in-flight status.
- Latest secret named e.g. `sh.helm.release.v1.<release-name>.v5`.

## 9. Decision Tree
```
                         [Release Stuck in Pending]
                                     |
                         Is CI Job Still Running?
                                     |
         +---------------------------+---------------------------+
         |                                                       |
   [Yes: Job Active]                                       [No: Job Aborted]
         |                                                       |
   Allow job to complete or                                Safely delete or patch
   reach --timeout limit                                   stuck pending release secret
```

## 10. Remediation Steps

### Action A: Delete Stuck Pending Secret (Safe / Standard Recovery)
```bash
# 1. Confirm no active deployment process is running
# 2. Identify the stuck pending release secret (the highest revision)
STUCK_SECRET=$(kubectl get secrets -n <namespace> -l owner=helm,name=<release-name> --sort-by=.metadata.creationTimestamp -o jsonpath='{.items[-1].metadata.name}')

# 3. Delete the stuck secret
kubectl delete secret $STUCK_SECRET -n <namespace>

# 4. Helm will fall back to the previous deployed revision
helm status <release-name> -n <namespace>
```

### Action B: Patch Stuck Secret to 'failed' (Alternative Recovery)
If deleting the secret is disallowed by policy, patch its status from `pending-upgrade` to `failed` using helm rollback or `helm-rollback` plugin.

## 11. Verification After Remediation
1. Verify `helm list -n <namespace>` reports `STATUS: deployed` (or `failed` allowing upgrades).
2. Execute dry-run upgrade to verify lock is cleared:
   `helm upgrade <release-name> <chart-path> -n <namespace> --dry-run`

## 12. Rollback Procedure
If the wrong secret was deleted, restore from cluster backup.

## 13. Escalation Criteria
- If release secrets are permanently corrupted, escalate to Platform SRE to rebuild release state.

## 14. Prevention Recommendations
1. Always specify reasonable timeouts in CI/CD (e.g. `helm upgrade --timeout 5m0s`).
2. Implement graceful termination handling in CI/CD runners to avoid hard SIGKILL.

## 15. Related Documentation References
- Helm Advanced Architecture: `https://helm.sh/docs/topics/advanced/`
""",
    },
    {
        "category": "runbooks/helm",
        "name": "rollback-failures.md",
        "title": "Runbook: Helm Rollback Failures and Hook Failures",
        "incident_id": "RB-HLM-004",
        "severity": "P1",
        "component": "helm-rollback",
        "applicable_versions": "Helm 3.x, Helm 4.x",
        "related_upstream_urls": [
            "https://helm.sh/docs/helm/helm_rollback/",
        ],
        "content": """# Runbook: Helm Rollback Failures and Hook Failures

## 1. Incident Identifier
- **ID:** RB-HLM-004
- **Severity:** P1 (Critical) — Emergency `helm rollback` command failed or timed out during incident mitigation.

## 2. Severity Classification
- **P1:** Outage in progress; emergency rollback fails to restore service.
- **P2:** Rollback blocked by pre-rollback hook failure.

## 3. Symptoms
- `helm rollback <name> <rev>` fails with: `ROLLBACK FAILED: release: already exists` or `timed out waiting for condition`.
- Release status transitions to `failed`.
- Pods continue running failing container image.

## 4. Common Causes
1. **Pre-Rollback Hook Failure:** Database migration rollback job failed or timed out.
2. **Missing Secrets/ConfigMaps in Target Revision:** Target historical revision references a Secret that was manually deleted from the cluster.
3. **Node Capacity Starvation:** Cannot schedule pods from previous revision due to resource exhaustion.

## 5. Impact Assessment
- Incident mitigation blocked; prolonged customer-facing outage.

## 6. Diagnostic Procedure
1. Check hook job logs if rollback hooks are configured.
2. Check if resources referenced in target revision exist in the cluster.
3. Manually inspect target revision manifest.

## 7. Read-Only Investigation Commands
```bash
# 1. Retrieve the exact manifest of the revision being rolled back to
helm get manifest <release-name> --revision=<target-rev> -n <namespace>

# 2. Check hook jobs in namespace
kubectl get jobs -n <namespace> -l helm.sh/hook

# 3. Check pods that failed during rollback
kubectl get pods -n <namespace> --sort-by=.metadata.creationTimestamp
```

## 8. Expected Outputs
- `hook failed`: Hook job failed to execute cleanly.
- `Secret "<name>" not found`: Missing dependency in target revision.

## 9. Decision Tree
```
                         [Helm Rollback Failed]
                                    |
                           Is Hook Blocking?
                                    |
         +--------------------------+--------------------------+
         |                                                     |
    [Yes: Hook Failed]                                  [No: Pods Failing]
         |                                                     |
  Bypass hooks in emergency:                            Manually patch deployment to
  helm rollback --no-hooks                              last known healthy container image
```

## 10. Remediation Steps

### Action A: Rollback with Hooks Disabled (Emergency Mitigation — Requires Lead Approval)
```bash
# In an emergency, bypass failing hooks to restore serving workload
helm rollback <release-name> <target-rev> -n <namespace> --no-hooks --timeout 5m
```

### Action B: Direct Manual Patch of Deployment Image (Immediate Triage)
```bash
# Bypass Helm temporarily to restore customer service immediately
kubectl set image deployment/<deployment-name> <container>=<known-good-image> -n <namespace>
```

## 11. Verification After Remediation
1. Verify user-facing service is restored and healthy.
2. Reconcile Helm release state once service stability is confirmed.

## 12. Rollback Procedure
N/A — This is the rollback remediation runbook.

## 13. Escalation Criteria
- Escalate immediately to Incident Commander and Core Development Team.

## 14. Prevention Recommendations
1. Avoid non-idempotent rollback hooks.
2. Never delete Secrets or ConfigMaps manually outside of Helm release lifecycle.

## 15. Related Documentation References
- Helm Rollback: `https://helm.sh/docs/helm/helm_rollback/`
""",
    },
    {
        "category": "runbooks/helm",
        "name": "chart-rendering-failures.md",
        "title": "Runbook: Helm Template Rendering Errors and Nil Pointer Panics",
        "incident_id": "RB-HLM-005",
        "severity": "P2",
        "component": "helm-template",
        "applicable_versions": "Helm 3.x, Helm 4.x",
        "related_upstream_urls": [
            "https://helm.sh/docs/chart_template_guide/",
        ],
        "content": """# Runbook: Helm Template Rendering Errors and Nil Pointer Panics

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
""",
    },
    {
        "category": "runbooks/helm",
        "name": "dependency-problems.md",
        "title": "Runbook: Helm Dependency Resolution and Repository Auth Failures",
        "incident_id": "RB-HLM-006",
        "severity": "P2",
        "component": "helm-dependency",
        "applicable_versions": "Helm 3.x, Helm 4.x",
        "related_upstream_urls": [
            "https://helm.sh/docs/helm/helm_dependency/",
        ],
        "content": """# Runbook: Helm Dependency Resolution and Repository Auth Failures

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
""",
    },
]

PROMETHEUS_RUNBOOKS: list[dict[str, any]] = [
    {
        "category": "runbooks/prometheus",
        "name": "targets-down.md",
        "title": "Runbook: Prometheus Scrape Targets Down and Up=0 Alerts",
        "incident_id": "RB-PRM-001",
        "severity": "P1",
        "component": "prometheus-scrape",
        "applicable_versions": "Prometheus 2.x, Prometheus 3.x",
        "related_upstream_urls": [
            "https://prometheus.io/docs/prometheus/latest/configuration/configuration/#scrape_config",
        ],
        "content": """# Runbook: Prometheus Scrape Targets Down and Up=0 Alerts

## 1. Incident Identifier
- **ID:** RB-PRM-001
- **Severity:** P1 (Critical) / P2 (High) — Prometheus scrape target unreachable (`up == 0`), causing monitoring blind spots and missed alerts.

## 2. Severity Classification
- **P1:** Core infrastructure targets (Node Exporter, CoreDNS, Kubelet) down across multiple nodes.
- **P2:** Single application pod instance scrape failing.
- **P3:** Intermittent scrape drops on ephemeral batch job.

## 3. Symptoms
- Prometheus alert `TargetDown` or `KubeNodeExporterDown` firing.
- PromQL query `up == 0` returns active failing endpoints.
- Prometheus `/targets` web UI shows target state `DOWN` with error message.

## 4. Common Causes
1. **Target Pod Crashed or Unhealthy:** Application container is in `CrashLoopBackOff` or terminated.
2. **NetworkPolicy Blocking Scraper:** Ingress NetworkPolicy on target pod does not permit traffic from Prometheus pods.
3. **Port / Path / Scheme Mismatch:** Scrape config targets port 8080 instead of 9090, or path `/metrics` instead of `/actuator/prometheus`.
4. **TLS / Certificate Verification Rejection:** Scrape configured with `scheme: HTTPS` but target uses self-signed certificate without `insecure_skip_verify`.
5. **Kubelet / Service Discovery Stale:** Prometheus discovery retains dead pod IP addresses.

## 5. Impact Assessment
- Loss of observability, missing rate metrics, false positive or silenced alerts.

## 6. Diagnostic Procedure
1. Execute `up == 0` in Prometheus query UI to identify failing targets.
2. Check error message recorded in Prometheus Targets page.
3. Test direct scrape connection from Prometheus pod using `curl`.

## 7. Read-Only Investigation Commands
```bash
# 1. PromQL: List all targets currently down
# up == 0

# 2. Check if target pod is actually alive and ready
kubectl get pod <target-pod> -n <namespace> -o wide

# 3. Test direct scrape from Prometheus pod
kubectl exec -it <prometheus-pod> -n monitoring -- curl -m 5 -Iv http://<target-pod-ip>:<metrics-port>/metrics

# 4. Check target ServiceMonitor / PodMonitor configuration
kubectl get servicemonitor <monitor-name> -n <namespace> -o yaml
```

## 8. Expected Outputs
- `context deadline exceeded`: Scrape timeout exceeded (target slow or blocked by network).
- `connection refused`: Target process not listening on expected port.
- `x509: certificate signed by unknown authority`: TLS certificate verification failure.

## 9. Decision Tree
```
                         [Prometheus Target Down: up == 0]
                                         |
                            Test Direct HTTP Scrape
                                         |
         +-------------------------------+-------------------------------+
         |                                                               |
  [Connection Refused]                                            [Connection Timeout]
         |                                                               |
  Check application process:                                      Check NetworkPolicy & Firewall:
  - Is metrics port listening?                                    - Allow monitoring namespace ingress
  - Fix port in ServiceMonitor                                    - Increase scrape_timeout
```

## 10. Remediation Steps

### Action A: Correct ServiceMonitor Port or Path (Safe)
```yaml
# In ServiceMonitor:
endpoints:
- port: http-metrics # Must match port name on Service
  path: /metrics
  interval: 30s
  scrapeTimeout: 10s
```

### Action B: Add Monitoring Allow Rule to Target NetworkPolicy (Safe)
```yaml
ingress:
- from:
  - namespaceSelector:
      matchLabels:
        kubernetes.io/metadata.name: monitoring
  ports:
  - protocol: TCP
    port: 8080
```

## 11. Verification After Remediation
1. Verify PromQL `up{job="<target-job>"}` returns `1`.
2. Confirm targets page in Prometheus UI displays green `UP` badge.

## 12. Rollback Procedure
Revert ServiceMonitor manifest if modifications cause syntax errors.

## 13. Escalation Criteria
- If Kubelet `/metrics` endpoint is failing across all nodes, escalate as P1 Kubelet Incident.

## 14. Prevention Recommendations
1. Establish standard metrics port naming (e.g. `http-metrics`) across all Helm charts.
2. Include monitoring namespace ingress permits in base NetworkPolicy templates.

## 15. Related Documentation References
- Prometheus Scrape Config: `https://prometheus.io/docs/prometheus/latest/configuration/configuration/#scrape_config`
""",
    },
    {
        "category": "runbooks/prometheus",
        "name": "missing-metrics.md",
        "title": "Runbook: Missing Metrics and Exposition Format Parsing Failures",
        "incident_id": "RB-PRM-002",
        "severity": "P2",
        "component": "prometheus-exposition",
        "applicable_versions": "Prometheus 2.x, Prometheus 3.x",
        "related_upstream_urls": [
            "https://prometheus.io/docs/instrumenting/exposition_formats/",
        ],
        "content": """# Runbook: Missing Metrics and Exposition Format Parsing Failures

## 1. Incident Identifier
- **ID:** RB-PRM-002
- **Severity:** P2 (High) — Expected metric missing from Prometheus TSDB despite target reporting `up == 1`.

## 2. Severity Classification
- **P1:** SLA/SLO metric missing, disabling customer billing or error budget tracking.
- **P2:** Application business metric missing.

## 3. Symptoms
- PromQL queries for metric (e.g. `http_requests_total`) return empty result.
- Target is reported as `UP` in Prometheus UI.
- Prometheus logs report: `text format parsing error in line X: expected float value`.

## 4. Common Causes
1. **Metric Relabeling Dropped Metric:** `metric_relabel_configs` dropped metric due to `action: drop` or `keep` regex.
2. **Exposition Format Syntax Error:** Target emits non-standard exposition syntax (invalid characters, unquoted label values).
3. **Metric Renamed in New Version:** Upstream software changed metric name (e.g. `container_cpu_usage_seconds_total`).
4. **Scrape Limit Exceeded:** `sample_limit` exceeded on scrape configuration, causing partial metric drops.

## 5. Impact Assessment
- Dashboards display `No Data`; alerting rules fail to evaluate.

## 6. Diagnostic Procedure
1. Inspect raw `/metrics` output directly from target pod.
2. Check Prometheus scrape configuration for `metric_relabel_configs`.
3. Check Prometheus server logs for parsing errors.

## 7. Read-Only Investigation Commands
```bash
# 1. Scrape raw endpoint directly and grep for target metric
kubectl exec -it <prometheus-pod> -n monitoring -- curl -s http://<target-ip>:8080/metrics | grep "<metric-name>"

# 2. Check Prometheus server logs for parse or sample limit errors
kubectl logs -n monitoring -l app=prometheus --tail=200 | grep -E "(parse error|sample limit)"

# 3. Inspect ServiceMonitor metric_relabel_configs
kubectl get servicemonitor <name> -n <namespace> -o jsonpath='{.spec.endpoints[*].metricRelabelings}' | jq .
```

## 8. Expected Outputs
- Metric present in curl output but absent in PromQL: Relabeling config drop.
- `sample limit exceeded`: Scrape job produces more metrics than allowed.

## 9. Decision Tree
```
                         [Metric Missing in PromQL]
                                     |
                          Does curl /metrics show it?
                                     |
         +---------------------------+---------------------------+
         |                                                       |
   [Yes: Metric Exists in App]                             [No: App Not Emitting]
         |                                                       |
   Check Prometheus:                                       Check Application:
   - Check metric_relabel_configs                          - Is feature flag enabled?
   - Check sample_limit                                    - Check client library config
```

## 10. Remediation Steps

### Action A: Remove Restrictive Metric Relabeling Filter (Safe)
```yaml
# Fix metric_relabel_configs in ServiceMonitor:
metricRelabelings:
- action: keep
  sourceLabels: [__name__]
  regex: "(http_requests_total|process_cpu_seconds_total)" # Ensure target metric included
```

### Action B: Increase Sample Limit (Safe)
```yaml
sampleLimit: 100000
```

## 11. Verification After Remediation
1. Query Prometheus TSDB: `<metric_name>{job="<target-job>"}`.
2. Confirm metric values appear on Grafana dashboards.

## 12. Rollback Procedure
Revert ServiceMonitor manifest if relabeling change causes metric explosion.

## 13. Escalation Criteria
- If application client library generates malformed Prometheus text format, escalate to Software Engineering.

## 14. Prevention Recommendations
1. Use official Prometheus client libraries (client_golang, client_python, micrometer) rather than manual string concatenation.

## 15. Related Documentation References
- Exposition Formats: `https://prometheus.io/docs/instrumenting/exposition_formats/`
""",
    },
    {
        "category": "runbooks/prometheus",
        "name": "scrape-failures.md",
        "title": "Runbook: Scrape Timeouts and Exporter Rate-Limiting",
        "incident_id": "RB-PRM-003",
        "severity": "P2",
        "component": "prometheus-scrape",
        "applicable_versions": "Prometheus 2.x, Prometheus 3.x",
        "related_upstream_urls": [
            "https://prometheus.io/docs/prometheus/latest/configuration/configuration/",
        ],
        "content": """# Runbook: Scrape Timeouts and Exporter Rate-Limiting

## 1. Incident Identifier
- **ID:** RB-PRM-003
- **Severity:** P2 (High) — Prometheus scrape jobs timing out before completing, causing intermittent gaps in time series data.

## 2. Severity Classification
- **P1:** Scrapes timing out across all critical cluster infrastructure exporters.
- **P2:** Single application or exporter scrape timing out.
- **P3:** Occasional slow scrape on non-critical metrics.

## 3. Symptoms
- Prometheus target status oscillates between `UP` and `DOWN` with error `context deadline exceeded`.
- Scrape duration metric `scrape_duration_seconds` consistently exceeds `scrape_timeout` (default 10s).
- Prometheus alert `PrometheusScrapeTimeout` firing.

## 4. Common Causes
1. **Target Exporter Slow Execution:** Exporter (e.g. database exporter, SNMP exporter) executes expensive queries on every scrape.
2. **Gigantic Metric Payload:** Exporter generating >50MB of metrics on single HTTP request.
3. **Scrape Timeout Shorter Than Scrape Interval:** Configured `scrape_timeout: 10s` while application takes 12s to format metrics.

## 5. Impact Assessment
- Data gaps, counter resets, and inaccurate rate calculations.

## 6. Diagnostic Procedure
1. Measure raw HTTP scrape duration using `curl -w "%{time_total}\n"`.
2. Inspect Prometheus `scrape_duration_seconds` for the affected job.

## 7. Read-Only Investigation Commands
```bash
# 1. Measure total scrape execution time from Prometheus pod
kubectl exec -it <prometheus-pod> -n monitoring -- curl -s -w "Time: %{time_total}s Size: %{size_download} bytes\n" -o /dev/null http://<target-ip>:8080/metrics

# 2. PromQL: Top slowest scrape targets
# topk(10, scrape_duration_seconds)
```

## 8. Expected Outputs
- Total time exceeding scrapeTimeout: Timeout configuration too tight or exporter degraded.

## 9. Decision Tree
```
                         [Scrape Timeout Failure]
                                     |
                          Is Exporter Heavy or Slow?
                                     |
         +---------------------------+---------------------------+
         |                                                       |
   [Expensive DB Queries]                                  [Huge Payload (>20MB)]
         |                                                       |
   Tune exporter collection flags;                         Drop unneeded metric series
   increase scrape interval                                using metric_relabel_configs
```

## 10. Remediation Steps

### Action A: Increase Scrape Timeout (Safe)
```yaml
scrapeTimeout: 25s
interval: 30s
```

### Action B: Disable Heavy Collectors in Exporter (Safe)
Tune exporter flags (e.g. disable expensive table stats in mysql-exporter).

## 11. Verification After Remediation
1. Verify `scrape_duration_seconds < scrape_timeout`.
2. Confirm target remains continuously `UP`.

## 12. Rollback Procedure
Revert ServiceMonitor configuration to prior values.

## 13. Escalation Criteria
- If hardware exporter is blocked on physical device locks, escalate to Systems Infrastructure.

## 14. Prevention Recommendations
1. Separate fast metrics (HTTP rates, CPU) from slow batch metrics (database table scans) into distinct scrape jobs.

## 15. Related Documentation References
- Prometheus Scrape Config: `https://prometheus.io/docs/prometheus/latest/configuration/configuration/`
""",
    },
    {
        "category": "runbooks/prometheus",
        "name": "high-cardinality-problems.md",
        "title": "Runbook: High Cardinality Explosion and TSDB Churn",
        "incident_id": "RB-PRM-004",
        "severity": "P1",
        "component": "prometheus-tsdb",
        "applicable_versions": "Prometheus 2.x, Prometheus 3.x",
        "related_upstream_urls": [
            "https://prometheus.io/docs/practices/histograms/",
        ],
        "content": """# Runbook: High Cardinality Explosion and TSDB Churn

## 1. Incident Identifier
- **ID:** RB-PRM-004
- **Severity:** P1 (Critical) / P2 (High) — Unbounded label values (user IDs, URLs, UUIDs) causing exponential TSDB series explosion and Prometheus OOM.

## 2. Severity Classification
- **P1:** Prometheus crashing repeatedly with OOMKilled; monitoring offline.
- **P2:** Head series surging (>1,500,000 series); memory growing steadily.

## 3. Symptoms
- Prometheus memory usage surges dramatically; pods restart with `OOMKilled` (exit code 137).
- Prometheus TSDB head series metric `prometheus_tsdb_head_series` climbing steeply.
- Alert `PrometheusTSDBCardinalityExplosion` firing.

## 4. Common Causes
1. **Dynamic High-Cardinality Labels:** Metric contains user IDs, email addresses, order IDs, or unnormalized URLs (e.g. `/api/v1/user/123456`).
2. **Too Many Custom Metrics:** Application microservice exporting thousands of unique metric names dynamically.
3. **Microservice High Churn:** Pods scaling up and down every 10 seconds with unique ephemeral pod names.

## 5. Impact Assessment
- Memory starvation; complete crash of monitoring infrastructure.

## 6. Diagnostic Procedure
1. Run Prometheus TSDB status API to identify top cardinality labels and metric names.
2. Pinpoint the offending workload exporter.

## 7. Read-Only Investigation Commands
```bash
# 1. Query Prometheus TSDB Status API for top 10 label names with highest cardinality
kubectl exec -it <prometheus-pod> -n monitoring -- curl -s http://localhost:9090/api/v1/status/tsdb | jq .data.seriesCountByMetricName[:10]
kubectl exec -it <prometheus-pod> -n monitoring -- curl -s http://localhost:9090/api/v1/status/tsdb | jq .data.labelValueCountByLabelName[:10]

# 2. PromQL: Head series total count
# prometheus_tsdb_head_series
```

## 8. Expected Outputs
- `labelValueCountByLabelName`: Pinpoints exact label name with hundreds of thousands of distinct values.

## 9. Decision Tree
```
                         [Cardinality Explosion]
                                    |
                         Identify Offending Label
                                    |
         +--------------------------+--------------------------+
         |                                                     |
  [User ID / UUID in Label]                             [Unnormalized URL Path]
         |                                                     |
  Drop label immediately via:                           Normalize path in app code;
  metricRelabelings: action: labeldrop                  drop raw path label in scrape
```

## 10. Remediation Steps

### Action A: Drop High-Cardinality Label in Metric Relabeling (Safe / Immediate Mitigation)
```yaml
# Add metric_relabel_configs to strip offending label at scrape time:
metricRelabelings:
- action: labeldrop
  regex: "(user_id|order_id|uuid)"
```

### Action B: Normalize Path Labels in Application Code
Replace raw URL paths with route templates (e.g. `/users/:id` instead of `/users/987654`).

## 11. Verification After Remediation
1. Verify `prometheus_tsdb_head_series` stabilizes and stops climbing.
2. Confirm Prometheus memory working set drops to safe operating levels.

## 12. Rollback Procedure
Revert ServiceMonitor patch if required metrics were dropped accidentally.

## 13. Escalation Criteria
- If WAL replay cannot complete due to memory limits, expand Prometheus memory limit temporarily.

## 14. Prevention Recommendations
1. Establish automated CI linting for metric definitions blocking labels that contain unbounded identifiers.

## 15. Related Documentation References
- Histograms and Cardinality: `https://prometheus.io/docs/practices/histograms/`
""",
    },
    {
        "category": "runbooks/prometheus",
        "name": "promql-performance.md",
        "title": "Runbook: Slow PromQL Queries and Evaluation Timeouts",
        "incident_id": "RB-PRM-005",
        "severity": "P2",
        "component": "promql-engine",
        "applicable_versions": "Prometheus 2.x, Prometheus 3.x",
        "related_upstream_urls": [
            "https://prometheus.io/docs/prometheus/latest/querying/basics/",
        ],
        "content": """# Runbook: Slow PromQL Queries and Evaluation Timeouts

## 1. Incident Identifier
- **ID:** RB-PRM-005
- **Severity:** P2 (High) — Heavy PromQL queries timing out, degrading Grafana dashboards and rule evaluation loops.

## 2. Severity Classification
- **P1:** Rule evaluation loop falling behind by multiple intervals, silencing alerts.
- **P2:** Grafana operational dashboards timing out.
- **P3:** Ad-hoc analytics query slow.

## 3. Symptoms
- Grafana panels show: `504 Gateway Timeout` or `query processing would load too many samples`.
- Prometheus logs: `query execution timeout exceeded`.
- Alert `PrometheusRuleEvaluationSlow` firing.

## 4. Common Causes
1. **Unindexed / Broad Regex Matchers:** Query uses `{name=~".*"}` across millions of series.
2. **Excessive Range Vector Duration:** Requesting `[30d]` resolution over thousands of series simultaneously.
3. **Lack of Recording Rules:** Computing heavy multi-level aggregations on every dashboard refresh instead of precomputing.

## 5. Impact Assessment
- Dashboards fail to render during incidents; delayed alert triggers.

## 6. Diagnostic Procedure
1. Enable and inspect the Prometheus query log.
2. Identify slow queries with high sample load.
3. Convert high-frequency slow queries into recording rules.

## 7. Read-Only Investigation Commands
```bash
# 1. Inspect slow queries from Prometheus query log
kubectl exec -it <prometheus-pod> -n monitoring -- tail -n 50 /var/log/prometheus/query.log | jq .

# 2. PromQL: Rule evaluation duration
# prometheus_rule_group_last_duration_seconds
```

## 8. Expected Outputs
- `load_time_seconds > 5.0`: Indicates disk TSDB fetch bottleneck.

## 9. Decision Tree
```
                         [Slow PromQL Query]
                                  |
                        Is it a Dashboard or Rule?
                                  |
         +------------------------+------------------------+
         |                                                 |
  [Dashboard Query]                                 [Alerting Rule]
         |                                                 |
  Precompute via Recording Rule                     Increase evaluation_interval
  and replace query on dashboard                    or tighten metric label filter
```

## 10. Remediation Steps

### Action A: Enable Query Log to Identify Slow Queries (Safe)
```yaml
# In prometheus command-line flags:
--query.log-file=/var/log/prometheus/query.log
```

### Action B: Precompute with Recording Rules (Safe / Recommended)
```yaml
groups:
- name: precomputed-metrics
  rules:
  - record: job:http_requests:rate5m
    expr: sum(rate(http_requests_total[5m])) by (job, status)
```

## 11. Verification After Remediation
1. Verify Grafana dashboards load in <1.5s using recording rules.
2. Confirm `prometheus_engine_query_duration_seconds` 99th percentile drops below 1s.

## 12. Rollback Procedure
Revert Prometheus rule file additions.

## 13. Escalation Criteria
- If TSDB disk I/O latency is throttling all queries, escalate to Storage Admin.

## 14. Prevention Recommendations
1. Avoid bare regex matches without at least one exact label matcher.
2. Mandate recording rules for dashboards queried by more than 5 users.

## 15. Related Documentation References
- PromQL Querying Basics: `https://prometheus.io/docs/prometheus/latest/querying/basics/`
""",
    },
    {
        "category": "runbooks/prometheus",
        "name": "alertmanager-notification-failures.md",
        "title": "Runbook: Alertmanager Routing and Notification Dispatch Failures",
        "incident_id": "RB-PRM-006",
        "severity": "P1",
        "component": "alertmanager",
        "applicable_versions": "Alertmanager 0.25-0.34",
        "related_upstream_urls": [
            "https://prometheus.io/docs/alerting/latest/configuration/",
        ],
        "content": """# Runbook: Alertmanager Routing and Notification Dispatch Failures

## 1. Incident Identifier
- **ID:** RB-PRM-006
- **Severity:** P1 (Critical) — Alertmanager failing to deliver notification webhooks to PagerDuty, Slack, or email.

## 2. Severity Classification
- **P1:** PagerDuty / On-call notification pipeline down; zero pages reaching engineers.
- **P2:** Non-critical Slack channel notification failing.

## 3. Symptoms
- Prometheus alerts are `FIRING`, but on-call engineers receive zero pages or notifications.
- Alertmanager logs report: `Notify for 1 alerts failed: Post "https://events.pagerduty.com/...": dial tcp ...: i/o timeout`.
- Prometheus alert `AlertmanagerNotificationsFailed` firing.

## 4. Common Causes
1. **Invalid Webhook URL / Expired API Token:** PagerDuty integration key or Slack incoming webhook expired.
2. **Egress Firewall / Proxy Blocking:** Outbound traffic from Alertmanager pods blocked by NetworkPolicy or security group.
3. **Routing Tree Catch-All Missing:** Alert labels do not match any route, and default receiver is invalid.
4. **Template Syntax Error:** Custom notification template has invalid Go template syntax, crashing the notifier.

## 5. Impact Assessment
- Undetected production outages; delayed incident response.

## 6. Diagnostic Procedure
1. Check Alertmanager logs for notification dispatch failures.
2. Test webhook URL reachability from Alertmanager pod.
3. Validate Alertmanager configuration using `amtool`.

## 7. Read-Only Investigation Commands
```bash
# 1. Check Alertmanager logs for notification dispatch failures
kubectl logs -n monitoring -l app.kubernetes.io/name=alertmanager --tail=100 | grep -i "notify"

# 2. Test webhook URL reachability from Alertmanager pod
kubectl exec -it <alertmanager-pod> -n monitoring -- curl -m 5 -Iv https://events.pagerduty.com

# 3. Check Alertmanager configuration validation with amtool
kubectl exec -it <alertmanager-pod> -n monitoring -- amtool check-config /etc/alertmanager/alertmanager.yml
```

## 8. Expected Outputs
- `Notify for 1 alerts failed ... 401 Unauthorized`: API key revoked or expired.
- `syntax error in template`: Template rendering failure.

## 9. Decision Tree
```
                         [Alertmanager Dispatch Failure]
                                        |
                          Is it 401 Auth or Network Timeout?
                                        |
         +------------------------------+------------------------------+
         |                                                             |
   [401 Unauthorized]                                            [Network Timeout]
         |                                                             |
   Update integration token in Secret;                           Check NetworkPolicy & Egress;
   reload Alertmanager config                                    allow HTTPS 443 outbound
```

## 10. Remediation Steps

### Action A: Update Integration Key in Secret (Safe)
Update the integration secret with fresh valid token and trigger config reload:
```bash
kubectl exec -it <alertmanager-pod> -n monitoring -- curl -X POST http://localhost:9093/-/reload
```

## 11. Verification After Remediation
1. Trigger a synthetic test alert to verify end-to-end delivery:
   `amtool alert add alertname=TestAlert severity=warning instance=manual-test`
2. Confirm receipt on target notification channel.

## 12. Rollback Procedure
Revert Alertmanager configuration Secret to previous revision.

## 13. Escalation Criteria
- If third-party incident management provider (PagerDuty/OpsGenie) is having a global outage, activate backup SMS/Email notifier.

## 14. Prevention Recommendations
1. Establish automated dead-man's-snitch heartbeat alerts (e.g. `Watchdog` alert) to verify continuous notification health.

## 15. Related Documentation References
- Alertmanager Configuration: `https://prometheus.io/docs/alerting/latest/configuration/`
""",
    },
]

SECURITY_RUNBOOKS: list[dict[str, any]] = [
    {
        "category": "runbooks/security",
        "name": "rbac-authorization-failures.md",
        "title": "Runbook: RBAC Authorization and Permission Denied (403) Failures",
        "incident_id": "RB-SEC-001",
        "severity": "P2",
        "component": "rbac-authorizer",
        "applicable_versions": "Kubernetes 1.28-1.37",
        "related_upstream_urls": [
            "https://kubernetes.io/docs/reference/access-authn-authz/rbac/",
        ],
        "content": """# Runbook: RBAC Authorization and Permission Denied (403) Failures

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
""",
    },
    {
        "category": "runbooks/security",
        "name": "serviceaccount-problems.md",
        "title": "Runbook: ServiceAccount Token Mount and Auto-Mount Failures",
        "incident_id": "RB-SEC-002",
        "severity": "P2",
        "component": "serviceaccount",
        "applicable_versions": "Kubernetes 1.28-1.37",
        "related_upstream_urls": [
            "https://kubernetes.io/docs/tasks/configure-pod-container/configure-service-account/",
        ],
        "content": """# Runbook: ServiceAccount Token Mount and Auto-Mount Failures

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
""",
    },
    {
        "category": "runbooks/security",
        "name": "pod-security-admission-failures.md",
        "title": "Runbook: Pod Security Standards (PSA/PSS) Admission Rejections",
        "incident_id": "RB-SEC-003",
        "severity": "P2",
        "component": "pod-security-admission",
        "applicable_versions": "Kubernetes 1.28-1.37",
        "related_upstream_urls": [
            "https://kubernetes.io/docs/concepts/security/pod-security-standards/",
            "https://kubernetes.io/docs/tasks/configure-pod-container/enforce-standards-admission-controller/",
        ],
        "content": """# Runbook: Pod Security Standards (PSA/PSS) Admission Rejections

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
""",
    },
    {
        "category": "runbooks/security",
        "name": "secret-configuration-problems.md",
        "title": "Runbook: Secret Decoding and Environment Injection Failures",
        "incident_id": "RB-SEC-004",
        "severity": "P2",
        "component": "secrets",
        "applicable_versions": "Kubernetes 1.28-1.37",
        "related_upstream_urls": [
            "https://kubernetes.io/docs/concepts/configuration/secret/",
        ],
        "content": """# Runbook: Secret Decoding and Environment Injection Failures

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
kubectl create secret generic <secret-name> -n <namespace> \
  --from-literal=<key>=<value> --dry-run=client -o yaml | kubectl apply -f -
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
""",
    },
    {
        "category": "runbooks/security",
        "name": "certificate-tls-failures.md",
        "title": "Runbook: TLS Handshake Failures and Ingress Certificate Mismatches",
        "incident_id": "RB-SEC-005",
        "severity": "P1",
        "component": "ingress-tls",
        "applicable_versions": "Kubernetes 1.28-1.37",
        "related_upstream_urls": [
            "https://kubernetes.io/docs/concepts/services-networking/ingress/#tls",
        ],
        "content": """# Runbook: TLS Handshake Failures and Ingress Certificate Mismatches

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
kubectl create secret tls <secret-name> -n <namespace> \
  --cert=fullchain.pem --key=privkey.pem --dry-run=client -o yaml | kubectl apply -f -
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
""",
    },
]
