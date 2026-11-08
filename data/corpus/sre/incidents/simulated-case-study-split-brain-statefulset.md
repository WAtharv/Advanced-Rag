---
title: "Incident Case Study: Storage Detach Delay and StatefulSet Split-Brain Mitigation"
incident_id: "SIM-INC-001"
severity: "P1"
category: "incidents"
component: "statefulset-storage"
document_type: "case-study"
doc_type: "case-study"
source_type: "project-authored-sre"
applicable_versions: "Kubernetes 1.28-1.37"
authoring_date: "2026-11-08"
spdx_license: "Apache-2.0"
attribution: "Enterprise SRE Copilot Authors"
content_hash: "0240f17104113d578bde79045b3da4adfc01adb92eefb9dac197d049e0eb88a2"
related_upstream_urls:
  - "https://kubernetes.io/docs/tasks/run-application/force-delete-stateful-set-pod/"
---

# Incident Case Study: Storage Detach Delay and StatefulSet Split-Brain Mitigation

> [!IMPORTANT]
> **SIMULATED INCIDENT — NOT A REAL PRODUCTION EVENT.**
> This incident case study is an educational scenario created for operational training and automated SRE guidance. All timestamps, incident logs, service names, and metrics are simulated.

## 1. Scenario Overview
During a scheduled cloud provider network maintenance event on 2026-08-11, worker node `worker-node-14` lost network connectivity to the Kubernetes control plane. The node hosted `postgres-0`, the primary database pod of a stateful PostgreSQL cluster.

The node controller marked `worker-node-14` as `NotReady` after 40 seconds. Following the default pod eviction timeout (300 seconds), Kubernetes attempted to recreate `postgres-0` on `worker-node-22`. However, the underlying cloud block volume (EBS volume `vol-0492819`) remained attached to `worker-node-14`.

## 2. Operational Challenge: The Danger of Premature Force-Deletion
An inexperienced on-call engineer suggested executing:
`kubectl delete pod postgres-0 -n database --force --grace-period=0`

> [!CAUTION]
> **Why this would have caused catastrophic data corruption (Split-Brain):**
> If `worker-node-14` was merely partitioned from the control plane but still running locally, the original PostgreSQL process on `worker-node-14` would continue writing to disk. Force-deleting the pod in Kubernetes does NOT stop the running container on the disconnected host. If the storage driver detached the volume and attached it to `worker-node-22`, TWO running database processes would attempt to write to the same filesystem simultaneously, permanently corrupting the database.

## 3. Correct SRE Mitigation Sequence
The Incident Commander intervened and enforced the safe fencing protocol:
1. **Fencing (Node Power Off):** Before any forced deletion, the engineer used the cloud provider API to hard-terminate `worker-node-14`:
   `aws ec2 stop-instances --instance-ids i-082498192841`
2. **Attachment Verification:** Verified instance state was `stopped`.
3. **Delete VolumeAttachment:** Safely deleted the stuck Kubernetes `VolumeAttachment` object.
4. **Trigger Safe Pod Recreation:** Deleted the pod, allowing `postgres-0` to mount cleanly on `worker-node-22`.

## 4. Key Takeaways
- Never force-delete a StatefulSet pod until physical node power termination is 100% verified.
- Modern Kubernetes clusters should enable the `NonGracefulNodeShutdown` taint feature (`node.kubernetes.io/out-of-service`) to automate safe fencing.
