---
title: "Controllers"
category: "architecture"
component: "kube-controller-manager"
kubernetes_version: "1.37"
source_repository: "https://github.com/kubernetes/website"
source_ref: "dev-1.37"
source_commit: "aebe074739e78e9111943ac285df6ae3a6319f69"
source_url: "https://kubernetes.io/docs/concepts/architecture/controller/"
source_version: "1.37"
retrieved_at: "2026-11-07T15:52:17.115439+00:00"
spdx_license: "CC-BY-4.0"
attribution: "Copyright The Kubernetes Authors"
content_hash: "5167ce8bc6775450e607c425634d6caf811082187ba678dbe3adf15c3fef3604"
document_type: "concept"
doc_type: "concept"
---

In robotics and automation, a _control loop_ is
a non-terminating loop that regulates the state of a system.

Here is one example of a control loop: a thermostat in a room.

When you set the temperature, that's telling the thermostat
about your *desired state*. The actual room temperature is the
*current state*. The thermostat acts to bring the current state
closer to the desired state, by turning equipment on or off.

## Controller pattern

A controller tracks at least one Kubernetes resource type.
These objects
have a spec field that represents the desired state. The
controller(s) for that resource are responsible for making the current
state come closer to that desired state.

The controller might carry the action out itself; more commonly, in Kubernetes,
a controller will send messages to the
API server that have
useful side effects. You'll see examples of this below.

Some built-in controllers, such as the namespace controller, act on objects
that do not have a spec. For simplicity, this page omits explaining that
detail.

### Control via API server

The control plane
act on the new information (there are new Pods to schedule and run),
and eventually the work is done.

After you create a new Job, the desired state is for that Job to be completed.
The Job controller makes the current state for that Job be nearer to your
desired state: creating Pods that do the work you wanted for that Job, so that
the Job is closer to completion.

Controllers also update the objects that configure them.
For example: once the work is done for a Job, the Job controller
updates that Job object to mark it `Finished`.

(This is a bit like how some thermostats turn a light off to
indicate that your room is now at the temperature you set).

### Direct control

In contrast with Job, some controllers need to make changes to
things outside of your cluster.

For example, if you use a control loop to make sure there
are enough Nodes
in your cluster, then that controller needs something outside the
current cluster to set up new Nodes when needed.

Controllers that interact with external state find their desired state from
the API server, then communicate directly with an external system to bring
the current state closer in line.

(There actually is a [controller](https://github.com/kubernetes/autoscaler/)
that horizontally scales the nodes in your cluster.)

The important point here is that the controller makes some changes to bring about
your desired state, and then reports the current state back to your cluster's API server.
Other control loops can observe that reported data and take their own actions.

In the thermostat example, if the room is very cold then a different controller
might also turn on a frost protection heater. With Kubernetes clusters, the control
plane indirectly works with IP address management tools, storage services,
cloud provider APIs, and other services by
[extending Kubernetes](/docs/concepts/extend-kubernetes/) to implement that.

## Desired versus current state {#desired-vs-current}

Kubernetes takes a cloud-native view of systems, and is able to handle
constant change.

Your cluster could be changing at any point as work happens and
control loops automatically fix failures. This means that,
potentially, your cluster never reaches a stable state.

As long as the controllers for your cluster are running and able to make
useful changes, it doesn't matter if the overall state is stable or not.

## Design

As a tenet of its design, Kubernetes uses lots of controllers that each manage
a particular aspect of cluster state. Most commonly, a particular control loop
(controller) uses one kind of resource as its desired state, and has a different
kind of resource that it manages to make that desired state happen. For example,
a controller for Jobs tracks Job objects (to discover new work) and Pod objects
(to run the Jobs, and then to see when the work is finished). In this case
something else creates the Jobs, whereas the Job controller creates Pods.

It's useful to have simple controllers rather than one, monolithic set of control
loops that are interlinked. Controllers can fail, so Kubernetes is designed to
allow for that.

> **Note:** 
There can be several controllers that create or update the same kind of object.
Behind the scenes, Kubernetes controllers make sure that they only pay attention
to the resources linked to their controlling resource.

For example, you can have Deployments and Jobs; these both create Pods.
The Job controller does not delete the Pods that your Deployment created,
because there is information (labels)
the controllers can use to tell those Pods apart.

## Ways of running controllers {#running-controllers}

Kubernetes comes with a set of built-in controllers that run inside
the kube controller manager. These
built-in controllers provide important core behaviors.

The Deployment controller and Job controller are examples of controllers that
come as part of Kubernetes itself ("built-in" controllers).
Kubernetes lets you run a resilient control plane, so that if any of the built-in
controllers were to fail, another part of the control plane will take over the work.

You can find controllers that run outside the control plane, to extend Kubernetes.
Or, if you want, you can write a new controller yourself.
You can run your own controller as a set of Pods,
or externally to Kubernetes. What fits best will depend on what that particular
controller does.

## ## What's next

* Read about the [Kubernetes control plane](/docs/concepts/architecture/#control-plane-components)
* Discover some of the basic [Kubernetes objects](/docs/concepts/overview/working-with-objects/)
* Learn more about the [Kubernetes API](/docs/concepts/overview/kubernetes-api/)
* If you want to write your own controller, see
  [Kubernetes extension patterns](/docs/concepts/extend-kubernetes/#extension-patterns)
  and the [sample-controller](https://github.com/kubernetes/sample-controller) repository.
