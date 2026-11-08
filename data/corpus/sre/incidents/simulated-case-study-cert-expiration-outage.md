---
title: "Incident Case Study: Kubelet Client Certificate Expiration Outage and Recovery"
incident_id: "SIM-INC-002"
severity: "P1"
category: "incidents"
component: "pki-certificates"
document_type: "case-study"
doc_type: "case-study"
source_type: "project-authored-sre"
applicable_versions: "Kubernetes 1.28-1.37"
authoring_date: "2026-11-08"
spdx_license: "Apache-2.0"
attribution: "Enterprise SRE Copilot Authors"
content_hash: "26872710057f41e16b6f28d1e70f5737b37606f48f1f68661e8beab17d9c527e"
related_upstream_urls:
  - "https://kubernetes.io/docs/tasks/administer-cluster/kubeadm/kubeadm-certs/"
---

# Incident Case Study: Kubelet Client Certificate Expiration Outage and Recovery

> [!IMPORTANT]
> **SIMULATED INCIDENT — NOT A REAL PRODUCTION EVENT.**
> This incident case study is an educational scenario created for operational training and automated SRE guidance. All timestamps, incident logs, service names, and metrics are simulated.

## 1. Scenario Overview
On 2026-07-22 at exactly 00:00 UTC, all 48 worker nodes in production cluster `k8s-prod-eu` simultaneously transitioned to `NotReady`. The API server refused all lease renewal requests from Kubelets. Running user traffic continued for 5 minutes until node controller started evicting pods, resulting in cluster-wide degradation.

## 2. Investigation and Findings
1. Inspection of Kubelet logs on `node-01`:
   `journalctl -u kubelet -n 50` revealed:
   `x509: certificate has expired or is not yet valid: current time 2026-07-22T00:03:12Z is after 2026-07-22T00:00:00Z`
2. The cluster was initially provisioned with `kubeadm` exactly 365 days prior.
3. Kubelet client certificate auto-rotation (`RotateKubeletClientCertificate`) was enabled, but automatic CSR approval controller had been accidentally disabled during a previous control plane hardening task.

## 3. Emergency Recovery Execution
1. On control plane nodes, renewed control plane certificates:
   `sudo kubeadm certs renew all`
2. Restarted control plane components.
3. Approved the pending worker node CSRs:
   `kubectl get csr -o name | xargs -r kubectl certificate approve`
4. On stubborn nodes where bootstrap token had also expired:
   Regenerated bootstrap token: `kubeadm token create` and re-bootstrapped Kubelet client certificates.
5. All 48 nodes returned to `Ready` within 18 minutes.

## 4. Key Takeaways
- Certificate expiration must be monitored with high-priority Prometheus alerts at 30, 14, and 7 days remaining.
- Do not disable `kubelet-client-approver` in `kube-controller-manager` unless an external automated PKI operator handles CSR approvals.
