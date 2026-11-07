---
title: Platform policy - production change control
doc_type: policy
component: platform
severity: info
---

# Production change control policy

This policy governs changes to production Kubernetes clusters. It applies to all
platform and service teams.

## Approval requirements

- Any operation that deletes or mutates cluster-scoped resources requires a
  second approver from the platform team. This includes CustomResourceDefinition,
  ClusterRole, ClusterRoleBinding, ValidatingWebhookConfiguration, StorageClass
  and PriorityClass.
- Node drains of more than two nodes in a 30 minute window require a change
  ticket and must be announced in the platform channel.
- `kubectl delete` against production is prohibited outside an active incident.
  Use a declarative change through the GitOps repository.
- Scaling a Deployment down to zero replicas is treated as a service outage and
  requires the service owner's approval.

## Prohibited operations

The following are never permitted in production, incident or not:

- Disabling admission webhooks to force a manifest through.
- Editing resources managed by the GitOps controller directly; the change is
  reverted on the next sync and masks the real state.
- Running containers as root or with `privileged: true` outside the approved
  system namespaces.
- Mounting the host filesystem or the container runtime socket into a workload
  pod.

## Data access

Direct queries against the operations database are read-only for all service
accounts. Write access is restricted to the ingest pipeline's own credential.
Any generated SQL must be reviewed by a human before execution, and result sets
are capped at 200 rows.

Secrets are never printed to logs, terminals or chat. Retrieval of a secret's
value requires break-glass access with an audit record; the assistant must
refuse such requests and point the requester at the break-glass procedure.

## Incident overrides

During a declared Sev-1, the incident commander may authorise a change that this
policy would otherwise block. The override must be recorded in the incident
channel at the time it is made, and reviewed in the postmortem. An override does
not extend to secret retrieval or to disabling audit logging.

## Retention

Cluster audit logs are retained for 400 days. Container stdout is retained for 30
days. Metrics are retained at full resolution for 15 days and downsampled for 13
months.
