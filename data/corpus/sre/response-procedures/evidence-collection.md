---
title: "Standard Procedure: Non-Destructive Evidence Collection and State Snapshotting"
incident_id: "SRE-PROC-004"
severity: "Standard"
category: "response-procedures"
component: "incident-framework"
document_type: "procedure"
doc_type: "procedure"
source_type: "project-authored-sre"
applicable_versions: "All Environments"
authoring_date: "2026-11-08"
spdx_license: "Apache-2.0"
attribution: "Enterprise SRE Copilot Authors"
content_hash: "618858e7acacfbc145b4e790478dac437f0101ffd80d89aabb63c072c7099d85"
---

# Standard Procedure: Non-Destructive Evidence Collection and State Snapshotting

## 1. Principle
Never restart a pod, drain a node, or delete a resource without first preserving volatile runtime evidence.

## 2. Mandatory Evidence Snapshot Checklist
Before mutating or restarting failed workloads:
```bash
# 1. Capture pod events and describe state
kubectl describe pod <pod-name> -n <namespace> > /tmp/evidence-<pod-name>-describe.txt

# 2. Capture current and previous container logs
kubectl logs <pod-name> -n <namespace> --all-containers --tail=500 > /tmp/evidence-<pod-name>-current.log
kubectl logs <pod-name> -n <namespace> --all-containers --previous --tail=500 > /tmp/evidence-<pod-name>-previous.log

# 3. Capture namespace events
kubectl get events -n <namespace> --sort-by=.lastTimestamp > /tmp/evidence-<namespace>-events.txt

# 4. Capture resource YAML definition
kubectl get pod <pod-name> -n <namespace> -o yaml > /tmp/evidence-<pod-name>-spec.yaml
```

## 3. Storage
Upload all evidence files to the designated incident evidence bucket or attach to the postmortem ticket.
