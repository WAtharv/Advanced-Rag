---
title: "Kubernetes Scheduler"
category: "scheduling"
component: "kube-scheduler"
source_url: "https://kubernetes.io/docs/concepts/scheduling-eviction/kube-scheduler/"
source_repository: "https://github.com/kubernetes/website"
source_version: "release-1.37"
retrieved_at: "2026-11-07T15:52:17.115439+00:00"
spdx_license: "CC-BY-4.0"
attribution: "Copyright The Kubernetes Authors"
content_hash: "cd7646b1bd7ae87fb0980df9ee4f060350afd5e53dace7d0b04a8582c56cb54d"
document_type: "concept"
doc_type: "concept"
---

In Kubernetes, _scheduling_ refers to making sure that Pods
are matched to Nodes so that
control plane.
kube-scheduler is designed so that, if you want and need to, you can
write your own scheduling component and use that instead.

Kube-scheduler selects an optimal node to run newly created or not yet
scheduled (unscheduled) pods. Since containers in pods - and pods themselves -
can have different requirements, the scheduler filters out any nodes that
don't meet a Pod's specific scheduling needs. Alternatively, the API lets
you specify a node for a Pod when you create it, but this is unusual
and is only done in special cases.

In a cluster, Nodes that meet the scheduling requirements for a Pod
are called _feasible_ nodes. If none of the nodes are suitable, the pod
remains unscheduled until the scheduler is able to place it.

The scheduler finds feasible Nodes for a Pod and then runs a set of
functions to score the feasible Nodes and picks a Node with the highest
score among the feasible ones to run the Pod. The scheduler then notifies
the API server about this decision in a process called _binding_.

Factors that need to be taken into account for scheduling decisions include
individual and collective resource requirements, hardware / software /
policy constraints, affinity and anti-affinity specifications, data
locality, inter-workload interference, and so on.

### Node selection in kube-scheduler {#kube-scheduler-implementation}

kube-scheduler selects a node for the pod in a 2-step operation:

1. Filtering
1. Scoring

The _filtering_ step finds the set of Nodes where it's feasible to
schedule the Pod. For example, the PodFitsResources filter checks whether a
candidate Node has enough available resources to meet a Pod's specific
resource requests. After this step, the node list contains any suitable
Nodes; often, there will be more than one. If the list is empty, that
Pod isn't (yet) schedulable.

In the _scoring_ step, the scheduler ranks the remaining nodes to choose
the most suitable Pod placement. The scheduler assigns a score to each Node
that survived filtering, basing this score on the active scoring rules.

Finally, kube-scheduler assigns the Pod to the Node with the highest ranking.
If there is more than one node with equal scores, kube-scheduler selects
one of these at random.

There are two supported ways to configure the filtering and scoring behavior
of the scheduler:

1. [Scheduling Policies](/docs/reference/scheduling/policies) allow you to configure _Predicates_ for filtering and _Priorities_ for scoring.
1. [Scheduling Profiles](/docs/reference/scheduling/config/#profiles) allow you to configure Plugins that implement different scheduling stages, including: `QueueSort`, `Filter`, `Score`, `Bind`, `Reserve`, `Permit`, and others. You can also configure the kube-scheduler to run different profiles.

## ## What's next

* Read about [scheduler performance tuning](/docs/concepts/scheduling-eviction/scheduler-perf-tuning/)
* Read about [Pod topology spread constraints](/docs/concepts/scheduling-eviction/topology-spread-constraints/)
* Read the [reference documentation](/docs/reference/command-line-tools-reference/kube-scheduler/) for kube-scheduler
* Read the [kube-scheduler config (v1)](/docs/reference/config-api/kube-scheduler-config.v1/) reference
* Learn about [configuring multiple schedulers](/docs/tasks/extend-kubernetes/configure-multiple-schedulers/)
* Learn about [topology management policies](/docs/tasks/administer-cluster/topology-manager/)
* Learn about [Pod Overhead](/docs/concepts/scheduling-eviction/pod-overhead/)
* Learn about scheduling of Pods that use volumes in:
  * [Volume Topology Support](/docs/concepts/storage/storage-classes/#volume-binding-mode)
  * [Storage Capacity Tracking](/docs/concepts/storage/storage-capacity/)
  * [Node-specific Volume Limits](/docs/concepts/storage/storage-limits/)
