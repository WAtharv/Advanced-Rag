---
title: "StatefulSets"
category: "workloads"
component: "kube-controller-manager"
source_url: "https://kubernetes.io/docs/concepts/workloads/controllers/statefulset/"
source_repository: "https://github.com/kubernetes/website"
source_version: "release-1.37"
retrieved_at: "2026-11-07T15:52:17.115439+00:00"
spdx_license: "CC-BY-4.0"
attribution: "Copyright The Kubernetes Authors"
content_hash: "e4831f08c5fbb11c93d9623f5df0b26a536f241104d081395ef97d3b6ecdbf7c"
document_type: "concept"
doc_type: "concept"
---

StatefulSet is the workload API object used to manage stateful applications.

## Using StatefulSets

StatefulSets are valuable for applications that require one or more of the
following:

* Stable, unique network identifiers.
* Stable, persistent storage.
* Ordered, graceful deployment and scaling.
* Ordered, automated rolling updates.

In the above, stable is synonymous with persistence across Pod (re)scheduling.
If an application doesn't require any stable identifiers or ordered deployment,
deletion, or scaling, you should deploy your application using a workload object
that provides a set of stateless replicas.
[Deployment](/docs/concepts/workloads/controllers/deployment/) or
[ReplicaSet](/docs/concepts/workloads/controllers/replicaset/) may be better suited to your stateless needs.

## Limitations

* The storage for a given Pod must either be provisioned by a
  [PersistentVolume Provisioner](/docs/concepts/storage/dynamic-provisioning/)
  based on the requested _storage class_, or pre-provisioned by an admin.
* Deleting and/or scaling a StatefulSet down will _not_ delete the volumes associated with the
  StatefulSet. This is done to ensure data safety, which is generally more valuable than an
  automatic purge of all related StatefulSet resources.
* StatefulSets currently require a [Headless Service](/docs/concepts/services-networking/service/#headless-services)
  to be responsible for the network identity of the Pods. You are responsible for creating this
  Service.
* StatefulSets do not provide any guarantees on the termination of pods when a StatefulSet is
  deleted. To achieve ordered and graceful termination of the pods in the StatefulSet, it is
  possible to scale the StatefulSet down to 0 prior to deletion.
* When using [Rolling Updates](#rolling-updates) with the default
  [Pod Management Policy](#pod-management-policies) (`OrderedReady`),
  it's possible to get into a broken state that requires
  [manual intervention to repair](#forced-rollback).

## Components

The example below demonstrates the components of a StatefulSet.

```yaml
apiVersion: v1
kind: Service
metadata:
  name: nginx
  labels:
    app: nginx
spec:
  ports:
  - port: 80
    name: web
  clusterIP: None
  selector:
    app: nginx
---
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: web
spec:
  selector:
    matchLabels:
      app: nginx # has to match .spec.template.metadata.labels
  serviceName: "nginx"
  replicas: 3 # by default is 1
  minReadySeconds: 10 # by default is 0
  template:
    metadata:
      labels:
        app: nginx # has to match .spec.selector.matchLabels
    spec:
      terminationGracePeriodSeconds: 10
      containers:
      - name: nginx
        image: registry.k8s.io/nginx-slim:0.24
        ports:
        - containerPort: 80
          name: web
        volumeMounts:
        - name: www
          mountPath: /usr/share/nginx/html
  volumeClaimTemplates:
  - metadata:
      name: www
    spec:
      accessModes: [ "ReadWriteOnce" ]
      storageClassName: "my-storage-class"
      resources:
        requests:
          storage: 1Gi
```

> **Note:** 
This example uses the `ReadWriteOnce` access mode, for simplicity. For
production use, the Kubernetes project recommends using the `ReadWriteOncePod`
access mode instead.

In the above example:

* A Headless Service, named `nginx`, is used to control the network domain.
* The StatefulSet, named `web`, has a Spec that indicates that 3 replicas of the nginx container will be launched in unique Pods.
* The `volumeClaimTemplates` will provide stable storage using
  [PersistentVolumes](/docs/concepts/storage/persistent-volumes/) provisioned by a
  PersistentVolume Provisioner.

The name of a StatefulSet object must be a valid
[DNS label](/docs/concepts/overview/working-with-objects/names#dns-label-names).

### Pod Selector

You must set the `.spec.selector` field of a StatefulSet to match the labels of its
`.spec.template.metadata.labels`. Failing to specify a matching Pod Selector will result in a
validation error during StatefulSet creation.

### Volume Claim Templates

You can set the `.spec.volumeClaimTemplates` field to create a
[PersistentVolumeClaim](/docs/concepts/storage/persistent-volumes/#persistentvolumeclaims). 
This will provide stable storage to the StatefulSet if either:

* The StorageClass specified for the volume claim is set up to use [dynamic
  provisioning](/docs/concepts/storage/dynamic-provisioning/).
* The cluster already contains a PersistentVolume with the correct StorageClass
  and sufficient available storage space.

### Minimum ready seconds

*(Feature state: stable in v1.25)*

`.spec.minReadySeconds` is an optional field that specifies the minimum number of seconds for which a newly
created Pod should be running and ready without any of its containers crashing, for it to be considered available.
This is used to check progression of a rollout when using a [Rolling Update](#rolling-updates) strategy.
This field defaults to 0 (the Pod will be considered available as soon as it is ready). To learn more about when
a Pod is considered ready, see [Container Probes](/docs/concepts/workloads/pods/pod-lifecycle/#container-probes).

## Pod Identity

StatefulSet Pods have a unique identity that consists of an ordinal, a
stable network identity, and stable storage. The identity sticks to the Pod,
regardless of which node it's (re)scheduled on.

### Ordinal Index

For a StatefulSet with N [replicas](#replicas), each Pod in the StatefulSet
will be assigned an integer ordinal, that is unique over the Set. By default,
pods will be assigned ordinals from 0 up through N-1. The StatefulSet controller
will also add a pod label with this index: `apps.kubernetes.io/pod-index`.

### Start ordinal

*(Feature state: beta in v1.35)*

You can control the maximum number of Pods that can be unavailable during an update
by specifying the `.spec.updateStrategy.rollingUpdate.maxUnavailable` field.
The value can be an absolute number (for example, `5`) or a percentage of desired
Pods (for example, `10%`). Absolute number is calculated from the percentage value
by rounding it up. This field cannot be 0. The default setting is 1.

This field applies to all Pods in the range `0` to `replicas - 1`. If there is any
unavailable Pod in the range `0` to `replicas - 1`, it will be counted towards
`maxUnavailable`.

> **Note:** 
The `maxUnavailable` field is in Beta stage and it is enabled by default.

### Forced rollback

When using [Rolling Updates](#rolling-updates) with the default
[Pod Management Policy](#pod-management-policies) (`OrderedReady`),
it's possible to get into a broken state that requires manual intervention to repair.

If you update the Pod template to a configuration that never becomes Running and
Ready (for example, due to a bad binary or application-level configuration error),
StatefulSet will stop the rollout and wait.

In this state, it's not enough to revert the Pod template to a good configuration.
Due to a [known issue](https://github.com/kubernetes/kubernetes/issues/67250),
StatefulSet will continue to wait for the broken Pod to become Ready
(which never happens) before it will attempt to revert it back to the working
configuration.

After reverting the template, you must also delete any Pods that StatefulSet had
already attempted to run with the bad configuration.
StatefulSet will then begin to recreate the Pods using the reverted template.

## Recreate

When a StatefulSet's `.spec.updateStrategy.type` is set to `Recreate`, the StatefulSet
controller deletes all of the StatefulSet's Pods at once and waits for them to terminate
completely before creating any new Pods from the updated `.spec.template`. Unlike
[`RollingUpdate`](#rolling-updates), the old and new revisions of a Pod are never running at
the same time, so this strategy incurs downtime for the duration of the update. This mirrors
the `Recreate` strategy of Deployments and is useful for applications that cannot run two
versions concurrently, such as workloads that require exclusive access to a shared resource
or that use an on-disk format that is incompatible between versions.

The deletion always removes every Pod together, regardless of the
[Pod Management Policy](#pod-management-policies). Once all Pods have terminated, the new
Pods are created according to that policy: with `OrderedReady` (the default) the Pods are
recreated one at a time, in ascending ordinal order, waiting for each to become Running and
Ready before creating the next; with `Parallel` all of the Pods are recreated at once.

Because this is an alpha feature, you must enable the `StatefulSetRecreateStrategy`
[feature gate](/docs/reference/command-line-tools-reference/feature-gates/#StatefulSetRecreateStrategy)
on the kube-controller-manager and the kube-apiserver to use this strategy.

## Revision history

ControllerRevision is a Kubernetes API resource used by controllers, such as the StatefulSet controller, to track historical configuration changes.

StatefulSets use ControllerRevisions to maintain a revision history, enabling rollbacks and version tracking.

### How StatefulSets track changes using ControllerRevisions

When you update a StatefulSet's Pod template (`spec.template`), the StatefulSet controller:

1. Prepares a new ControllerRevision object  
2. Stores a snapshot of the Pod template and metadata  
3. Assigns an incremental revision number  

#### Key Properties

See [ControllerRevision](/docs/reference/kubernetes-api/workload-resources/controller-revision-v1/) to learn more about key properties and other details. 

---

### Managing Revision History

Control retained revisions with `.spec.revisionHistoryLimit`:

```yaml
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: webapp
spec:
  revisionHistoryLimit: 5  # Keep last 5 revisions
  # ... other spec fields ...
```

* **Default**: 10 revisions retained if unspecified  
* **Cleanup**: Oldest revisions are garbage-collected when exceeding the limit

#### Performing Rollbacks

You can revert to a previous configuration using:

```bash
# View revision history
kubectl rollout history statefulset/webapp

# Rollback to a specific revision
kubectl rollout undo statefulset/webapp --to-revision=3
```

This will:

* Apply the Pod template from revision 3  
* Create a new ControllerRevision with an updated revision number  

#### Inspecting ControllerRevisions

To view associated ControllerRevisions:

```bash
# List all revisions for the StatefulSet
kubectl get controllerrevisions -l app.kubernetes.io/name=webapp

# View detailed configuration of a specific revision
kubectl get controllerrevision/webapp-3 -o yaml
```

#### Best Practices

##### Retention Policy

- Set `revisionHistoryLimit` between **5–10** for most workloads.  
- Increase only if **deep rollback history** is required.

##### Monitoring

* Regularly check revisions with:

  ```bash
  kubectl get controllerrevisions
  ```

- Alert on **rapid revision count growth**.

##### Avoid

* Manual edits to ControllerRevision objects.  
* Using revisions as a backup mechanism (use actual backup tools).
* Setting `revisionHistoryLimit: 0` (disables rollback capability).

## PersistentVolumeClaim retention

The optional `.spec.persistentVolumeClaimRetentionPolicy` field controls if
and how PVCs are deleted during the lifecycle of a StatefulSet. You must enable the
`StatefulSetAutoDeletePVC` [feature gate](/docs/reference/command-line-tools-reference/feature-gates/)
on the API server and the controller manager to use this field.
Once enabled, there are two policies you can configure for each StatefulSet:

`whenDeleted`
: Configures the volume retention behavior that applies when the StatefulSet is deleted.

`whenScaled`
: Configures the volume retention behavior that applies when the replica count of
  the StatefulSet   is reduced; for example, when scaling down the set.
  
For each policy that you can configure, you can set the value to either `Delete` or `Retain`.

`Delete`
: The PVCs created from the StatefulSet `volumeClaimTemplate` are deleted for each Pod
  affected by the policy. With the `whenDeleted` policy all PVCs from the
  `volumeClaimTemplate` are deleted after their Pods have been deleted. With the
  `whenScaled` policy, only PVCs corresponding to Pod replicas being scaled down are
  deleted, after their Pods have been deleted.

`Retain` (default)
: PVCs from the `volumeClaimTemplate` are not affected when their Pod is
  deleted. This is the behavior before this new feature.

Bear in mind that these policies **only** apply when Pods are being removed due to the
StatefulSet being deleted or scaled down. For example, if a Pod associated with a StatefulSet
fails due to node failure, and the control plane creates a replacement Pod, the StatefulSet
retains the existing PVC.  The existing volume is unaffected, and the cluster will attach it to
the node where the new Pod is about to launch.
  
The default for policies is `Retain`, matching the StatefulSet behavior before this new feature.

Here is an example policy:

```yaml
apiVersion: apps/v1
kind: StatefulSet
...
spec:
  persistentVolumeClaimRetentionPolicy:
    whenDeleted: Retain
    whenScaled: Delete
...
```

The StatefulSet controller adds
[owner references](/docs/concepts/overview/working-with-objects/owners-dependents/#owner-references-in-object-specifications)
to its PVCs, which are then deleted by the garbage collector after the Pod is terminated. This enables the Pod to
cleanly unmount all volumes before the PVCs are deleted (and before the backing PV and
volume are deleted, depending on the retain policy).  When you set the `whenDeleted`
policy to `Delete`, an owner reference to the StatefulSet instance is placed on all PVCs
associated with that StatefulSet.

The `whenScaled` policy must delete PVCs only when a Pod is scaled down, and not when a
Pod is deleted for another reason. When reconciling, the StatefulSet controller compares
its desired replica count to the actual Pods present on the cluster. Any StatefulSet Pod
whose id greater than the replica count is condemned and marked for deletion. If the
`whenScaled` policy is `Delete`, the condemned Pods are first set as owners to the
associated StatefulSet template PVCs, before the Pod is deleted. This causes the PVCs
to be garbage collected after only the condemned Pods have terminated.

This means that if the controller crashes and restarts, no Pod will be deleted before its
owner reference has been updated appropriate to the policy. If a condemned Pod is
force-deleted while the controller is down, the owner reference may or may not have been
set up, depending on when the controller crashed. It may take several reconcile loops to
update the owner references, so some condemned Pods may have set up owner references and
others may not. For this reason we recommend waiting for the controller to come back up,
which will verify owner references before terminating Pods. If that is not possible, the
operator should verify the owner references on PVCs to ensure the expected objects are
deleted when Pods are force-deleted.

### Replicas

`.spec.replicas` is an optional field that specifies the number of desired Pods. It defaults to 1.

Should you manually scale a StatefulSet, via `kubectl scale
statefulset statefulset --replicas=X`, and then you update that StatefulSet
based on a manifest (for example: by running `kubectl apply -f
statefulset.yaml`), then applying that manifest overwrites the manual scaling
that you previously did.

If a [HorizontalPodAutoscaler](/docs/concepts/workloads/autoscaling/horizontal-pod-autoscale/)
(or any similar API for horizontal scaling) is managing scaling for a
Statefulset, don't set `.spec.replicas`. Instead, allow the Kubernetes
control plane to manage
the `.spec.replicas` field automatically.

## ## What's next

* Learn about [Pods](/docs/concepts/workloads/pods).
* Find out how to use StatefulSets
  * Follow an example of [deploying a stateful application](/docs/tutorials/stateful-application/basic-stateful-set/).
  * Follow an example of [deploying Cassandra with Stateful Sets](/docs/tutorials/stateful-application/cassandra/).
  * Follow an example of [running a replicated stateful application](/docs/tasks/run-application/run-replicated-stateful-application/).
  * Learn how to [scale a StatefulSet](/docs/tasks/run-application/scale-stateful-set/).
  * Learn what's involved when you [delete a StatefulSet](/docs/tasks/run-application/delete-stateful-set/).
  * Learn how to [configure a Pod to use a volume for storage](/docs/tasks/configure-pod-container/configure-volume-storage/).
  * Learn how to [configure a Pod to use a PersistentVolume for storage](/docs/tutorials/configuration/configure-persistent-volume-storage/).
* `StatefulSet` is a top-level resource in the Kubernetes REST API.
  Read the 
  object definition to understand the API for stateful sets.
* Read about [PodDisruptionBudget](/docs/concepts/workloads/pods/disruptions/) and how
  you can use it to manage application availability during disruptions.
