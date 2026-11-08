---
title: "Runbook: EndpointSlice Desynchronization and Stale Backends"
incident_id: "RB-NET-005"
severity: "P2"
category: "runbooks/networking"
component: "endpointslice-controller"
document_type: "runbook"
doc_type: "runbook"
source_type: "project-authored-sre"
applicable_versions: "Kubernetes 1.28-1.37"
authoring_date: "2026-11-08"
spdx_license: "Apache-2.0"
attribution: "Enterprise SRE Copilot Authors"
content_hash: "0e7a459db9d4f2708e77ad71c99481780db083da4e84ba690ced3c835840ad02"
related_upstream_urls:
  - "https://kubernetes.io/docs/concepts/services-networking/endpoint-slices/"
---

# Runbook: EndpointSlice Desynchronization and Stale Backends

## 1. Incident Identifier
- **ID:** RB-NET-005
- **Severity:** P2 (High) — EndpointSlice controller fails to update backend endpoints, causing traffic to be routed to terminated or non-existent pods.

## 2. Severity Classification
- **P1:** Outdated endpoints routing customer traffic into dead IP blackholes across multiple services.
- **P2:** Intermittent connection drops after pod rollouts due to stale EndpointSlice references.
- **P3:** Stale EndpointSlice mirror in test namespace.

## 3. Symptoms
- Kube-proxy routes traffic to terminated pod IPs, returning TCP RST or timeout.
- `kubectl get endpointslices` contains addresses of pods that no longer exist in `kubectl get pods`.
- Alert `EndpointSliceMirroringFailed` firing.

## 4. Common Causes
1. **EndpointSlice Controller Backlog / Crash:** Kube-controller-manager saturated, delaying EndpointSlice updates.
2. **Orphaned EndpointSlices:** Rapid creation and deletion of services leaving orphaned slices.
3. **Dual-Stack IPv4/IPv6 Misconfiguration:** Service configured with `IPFamilies: [IPv6, IPv4]` but nodes only support IPv4.
4. **Custom Controller Interference:** Third-party operator manually modifying managed EndpointSlices.

## 5. Impact Assessment
- Traffic blackholing; 5-10% packet loss during scaling or rolling deployment events.

## 6. Diagnostic Procedure
1. List EndpointSlices for the affected service.
2. Compare IP addresses in EndpointSlice with current live pod IPs.
3. Check kube-controller-manager logs for sync errors.

## 7. Read-Only Investigation Commands
```bash
# 1. Inspect EndpointSlices associated with service
kubectl get endpointslices -l kubernetes.io/service-name=<service-name> -n <namespace> -o yaml

# 2. Compare against active running pod IPs
kubectl get pods -n <namespace> -l <service-selector> -o custom-columns=NAME:.metadata.name,STATUS:.status.phase,IP:.status.podIP

# 3. Check endpoint conditions in slice (Ready, Serving, Terminating)
kubectl get endpointslices -l kubernetes.io/service-name=<service-name> -n <namespace> -o jsonpath='{range .items[*].endpoints[*]}{.addresses[*]}{"	Ready:"}{.conditions.ready}{"	Terminating:"}{.conditions.terminating}{"
"}{end}'
```

## 8. Expected Outputs
- IP present in EndpointSlice marked `ready: true`, but pod deleted >5 minutes ago: Stale endpoint desynchronization confirmed.

## 9. Decision Tree
```
                         [Stale Endpoints Detected]
                                     |
                          Are Slices Auto-Generated?
                                     |
         +---------------------------+---------------------------+
         |                                                       |
   [Managed by EndpointSlice Controller]                   [Manually Managed]
         |                                                       |
   Delete stale EndpointSlice object;                      Fix external controller
   controller will immediately recreate fresh slice        or update endpoints
```

## 10. Remediation Steps

### Action A: Delete Stale EndpointSlice to Trigger Instant Regeneration (Safe)
```bash
# Deleting the managed EndpointSlice forces the endpointslice-controller to recreate it immediately from live pods
kubectl delete endpointslice <stale-slice-name> -n <namespace>
```

### Action B: Restart Kube-Controller-Manager if Stalled (Control Plane Access)
```bash
# Restart controller-manager static pod on control plane host
sudo kill -s SIGHUP $(pidof kube-controller-manager)
```

## 11. Verification After Remediation
1. Verify new EndpointSlice is generated within 5 seconds:
   `kubectl get endpointslices -l kubernetes.io/service-name=<service-name> -n <namespace>`
2. Confirm all listed IPs correspond to currently active pods in `Running` state.

## 12. Rollback Procedure
N/A — EndpointSlice regeneration is automatic and idempotent.

## 13. Escalation Criteria
- If kube-controller-manager sync loop is permanently stuck across all services, escalate as P1 Control Plane Incident.

## 14. Prevention Recommendations
1. Ensure `kube-controller-manager` has adequate CPU resources and `--concurrent-endpoint-syncs` is tuned for large clusters (>500 nodes).

## 15. Related Documentation References
- EndpointSlices: `https://kubernetes.io/docs/concepts/services-networking/endpoint-slices/`
