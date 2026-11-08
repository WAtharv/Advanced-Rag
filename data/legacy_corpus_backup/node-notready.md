---
title: Runbook - Node NotReady
doc_type: runbook
component: kubelet
severity: critical
---

# Node NotReady

A node reports NotReady when the kubelet stops posting healthy status to the API
server, or when it reports a condition that makes it unfit to run pods. After
the node monitor grace period (40 seconds by default) the controller manager
marks the node NotReady; after the pod eviction timeout (5 minutes) it begins
evicting pods from that node.

## Determine which condition failed

```
kubectl describe node <node> | grep -A12 Conditions
kubectl get node <node> -o jsonpath='{.status.conditions}'
```

- `Ready=False` with `KubeletNotReady`: the kubelet is running but reports a
  problem, most often the container runtime being down or the CNI plugin not
  initialised.
- `Ready=Unknown`: the API server has not heard from the kubelet at all. Network
  partition, kubelet crash, or the node is gone.
- `MemoryPressure=True`: available memory crossed the eviction threshold.
- `DiskPressure=True`: the image or root filesystem crossed its threshold.
- `PIDPressure=True`: too many processes on the node.

## DiskPressure

The most common recoverable cause. The kubelet garbage-collects images at
imagefs.available below 15 percent and evicts pods below 10 percent.

```
df -h /var/lib/containerd /var/lib/kubelet
crictl images | wc -l
journalctl -u kubelet --since "30 min ago" | grep -i evict
```

Remediation: `crictl rmi --prune` to remove unused images, then find what is
consuming the filesystem. Frequent culprits are container logs without rotation,
emptyDir volumes used as scratch space with no size limit, and a stuck image pull
leaving partial layers.

## Kubelet and runtime health

```
systemctl status kubelet containerd
journalctl -u kubelet --since "15 min ago" --no-pager | tail -50
crictl info
```

If containerd is unhealthy the kubelet cannot report Ready even though the node
is otherwise fine. Restarting containerd will restart every container on the
node, so drain first unless the node is already evicting.

## Safe recovery

1. Cordon the node so nothing new is scheduled: `kubectl cordon <node>`
2. Drain it, respecting disruption budgets:
   `kubectl drain <node> --ignore-daemonsets --delete-emptydir-data --timeout=300s`
3. Remediate at the OS level, or replace the node if it is part of a managed
   node group - replacement is usually faster and safer than repair.
4. Uncordon only after the node reports Ready and a test pod schedules
   successfully.

## When several nodes go NotReady together

Do not drain them. Simultaneous NotReady across nodes points at the control
plane, the network fabric, or an expired certificate rather than the nodes.
Draining in that state will fail to reschedule and can cascade. Check API server
health and etcd first, and confirm the kubelet client certificates have not
expired.
