---
title: "RBAC Best Practices for Charts"
category: "security"
component: "rbac"
helm_version: "4.3.1"
source_repository: "https://github.com/helm/helm-www"
source_ref: "main"
source_commit: "60d4db89421147aec9fc3fcef9e2d49e2729f50b"
source_url: "https://helm.sh/docs/chart_best_practices/rbac/"
source_version: "4.3.1"
retrieved_at: "2026-11-08T04:55:44.197718+00:00"
spdx_license: "CC-BY-4.0"
attribution: "Copyright The Helm Authors"
content_hash: "f1231175e27c8aeb4dc662b9ebf87d22efdb075165dcb0674a2bce5bc5305499"
document_type: "guide"
doc_type: "guide"
---

This part of the Best Practices Guide discusses the creation and formatting of
RBAC resources in chart manifests.

RBAC resources are:

- ServiceAccount (namespaced)
- Role (namespaced)
- ClusterRole
- RoleBinding (namespaced)
- ClusterRoleBinding

## YAML Configuration

RBAC and ServiceAccount configuration should happen under separate keys. They
are separate things. Splitting these two concepts out in the YAML disambiguates
them and makes this clearer.

```yaml
rbac:
  # Specifies whether RBAC resources should be created
  create: true

serviceAccount:
  # Specifies whether a ServiceAccount should be created
  create: true
  # The name of the ServiceAccount to use.
  # If not set and create is true, a name is generated using the fullname template
  name:
```

This structure can be extended for more complex charts that require multiple
ServiceAccounts.

```yaml
someComponent:
  serviceAccount:
    create: true
    name:
anotherComponent:
  serviceAccount:
    create: true
    name:
```

## RBAC Resources Should be Created by Default

`rbac.create` should be a boolean value controlling whether RBAC resources are
created.  The default should be `true`.  Users who wish to manage RBAC access
controls themselves can set this value to `false` (in which case see below).

## Using RBAC Resources

`serviceAccount.name` should be set to the name of the ServiceAccount to be used
by access-controlled resources created by the chart.  If `serviceAccount.create`
is true, then a ServiceAccount with this name should be created.  If the name is
not set, then a name is generated using the `fullname` template, If
`serviceAccount.create` is false, then it should not be created, but it should
still be associated with the same resources so that manually-created RBAC
resources created later that reference it will function correctly.  If
`serviceAccount.create` is false and the name is not specified, then the default
ServiceAccount is used.

The following helper template should be used for the ServiceAccount.

```yaml
{{/*
Create the name of the service account to use
*/}}
{{- define "mychart.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
    {{ default (include "mychart.fullname" .) .Values.serviceAccount.name }}
{{- else -}}
    {{ default "default" .Values.serviceAccount.name }}
{{- end -}}
{{- end -}}
```
