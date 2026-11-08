---
title: "Runbook: Pod CrashLoopBackOff Triage and Resolution"
incident_id: "RB-K8S-001"
severity: "P2"
category: "runbooks/kubernetes"
component: "pod-lifecycle"
document_type: "runbook"
doc_type: "runbook"
source_type: "project-authored-sre"
applicable_versions: "Kubernetes 1.28-1.37"
authoring_date: "2026-11-08"
spdx_license: "Apache-2.0"
attribution: "Enterprise SRE Copilot Authors"
content_hash: "c602c95207952d14d99169fa658ae63a13da5943e9343c6d5cc098ceaea6471e"
related_upstream_urls:
  - "https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/"
  - "https://kubernetes.io/docs/tasks/debug/debug-application/determine-pod-failure-reason/"
---

# Runbook: Pod CrashLoopBackOff Triage and Resolution

## 1. Incident Identifier
- **ID:** RB-K8S-001
- **Severity:** P2 (High) — Workload pod continuously crashing and restarting with exponential backoff delay.

## 2. Severity Classification
- **P1:** If >50% of application replicas in a production service are in `CrashLoopBackOff`, degrading user traffic.
- **P2:** Single replica failure in multi-replica deployment, or background worker pod failure.
- **P3:** Staging or non-critical test namespace pod failure.

## 3. Symptoms
- `kubectl get pods` shows `STATUS: CrashLoopBackOff` with restart count steadily incrementing (e.g., 5, 12, 45).
- Pod status oscillates between `Running` for a few seconds and `CrashLoopBackOff`.
- Alert triggered: `KubePodCrashLooping` firing for target workload.

## 4. Common Causes
1. **Application Runtime Panic / Uncaught Exception:** Code failure on startup, missing dependency, or unhandled null pointer.
2. **Missing Configuration / Secrets:** Missing required environment variables, invalid config file path, or unparsed JSON/YAML.
3. **Database / Downstream Unreachability:** Application startup probe or database connection timeout aborting initialization.
4. **Incorrect Container Command / Entrypoint:** Misconfigured `command` or `args` in PodSpec pointing to a nonexistent binary.
5. **Permission / File Access Denial:** Container running as non-root user attempting to write to root-owned volume or directory.

## 5. Impact Assessment
- Potential reduction in service capacity, increased latency on remaining healthy replicas.
- Risk of cascading failures if remaining replicas take excess traffic.
- Stale connections and incomplete transaction rollbacks.

## 6. Diagnostic Procedure
Follow this sequence strictly:
1. Identify the exact failing container within the pod.
2. Inspect the exit code and termination reason.
3. Retrieve container logs from both the current instance and the previous crashed instance.
4. Inspect recent cluster events affecting the pod.

## 7. Read-Only Investigation Commands
Run these read-only commands in order:

```bash
# 1. Inspect pod status, container exit codes, and restart count
kubectl get pod <pod-name> -n <namespace> -o wide

# 2. Extract detailed container termination state and last state
kubectl get pod <pod-name> -n <namespace> -o jsonpath='{range .status.containerStatuses[*]}{.name}{"	ExitCode:"}{.lastState.terminated.exitCode}{"	Reason:"}{.lastState.terminated.reason}{"	Message:"}{.lastState.terminated.message}{"
"}{end}'

# 3. Retrieve logs from the crashed container instance before restart
kubectl logs <pod-name> -n <namespace> -c <container-name> --previous --tail=100

# 4. Stream current logs if the container runs briefly before exiting
kubectl logs <pod-name> -n <namespace> -c <container-name> --tail=50

# 5. Check Kubernetes events for the pod (OOM, probe failures, killed signals)
kubectl get events -n <namespace> --field-selector involvedObject.name=<pod-name> --sort-by='.lastTimestamp'
```

## 8. Expected Outputs
- **Exit Code 1:** General application error / uncaught runtime exception (inspect `--previous` logs for stack traces).
- **Exit Code 137:** Process received SIGKILL (`kill -9`), typically triggered by kernel OOM killer or liveness probe failure timeout.
- **Exit Code 143:** Process received SIGTERM (`kill -15`), terminated gracefully by Kubernetes or external orchestrator.
- **Exit Code 126 / 127:** Command invoked cannot execute or binary not found in container filesystem.
- **Exit Code 0:** Container completed and exited successfully, but `restartPolicy` is `Always`, causing Kubernetes to restart it.

## 9. Decision Tree
```
                         [Pod in CrashLoopBackOff]
                                     |
                    Inspect Exit Code & Previous Logs
                                     |
        +----------------------------+----------------------------+
        |                            |                            |
  [Exit Code 137]              [Exit Code 127]              [Exit Code 1]
        |                            |                            |
  Check OOMKilled:             Binary not found in           Application stack trace:
  - If OOM: raise memory       image; verify entrypoint      - ConfigMap/Secret missing?
  - If Liveness: check probe   and PodSpec command           - DB connection refused?
```

## 10. Remediation Steps
Choose the path indicated by the decision tree:

### Path A: Missing ConfigMap or Secret Key (Safe / Non-disruptive)
```bash
# 1. Check if referenced ConfigMaps and Secrets exist
kubectl get configmap,secret -n <namespace>

# 2. Update missing key or ConfigMap, then trigger rollout restart
kubectl rollout restart deployment/<deployment-name> -n <namespace>
```

### Path B: Transient Downstream Dependency (Safe)
```bash
# Verify downstream service accessibility from within cluster network
kubectl run netcheck --rm -it --image=curlimages/curl --restart=Never -n <namespace> -- curl -m 5 -Iv http://<service-name>:<port>/health
```

### Path C: Rollback to Last Known Healthy Revision (Disruptive — Requires L2 Approval)
```bash
# 1. Check revision history
kubectl rollout history deployment/<deployment-name> -n <namespace>

# 2. Rollback to previous working revision
kubectl rollout undo deployment/<deployment-name> -n <namespace>
```

## 11. Verification After Remediation
1. Verify pod reaches `Running` state and passes readiness checks:
   ```bash
   kubectl get pods -n <namespace> -l app=<app-label> -w
   ```
2. Confirm restart count does not increment for at least 10 minutes.
3. Validate application health endpoint returns HTTP 200 OK.

## 12. Rollback Procedure
If the remediation fails or introduces new errors:
```bash
kubectl rollout undo deployment/<deployment-name> -n <namespace> --to-revision=<healthy-revision-number>
```

## 13. Escalation Criteria
- If root cause is a database schema incompatibility or code bug, escalate to Application Dev On-Call.
- If storage volume permission prevents startup, escalate to Platform/Infra SRE.
- If pod continues crashlooping after 15 minutes of triage, escalate incident to P1 if user impact is detected.

## 14. Prevention Recommendations
1. Implement robust startup probes (`startupProbe`) with adequate failure threshold for slow-starting applications.
2. Validate environment variables and configuration files at build time or using admission webhooks.
3. Set appropriate `terminationGracePeriodSeconds` to permit in-flight shutdown handlers to exit cleanly.
4. Establish synthetic canary deployments before routing 100% traffic to new container images.

## 15. Related Documentation References
- Kubernetes Pod Lifecycle: `https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/`
- Determine Pod Failure Reason: `https://kubernetes.io/docs/tasks/debug/debug-application/determine-pod-failure-reason/`
- Core Knowledge Base: `data/corpus/kubernetes/troubleshooting/determine-pod-failure-reason.md`
