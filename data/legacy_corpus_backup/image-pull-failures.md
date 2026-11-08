---
title: Runbook - ImagePullBackOff and ErrImagePull
doc_type: runbook
component: containerd
severity: medium
---

# ImagePullBackOff and ErrImagePull

ErrImagePull means a pull attempt failed; ImagePullBackOff means the kubelet is
now backing off between retries. The underlying error is in the pod events, and
it is almost always one of four things: the tag does not exist, credentials are
missing or wrong, the registry is unreachable, or the platform does not match.

`kubectl describe pod <pod> -n <namespace> | grep -A10 Events`

## Error message to cause

- `manifest unknown` or `not found`: the tag or digest does not exist in the
  repository. Verify with `crane manifest <image>` or the registry UI. A common
  cause is a CI pipeline that pushed to a different repository path.
- `unauthorized: authentication required`: no imagePullSecret matched, or the
  secret is in the wrong namespace. imagePullSecrets are namespace-scoped and
  must be created in every namespace that pulls the image.
- `pull access denied`: the credential is valid but lacks read scope on that
  repository.
- `dial tcp: i/o timeout` or `connection refused`: network path to the registry
  is blocked. Check egress NetworkPolicy, the node's route to the registry, and
  any proxy configuration in the containerd config.
- `no match for platform in manifest`: the image has no build for the node's
  architecture. Frequent when arm64 nodes are added to an amd64 cluster.
- `toomanyrequests`: registry rate limit. Authenticate the pull even for public
  images, and consider a pull-through cache.

## Verifying credentials

```
kubectl get secret <secret> -n <namespace> -o jsonpath='{.data.\.dockerconfigjson}' | base64 -d
kubectl get serviceaccount default -n <namespace> -o yaml
```

The secret must be of type `kubernetes.io/dockerconfigjson`, and it must be
referenced either in the pod spec's imagePullSecrets or attached to the pod's
ServiceAccount. A generic Opaque secret with the same content will not work.

## imagePullPolicy pitfalls

With `imagePullPolicy: IfNotPresent` and a mutable tag such as `latest`, nodes
that already cached the tag keep running old code while new nodes pull the new
image, producing a cluster where behaviour depends on which node served the
request. Pin images by digest, or use immutable tags plus
`imagePullPolicy: IfNotPresent`.

The default policy is Always for the `latest` tag and IfNotPresent for any other
tag, which surprises teams who assume Always everywhere.

## Node-level check

If one node fails to pull an image that others pull successfully, the problem is
on that node. Check disk pressure (a full image filesystem fails pulls), the
containerd service status, and the node's registry credentials if they are
provided by an instance role rather than a secret.
