---
title: Postmortem - checkout outage 2026-03-14
doc_type: postmortem
component: ingress-nginx
severity: critical
---

# Postmortem: checkout outage, 2026-03-14

Duration: 47 minutes (14:02 - 14:49 UTC). Impact: 100 percent of checkout
requests returned 502 in the eu-west-1 production cluster. Estimated 18,400
failed transactions.

## Timeline

- 13:58 - Deploy of checkout-api v2.31.0 begins, rolling update, 12 replicas.
- 14:02 - Ingress 502 rate goes from 0 to 100 percent. Alert fires.
- 14:07 - On-call confirms `kubectl get endpoints checkout-api` returns none.
- 14:15 - Rollback to v2.30.4 started; endpoints remain empty.
- 14:31 - Engineer notices new pods are Running but never Ready.
- 14:38 - Readiness probe path found changed from /healthz to /health in v2.31.0
  application code, while the Deployment manifest still probed /healthz.
- 14:41 - Rollback completes with corrected manifest; endpoints repopulate.
- 14:49 - Error rate returns to baseline.

## Root cause

The readiness probe path was renamed in application code without a matching
change to the Deployment manifest, which lives in a separate repository. Every
new pod failed readiness, so the Service had no endpoints and the ingress
controller had no upstream to route to. The rolling update completed from
Kubernetes' point of view because maxUnavailable allowed old pods to terminate
before new ones became Ready.

## Contributing factors

- maxUnavailable was set to 50 percent, so half the healthy fleet was removed
  before the failure was detectable.
- No deployment gate on endpoint count; the pipeline treated "pods Running" as
  success.
- The rollback initially reused the same broken manifest, costing 16 minutes.

## Corrective actions

1. Set `maxUnavailable: 0` and `maxSurge: 25%` for all customer-facing
   Deployments, so a bad rollout cannot remove healthy capacity.
2. Add a post-deploy check that asserts endpoint count equals expected replicas
   before marking the pipeline green.
3. Move probe paths into a shared config consumed by both repositories.
4. Add an alert on `kube_endpoint_address_available == 0` for tier-1 services,
   which would have fired at 14:03 with an unambiguous cause.

## Lesson

Ready is the only signal that matters for traffic. Running is not readiness, and
a rolling update will happily complete while producing zero serving capacity.
