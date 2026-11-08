---
title: "Postmortem: CoreDNS Exhaustion and Inter-Service DNS Resolution Failure"
incident_id: "SIM-PM-002"
severity: "P1"
category: "postmortems"
component: "networking-dns"
document_type: "postmortem"
doc_type: "postmortem"
source_type: "project-authored-sre"
applicable_versions: "Kubernetes 1.28-1.37"
authoring_date: "2026-11-08"
spdx_license: "Apache-2.0"
attribution: "Enterprise SRE Copilot Authors"
content_hash: "e286bcc2ed3b524f943f197cb19f5643eefedb3574918c9cf1c1d43a568ffce2"
related_upstream_urls:
  - "https://kubernetes.io/docs/tasks/administer-cluster/dns-debugging-resolution/"
---

# Postmortem: CoreDNS Exhaustion and Inter-Service DNS Resolution Failure

> [!IMPORTANT]
> **SIMULATED INCIDENT — NOT A REAL PRODUCTION EVENT.**
> This incident postmortem is an educational case study created for operational training and automated SRE guidance. All timestamps, incident logs, service names, and metrics are simulated.

## 1. Incident Summary
On 2026-10-02 at 09:18 UTC, cluster-wide DNS lookup failures occurred across `prod-cluster-01`. Internal microservice calls timed out resolving `.cluster.local` domain names, causing widespread 504 Gateway Timeouts across the mobile API gateway. Duration was 22 minutes. The incident was mitigated by scaling CoreDNS from 2 replicas to 8 replicas and deploying NodeLocal DNSCache.

## 2. Customer and Service Impact
- **Impacted Services:** All microservices performing service discovery.
- **Duration:** 22 minutes (09:18 UTC to 09:40 UTC).
- **User Impact:** Mobile app users experienced repeated connection timeout banners.

## 3. Incident Timeline (UTC)
- **09:15 UTC:** Flash marketing campaign launched, driving a 400% surge in user logins.
- **09:18 UTC:** CoreDNS CPU utilization spiked to 100% on both existing replicas.
- **09:20 UTC:** DNS queries began timing out (>2000ms latency); applications experienced connection pool exhaustion.
- **09:22 UTC:** P1 alert `CoreDNSLookupsHighLatency` fired; incident declared.
- **09:26 UTC:** SRE observed CoreDNS dropping UDP packets and scaling delayed due to missing HPA.
- **09:28 UTC:** SRE executed manual emergency scale-up: `kubectl scale deployment/coredns -n kube-system --replicas=8`.
- **09:33 UTC:** Additional CoreDNS pods became ready across worker nodes.
- **09:36 UTC:** DNS query latency dropped from 2400ms to 3.2ms.
- **09:40 UTC:** Microservice error rates returned to 0%; incident mitigated.

## 4. Root Cause
Default Kubernetes cluster deployment provided only 2 CoreDNS replicas with CPU limits set to 100m. The sudden 4x traffic surge generated over 45,000 DNS queries per second, causing CPU throttling, UDP packet drops, and kernel conntrack exhaustion on the two host nodes.

## 5. Contributing Factors
1. **High Default ndots Setting:** Application default `ndots: 5` resulted in up to 4 redundant lookup attempts for every external API call.
2. **Absence of NodeLocal DNSCache:** All cluster DNS traffic had to traverse virtual overlay network to two centralized pods.
3. **Absence of CoreDNS Autoscaling:** CoreDNS was configured with static 2 replicas rather than cluster-proportional or horizontal autoscaling.

## 6. Corrective Actions Taken
- [x] Scaled CoreDNS to 8 replicas.
- [x] Deployed NodeLocal DNSCache DaemonSet across all worker nodes.
- [x] Configured `cluster-proportional-autoscaler` to maintain 1 CoreDNS replica per 16 cluster nodes.
- [x] Configured `ndots: 2` in application pod templates.
