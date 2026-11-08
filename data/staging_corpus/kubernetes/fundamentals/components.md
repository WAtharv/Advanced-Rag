---
title: "Kubernetes Components"
category: "fundamentals"
component: "control-plane"
source_url: "https://kubernetes.io/docs/concepts/overview/components/"
source_repository: "https://github.com/kubernetes/website"
source_version: "release-1.37"
retrieved_at: "2026-11-07T15:52:17.115439+00:00"
spdx_license: "CC-BY-4.0"
attribution: "Copyright The Kubernetes Authors"
content_hash: "3ad60a1f8b6744e49daff7f807d00cf4151c1ebd66b132c01d598528347d91c5"
document_type: "concept"
doc_type: "concept"
---

This page provides a high-level overview of the essential components that make up a Kubernetes cluster.

## Core Components

A Kubernetes cluster consists of a control plane and one or more worker nodes.
Here's a brief overview of the main components:

### Control Plane Components

Manage the overall state of the cluster:

[kube-apiserver](/docs/concepts/architecture/#kube-apiserver)
: The core component server that exposes the Kubernetes HTTP API.

[etcd](/docs/concepts/architecture/#etcd)
: Consistent and highly-available key value store for all API server data.

[kube-scheduler](/docs/concepts/architecture/#kube-scheduler)
: Looks for Pods not yet bound to a node, and assigns each Pod to a suitable node.

[kube-controller-manager](/docs/concepts/architecture/#kube-controller-manager)
: Runs controllers to implement Kubernetes API behavior.

[cloud-controller-manager](/docs/concepts/architecture/#cloud-controller-manager) (optional)
: Integrates with underlying cloud provider(s).

### Node Components

Run on every node, maintaining running pods and providing the Kubernetes runtime environment:

[kubelet](/docs/concepts/architecture/#kubelet)
: Ensures that Pods are running, including their containers.

[kube-proxy](/docs/concepts/architecture/#kube-proxy) (optional)
: Maintains network rules on nodes to implement Services.

[Container runtime](/docs/concepts/architecture/#container-runtime)
: Software responsible for running containers. Read
  [Container Runtimes](/docs/setup/production-environment/container-runtimes/) to learn more.

Your cluster may require additional software on each node; for example, you might also
run [systemd](https://systemd.io/) on a Linux node to supervise local components.

## Addons

Addons extend the functionality of Kubernetes. A few important examples include:

[DNS](/docs/concepts/architecture/#dns)
: For cluster-wide DNS resolution.

[Web UI](/docs/concepts/architecture/#web-ui-dashboard) (Dashboard)
: For cluster management via a web interface.

[Container Resource Monitoring](/docs/concepts/architecture/#container-resource-monitoring)
: For collecting and storing container metrics.

[Cluster-level Logging](/docs/concepts/architecture/#cluster-level-logging)
: For saving container logs to a central log store.

## Flexibility in Architecture

Kubernetes allows for flexibility in how these components are deployed and managed.
The architecture can be adapted to various needs, from small development environments
to large-scale production deployments.

For more detailed information about each component and various ways to configure your
cluster architecture, see the [Cluster Architecture](/docs/concepts/architecture/) page.
