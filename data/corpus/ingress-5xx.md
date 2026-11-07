---
title: Runbook - Ingress 502 and 504 errors
doc_type: runbook
component: ingress-nginx
severity: high
---

# Ingress 502 and 504 errors

502 Bad Gateway means the ingress controller reached no healthy upstream or the
upstream closed the connection. 504 Gateway Timeout means an upstream accepted
the connection but did not answer within the proxy timeout. The distinction
matters: 502 is a connectivity or readiness problem, 504 is a latency problem.

## First checks

```
kubectl -n ingress-nginx logs deploy/ingress-nginx-controller --tail=100 | grep -E "502|504"
kubectl get endpoints <service> -n <namespace>
kubectl get pods -n <namespace> -l app=<label> -o wide
```

An empty ENDPOINTS column is the single most common cause of 502. It means no pod
currently passes its readiness probe, or the Service selector does not match the
pod labels.

## Service and selector mismatch

`kubectl get service <service> -n <namespace> -o yaml` and compare
`spec.selector` against the pod labels. A Service with a selector that matches
nothing is created successfully and reports no error - it simply never gets
endpoints. Also confirm `targetPort` names the port the container actually
listens on; a numeric targetPort that does not match produces connection refused
on every request.

## Readiness probes and rolling updates

During a rolling update, 502s appear in bursts if terminating pods are removed
from the endpoints list after they stop accepting connections rather than before.
Two fixes, applied together:

```yaml
lifecycle:
  preStop:
    exec:
      command: ["sh", "-c", "sleep 10"]
terminationGracePeriodSeconds: 45
```

The preStop sleep gives kube-proxy and the ingress controller time to observe the
endpoint removal before the process exits. The grace period must exceed the sleep
plus the application's own drain time.

## 504 and timeout tuning

The controller's default proxy read timeout is 60 seconds. Requests that
legitimately run longer need a per-ingress annotation:

```yaml
metadata:
  annotations:
    nginx.ingress.kubernetes.io/proxy-read-timeout: "180"
    nginx.ingress.kubernetes.io/proxy-send-timeout: "180"
```

Raise these only for endpoints that genuinely need it. A blanket increase turns
fast failures into held connections and exhausts the controller's worker
connections under load.

## Upstream saturation

If 502s correlate with load rather than deploys, the upstream is refusing
connections because its listen backlog is full. Check the application's own
concurrency limit, the pod count against HPA maximum, and whether CPU throttling
(`container_cpu_cfs_throttled_seconds_total`) is starving the request loop. CPU
limits set close to requests cause throttling that presents as intermittent 502s
with no application errors logged.

## NetworkPolicy

A default-deny NetworkPolicy in the workload namespace without an explicit
ingress rule for the controller's namespace blocks the proxy connection and
produces uniform 502s that are unaffected by scaling or restarts.
