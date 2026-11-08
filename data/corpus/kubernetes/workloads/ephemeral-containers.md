---
title: "Ephemeral Containers"
category: "workloads"
component: "pod"
kubernetes_version: "1.37"
source_repository: "https://github.com/kubernetes/website"
source_ref: "dev-1.37"
source_commit: "aebe074739e78e9111943ac285df6ae3a6319f69"
source_url: "https://kubernetes.io/docs/concepts/workloads/pods/ephemeral-containers/"
source_version: "1.37"
retrieved_at: "2026-11-07T15:52:17.115439+00:00"
spdx_license: "CC-BY-4.0"
attribution: "Copyright The Kubernetes Authors"
content_hash: "9728500cfcbf4c7b4f62ad56e1736de415fc0d75a4c20d1ce5c3ff887f5b0e66"
document_type: "concept"
doc_type: "concept"
---

This page provides an overview of ephemeral containers: a special type of container
that runs temporarily in an existing Pods are the fundamental building
block of Kubernetes applications. Since Pods are intended to be disposable and
replaceable, you cannot add a container to a Pod once it has been created.
Instead, you usually delete and replace Pods in a controlled fashion using
deployments.

Sometimes it's necessary to inspect the state of an existing Pod, however, for
example to troubleshoot a hard-to-reproduce bug. In these cases you can run
an ephemeral container in an existing Pod to inspect its state and run
arbitrary commands.

### What is an ephemeral container?

Ephemeral containers differ from other containers in that they lack guarantees
for resources or execution, and they will never be automatically restarted, so
they are not appropriate for building applications.  Ephemeral containers are
described using the same `ContainerSpec` as regular containers, but many fields
are incompatible and disallowed for ephemeral containers.

- Ephemeral containers may not have ports, so fields such as `ports`,
  `livenessProbe`, `readinessProbe` are disallowed.
- Pod resource allocations are immutable, so setting `resources` is disallowed.
- For a complete list of allowed fields, see the [EphemeralContainer reference
  documentation](/docs/reference/generated/kubernetes-api/v1.37/#ephemeralcontainer-v1-core).

Ephemeral containers are created using a special `ephemeralcontainers` handler
in the API rather than by adding them directly to `pod.spec`, so it's not
possible to add an ephemeral container using `kubectl edit`.

Like regular containers, you may not change or remove an ephemeral container
after you have added it to a Pod.

> **Note:** 
Ephemeral containers are not supported by [static pods](/docs/tasks/configure-pod-container/static-pod/).

## Uses for ephemeral containers

Ephemeral containers are useful for interactive troubleshooting when `kubectl
exec` is insufficient because a container has crashed or a container image
doesn't include debugging utilities.

In particular, [distroless images](https://github.com/GoogleContainerTools/distroless)
enable you to deploy minimal container images that reduce attack surface
and exposure to bugs and vulnerabilities. Since distroless images do not include a
shell or any debugging utilities, it's difficult to troubleshoot distroless
images using `kubectl exec` alone.

When using ephemeral containers, it's helpful to enable [process namespace
sharing](/docs/tasks/configure-pod-container/share-process-namespace/) so
you can view processes in other containers.

## ## What's next

* Learn how to [debug pods using ephemeral containers](/docs/tasks/debug/debug-application/debug-running-pod/#ephemeral-container).
