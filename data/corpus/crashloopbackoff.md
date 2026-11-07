---
title: Runbook - Pod CrashLoopBackOff
doc_type: runbook
component: kubelet
severity: high
---

# Pod CrashLoopBackOff

A pod enters CrashLoopBackOff when its container exits repeatedly and the kubelet
applies an exponential back-off before each restart. The back-off starts at 10s
and doubles to a cap of 5 minutes, resetting after the container stays healthy
for 10 minutes. CrashLoopBackOff is a symptom, never a root cause.

## Triage steps

1. Read the previous container's logs, not the current ones. The running
   container may not have produced output yet:
   `kubectl logs <pod> -c <container> --previous -n <namespace>`
2. Get the exit code and reason:
   `kubectl get pod <pod> -n <namespace> -o jsonpath='{.status.containerStatuses[*].lastState.terminated}'`
3. Read the events, which carry probe failures and image errors:
   `kubectl describe pod <pod> -n <namespace>`

## Exit code interpretation

- Exit code 0 with restarts: the process completed and the restartPolicy is
  Always. Use a Job or CronJob instead of a Deployment.
- Exit code 1 or 2: application error. The logs hold the cause; usually a
  missing environment variable, an unreachable dependency at startup, or a
  malformed config file.
- Exit code 137: SIGKILL. Almost always OOMKilled - confirm in lastState and see
  the memory limits runbook.
- Exit code 139: SIGSEGV, a segmentation fault inside the container.
- Exit code 143: SIGTERM. The container was asked to stop and did; check whether
  a liveness probe is killing it.

## Liveness probe induced crash loops

A liveness probe that is stricter than the application's startup time produces a
crash loop that looks like an application bug. The pod starts, fails the probe
before it finishes initialising, gets killed, and repeats.

Symptoms: events show `Liveness probe failed` followed by `Killing container`,
and the container's own logs show a clean startup that never completes.

Fix: add a startupProbe so the liveness probe does not run during boot, and set
failureThreshold and periodSeconds from measured startup time.

```yaml
startupProbe:
  httpGet:
    path: /healthz
    port: 8080
  failureThreshold: 30
  periodSeconds: 5
livenessProbe:
  httpGet:
    path: /healthz
    port: 8080
  periodSeconds: 10
  failureThreshold: 3
```

## Config and secret errors

If the container exits immediately with no application logs, check that every
referenced ConfigMap and Secret exists in the same namespace. A missing key in
an existing ConfigMap fails at container start with CreateContainerConfigError
rather than CrashLoopBackOff, so both statuses should be checked together.

## Escalation

Escalate to the owning service team when the exit code indicates an application
fault and the last deploy was more than 24 hours ago. Escalate to platform when
the crash loop spans multiple unrelated workloads on the same node, which points
at the node or the container runtime rather than the applications.
