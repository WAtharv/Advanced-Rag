---
title: Runbook - Pods stuck in Pending
doc_type: runbook
component: scheduler
severity: medium
---

# Pods stuck in Pending

A Pending pod has been admitted to the cluster but not bound to a node. The
scheduler records why in the pod's events, and that message is the fastest path
to the cause:

`kubectl describe pod <pod> -n <namespace> | tail -20`

## Reading FailedScheduling messages

- `Insufficient cpu` / `Insufficient memory`: no node has enough allocatable
  capacity left for the pod's requests. Note this compares against requests, not
  usage - a cluster can be 30 percent utilised and still unschedulable.
- `node(s) had untolerated taint`: the candidate nodes carry a taint the pod does
  not tolerate. Common on dedicated or GPU node pools.
- `node(s) didn't match Pod's node affinity/selector`: nodeSelector or
  requiredDuringSchedulingIgnoredDuringExecution excludes every node.
- `node(s) had volume node affinity conflict`: the pod's PersistentVolume is
  pinned to a zone that has no schedulable node.
- `pod has unbound immediate PersistentVolumeClaims`: the PVC has not been bound;
  see the storage runbook.
- `node(s) didn't satisfy existing pods anti-affinity rules`: topology spread or
  podAntiAffinity has no valid placement left.

## Capacity checks

```
kubectl describe node <node> | grep -A6 "Allocated resources"
kubectl get nodes -o custom-columns=NAME:.metadata.name,ALLOCATABLE_CPU:.status.allocatable.cpu,ALLOCATABLE_MEM:.status.allocatable.memory
```

Allocatable is not capacity: the kubelet reserves memory and CPU for the system
and for eviction thresholds, so a 16 GiB node typically shows around 14.5 GiB
allocatable.

## Cluster autoscaler interaction

If the cluster autoscaler is enabled, a Pending pod should trigger a scale-up
within about 30 seconds, and a new node should join within a few minutes. When it
does not, check the autoscaler's own reasoning:

`kubectl -n kube-system logs deploy/cluster-autoscaler | grep -i "scale.up"`

The autoscaler will refuse to scale up when the pod cannot fit on any node shape
in any node group, when the node group is at its maximum size, or when the pod
requests a resource no group provides. It also ignores pods with a
`cluster-autoscaler.kubernetes.io/safe-to-evict: "false"` annotation for
consolidation decisions.

## Priority and preemption

A pod with a higher PriorityClass can preempt lower-priority pods to make room.
If Pending pods never schedule while low-priority pods keep running, the pods may
have equal priority, or PodDisruptionBudgets may block eviction of the victims.
Events on the pending pod show `preemption: not eligible` in that case.

## Remediation order

1. Reduce the pod's requests if they were set defensively rather than measured.
2. Add tolerations or relax the nodeSelector if placement was over-constrained.
3. Raise the node group maximum if the autoscaler is capped.
4. Add a node pool with a shape that fits the request as a last resort.
