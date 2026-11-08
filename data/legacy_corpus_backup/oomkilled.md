---
title: Runbook - OOMKilled and memory limits
doc_type: runbook
component: kubelet
severity: high
---

# OOMKilled containers

A container is OOMKilled when it exceeds its memory limit and the kernel OOM
killer terminates it. The container status shows reason OOMKilled with exit code
137. Kubernetes memory limits are enforced by cgroups and are not negotiable at
runtime: there is no swapping and no soft grace period by default.

## Confirming the diagnosis

```
kubectl get pod <pod> -n <namespace> -o jsonpath='{.status.containerStatuses[*].lastState.terminated.reason}'
kubectl describe pod <pod> -n <namespace> | grep -A5 "Last State"
```

Node-level confirmation: `dmesg -T | grep -i "killed process"` on the node shows
the kernel decision and the RSS at kill time.

## Distinguishing the three common causes

- Limit set too low: memory use climbs to the limit quickly after start and stays
  flat until the kill. The workload simply needs more than it was given.
- Memory leak: memory grows steadily over hours or days regardless of load, and
  each restart resets the clock. Restart interval is roughly constant.
- Load-driven spike: memory tracks request rate. Kills cluster around traffic
  peaks and coincide with latency increases upstream.

Plot `container_memory_working_set_bytes` against
`kube_pod_container_resource_limits{resource="memory"}` over 7 days to tell them
apart before changing anything.

## Requests, limits and QoS

The requests value drives scheduling; the limits value drives the OOM kill. The
relationship between them determines the pod's QoS class:

- Guaranteed: requests equal limits for every container. Evicted last.
- Burstable: requests set and lower than limits. Evicted after BestEffort.
- BestEffort: neither set. Evicted first under node memory pressure.

For latency-sensitive services set memory requests equal to memory limits. This
gives the pod Guaranteed QoS, which protects it from eviction when the node comes
under pressure, at the cost of reserving capacity it may not use.

## Node memory pressure versus container OOM

A container OOM kill affects one container and shows OOMKilled. Node memory
pressure triggers kubelet eviction instead, which shows as pod status Evicted
with a message about the node condition MemoryPressure. Eviction respects QoS
class and pod priority; container OOM kills do not.

Check which one happened before tuning: raising a limit makes container OOM
kills less frequent but makes node pressure worse.

## Remediation

1. Set the limit from observed p99 working set plus 25 to 40 percent headroom.
2. For JVM workloads, set -XX:MaxRAMPercentage=75 rather than a fixed -Xmx so
   the heap tracks the cgroup limit.
3. Add a memory-based HPA only when the workload's memory genuinely scales with
   concurrency; for leaks, horizontal scaling hides the problem.
4. If the limit must exceed node capacity headroom, move the workload to a node
   pool with larger instances rather than overcommitting.
