---
title: "Pod Security Best Practices in Charts"
category: "security"
component: "security"
helm_version: "4.3.1"
source_repository: "https://github.com/helm/helm-www"
source_ref: "main"
source_commit: "60d4db89421147aec9fc3fcef9e2d49e2729f50b"
source_url: "https://helm.sh/docs/chart_best_practices/pods/"
source_version: "4.3.1"
retrieved_at: "2026-11-08T04:55:44.197718+00:00"
spdx_license: "CC-BY-4.0"
attribution: "Copyright The Helm Authors"
content_hash: "830ef7e79c309bc8b8df307057a18ab5fe49cb356652f50d065d377b316b7c31"
document_type: "guide"
doc_type: "guide"
---

This part of the Best Practices Guide discusses formatting the Pod and
PodTemplate portions in chart manifests.

The following (non-exhaustive) list of resources use PodTemplates:

- Deployment
- ReplicationController
- ReplicaSet
- DaemonSet
- StatefulSet

## Images

A container image should use a fixed tag or the SHA of the image. It should not
use the tags `latest`, `head`, `canary`, or other tags that are designed to be
"floating".

Images _may_ be defined in the `values.yaml` file to make it easy to swap out
images.

```yaml
image: {{ .Values.redisImage | quote }}
```

An image and a tag _may_ be defined in `values.yaml` as two separate fields:

```yaml
image: "{{ .Values.redisImage }}:{{ .Values.redisTag }}"
```

## ImagePullPolicy

`helm create` sets the `imagePullPolicy` to `IfNotPresent` by default by doing
the following in your `deployment.yaml`:

```yaml
imagePullPolicy: {{ .Values.image.pullPolicy }}
```

And `values.yaml`:

```yaml
image:
  pullPolicy: IfNotPresent
```

Similarly, Kubernetes defaults the `imagePullPolicy` to `IfNotPresent` if it is
not defined at all. If you want a value other than `IfNotPresent`, simply update
the value in `values.yaml` to your desired value.

## PodTemplates Should Declare Selectors

All PodTemplate sections should specify a selector. For example:

```yaml
selector:
  matchLabels:
      app.kubernetes.io/name: MyName
template:
  metadata:
    labels:
      app.kubernetes.io/name: MyName
```

This is a good practice because it makes the relationship between the set and
the pod.

But this is even more important for sets like Deployment. Without this, the
_entire_ set of labels is used to select matching pods, and this will break if
you use labels that change, like version or release date.
