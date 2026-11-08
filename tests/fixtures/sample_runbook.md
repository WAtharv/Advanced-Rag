---
title: Production Node Evacuation Runbook
doc_type: runbook
category: operation
component: kubelet
source_url: https://internal.sre.corp/runbooks/node-evacuation
source_version: "2.1.0"
---

# Node Evacuation Procedures

This runbook covers how to safely drain pods from problematic cluster nodes.

## Drain Preparation

Before starting the drain, verify the node status and cordoning:

```bash
kubectl cordon node-01.corp.internal
kubectl get nodes -o wide
```

## Eviction Command

Execute graceful eviction with appropriate timeouts:

```bash
kubectl drain node-01.corp.internal --ignore-daemonsets --delete-emptydir-data --timeout=300s
```

