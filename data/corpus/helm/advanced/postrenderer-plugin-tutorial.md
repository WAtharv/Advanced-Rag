---
title: "Developing Post-Renderer Plugins"
category: "advanced"
component: "post-rendering"
helm_version: "4.3.1"
source_repository: "https://github.com/helm/helm-www"
source_ref: "main"
source_commit: "60d4db89421147aec9fc3fcef9e2d49e2729f50b"
source_url: "https://helm.sh/docs/plugins/developer/tutorial-postrenderer-plugin/"
source_version: "4.3.1"
retrieved_at: "2026-11-08T04:55:44.197718+00:00"
spdx_license: "CC-BY-4.0"
attribution: "Copyright The Helm Authors"
content_hash: "65d7e0cf89fd9dab7acd55a97c1c9a8511f2832070f1d940f807ee2347a844fb"
document_type: "guide"
doc_type: "guide"
---

Build a plugin that adds custom labels to all Kubernetes resources.

## Subprocess Runtime

Let's start by building a Postrenderer Plugin with the Subprocess runtime.

### Prerequisites

1. Install the latest Helm 4 release: **v4.3.1**
2. In your terminal session, alias `helm` to the release you downloaded. `helm version --short` should now show the correct Helm version in this terminal session.
3. Install `mikefarah/yq`: https://github.com/mikefarah/yq/#install

### 1. Create Plugin Directory

You can create this anywhere on your filesystem. For example:

```bash
mkdir -p $HOME/code/helm/plugins/label-injector
cd $HOME/code/helm/plugins/label-injector
```

### 2. Create plugin manifest

```yaml title="plugin.yaml" showLineNumbers
apiVersion: v1
type: postrenderer/v1
name: label-injector
version: 0.1.0
runtime: subprocess
runtimeConfig:
  platformCommand:
    - command: ${HELM_PLUGIN_DIR}/inject-labels.sh
```

### 3. Create Script

```bash title="inject-labels.sh" showLineNumbers
#!/usr/bin/env sh
# set -e
cat <&0 | yq '.metadata.labels.postrendered-by = "helm-label-injector-plugin"'
```

Make it executable:

```bash
chmod +x inject-labels.sh
```

### 4. Install in Dev Mode and Test

Installing a plugin from your filesystem installs in local dev mode, which does not check the provenance:

```bash
% helm plugin install $HOME/code/helm/plugins/label-injector
Installing plugin from local directory (development mode)
Installed plugin: label-injector
```

As we saw in the [CLI Plugin](/plugins/developer/tutorial-cli-plugin.mdx) and [Getter Plugin](/plugins/developer/tutorial-getter-plugin.mdx) tutorials, local dev mode installation creates a symlink from your plugin source directory to the plugins directory. You now have three plugins installed:

```bash
% ls -lah $(helm env HELM_PLUGINS)
total 0
drwxr-xr-x@ 5 r6by  staff   160B Nov 10 04:04 .
drwxr-xr-x@ 3 r6by  staff    96B Jan 21  2025 ..
lrwxr-xr-x  1 r6by  staff    41B Nov 10 04:04 demo-getter -> /Users/r6by/code/helm/plugins/demo-getter
lrwxr-xr-x  1 r6by  staff    44B Nov 10 03:02 label-injector -> /Users/r6by/code/helm/plugins/label-injector
lrwxr-xr-x  1 r6by  staff    41B Nov 10 02:18 system-info -> /Users/r6by/code/helm/plugins/system-info
```

You can now see your installed Postrenderer plugin details along with the installed CLI and Getter plugins, using `helm plugin list`:

```bash
% helm plugin list
NAME          	VERSION	TYPE           	APIVERSION	PROVENANCE	SOURCE
demo-getter   	0.1.0  	getter/v1      	v1        	local dev 	unknown
label-injector	0.1.0  	postrenderer/v1	v1        	local dev 	unknown
system-info   	0.1.0  	cli/v1         	v1        	local dev 	unknown
```

Let's try it out:

```bash
% helm create ../mychart
% helm template ../mychart --post-renderer label-injector
```

You should see labels like this in the output:

```yaml
metadata:
  labels:
    postrendered-by: helm-label-injector-plugin
```

What you built: A Postrenderer plugin using the Subprocess runtime!

### Use kustomize as a Post-Renderer

The same pattern wraps an existing tool. This plugin runs `kustomize build` over the rendered manifests. It requires [`kustomize`](https://kubectl.docs.kubernetes.io/installation/kustomize/) on your `PATH`.

Create a new plugin directory:

```bash
mkdir -p $HOME/code/helm/plugins/kustomize-renderer
cd $HOME/code/helm/plugins/kustomize-renderer
```

```yaml title="plugin.yaml" showLineNumbers
apiVersion: v1
type: postrenderer/v1
name: kustomize-renderer
version: 0.1.0
runtime: subprocess
runtimeConfig:
  platformCommand:
    - command: ${HELM_PLUGIN_DIR}/kustomize.sh
```

The kustomization reads the rendered manifests from `all.yaml`. This example sets every Deployment to 3 replicas:

```yaml title="kustomization.yaml" showLineNumbers
resources:
  - all.yaml
patches:
  - target:
      kind: Deployment
    patch: |-
      - op: replace
        path: /spec/replicas
        value: 3
```

The script writes the manifests from STDIN into a temporary directory next to a copy of the kustomization, then builds it:

```bash title="kustomize.sh" showLineNumbers
#!/usr/bin/env sh
set -e
dir=$(mktemp -d)
trap 'rm -rf "$dir"' EXIT
cp "${HELM_PLUGIN_DIR}/kustomization.yaml" "$dir/"
cat <&0 > "$dir/all.yaml"
kustomize build "$dir"
```

Make it executable, install the plugin, and try it:

```bash
% chmod +x kustomize.sh
% helm plugin install $HOME/code/helm/plugins/kustomize-renderer
% helm template ../mychart --post-renderer kustomize-renderer
```

The Deployment in the output now has `replicas: 3`. If `kustomize build` fails, the script exits non-zero and Helm reports the error.

Let's do this one in Wasm runtime now too…

## Wasm Runtime

### Prerequisites

- Prerequisites from [Subprocess Runtime](#subprocess-runtime)
- Go 1.25 installed

> **Warning:** 
> To-do: add this section
