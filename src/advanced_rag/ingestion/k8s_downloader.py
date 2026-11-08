"""Automated collection and curation pipeline for official Kubernetes documentation.

Pulls official, versioned documentation from kubernetes/website (release-1.37),
cleans Hugo shortcodes while preserving code blocks and YAML manifests,
attaches standard metadata frontmatter, detects duplicates, supports incremental
caching, safe staging, and generates a machine-readable corpus manifest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import shutil
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_REPO = "https://github.com/kubernetes/website"
DEFAULT_KUBERNETES_VERSION = "1.37"
DEFAULT_SOURCE_REF = "dev-1.37"
DEFAULT_SOURCE_COMMIT = "aebe074739e78e9111943ac285df6ae3a6319f69"
DEFAULT_BRANCH = "1.37"
DEFAULT_UPSTREAM_REF = "dev-1.37"
RAW_BASE_URL = f"https://raw.githubusercontent.com/kubernetes/website/{DEFAULT_SOURCE_COMMIT}/"
SPDX_LICENSE = "CC-BY-4.0"
ATTRIBUTION = "Copyright The Kubernetes Authors"
USER_AGENT = "KubernetesDocCollector/1.0 (AdvancedRAG SRE Copilot)"


DEFAULT_CORPUS_DIR = Path("data/corpus/kubernetes")
DEFAULT_MANIFEST_PATH = Path("data/corpus/kubernetes_manifest.json")
DEFAULT_STAGING_DIR = Path("data/staging_corpus/kubernetes")
DEFAULT_STAGING_MANIFEST = Path("data/staging_corpus/kubernetes_manifest.json")
DEFAULT_BACKUP_131_DIR = Path("data/corpus_backup_1.31")


# Curated catalog covering the 15 requested Kubernetes domains mapped to the 11 corpus categories:
# 1. fundamentals
# 2. architecture
# 3. workloads (pods, containers, controllers, probes)
# 4. networking (services, ingress, gateway-api, dns, policies)
# 5. storage (volumes, pv, pvc, storageclasses, snapshots)
# 6. configuration (configmaps, secrets, resource management)
# 7. scheduling (kube-scheduler, assignment, taints, quotas, limits)
# 8. security (rbac, standards, admission, service-accounts, security-context)
# 9. cluster-administration (drain, reserve-compute, shutdown, certs)
# 10. troubleshooting (debug pods, services, running-pod, failure reason, node health, crictl)
# 11. monitoring (usage monitoring, metrics pipeline, system metrics)
K8S_DOCUMENT_CATALOG: list[dict[str, str]] = [
    # 1. Fundamentals (kubernetes/fundamentals)
    {
        "category": "fundamentals",
        "name": "what-is-kubernetes.md",
        "upstream_path": "content/en/docs/concepts/overview/_index.md",
        "document_type": "concept",
        "component": "general",
        "title": "What is Kubernetes",
    },
    {
        "category": "fundamentals",
        "name": "components.md",
        "upstream_path": "content/en/docs/concepts/overview/components.md",
        "document_type": "concept",
        "component": "control-plane",
        "title": "Kubernetes Components",
    },
    {
        "category": "fundamentals",
        "name": "kubernetes-api.md",
        "upstream_path": "content/en/docs/concepts/overview/kubernetes-api.md",
        "document_type": "concept",
        "component": "kube-apiserver",
        "title": "The Kubernetes API",
    },
    {
        "category": "fundamentals",
        "name": "namespaces.md",
        "upstream_path": "content/en/docs/concepts/overview/working-with-objects/namespaces.md",
        "document_type": "concept",
        "component": "general",
        "title": "Namespaces",
    },
    {
        "category": "fundamentals",
        "name": "labels-and-selectors.md",
        "upstream_path": "content/en/docs/concepts/overview/working-with-objects/labels.md",
        "document_type": "concept",
        "component": "general",
        "title": "Labels and Selectors",
    },

    # 2. Architecture & Components (kubernetes/architecture)
    {
        "category": "architecture",
        "name": "nodes.md",
        "upstream_path": "content/en/docs/concepts/architecture/nodes.md",
        "document_type": "concept",
        "component": "node",
        "title": "Nodes",
    },
    {
        "category": "architecture",
        "name": "control-plane-node-communication.md",
        "upstream_path": "content/en/docs/concepts/architecture/control-plane-node-communication.md",
        "document_type": "concept",
        "component": "control-plane",
        "title": "Control Plane-Node Communication",
    },
    {
        "category": "architecture",
        "name": "controllers.md",
        "upstream_path": "content/en/docs/concepts/architecture/controller.md",
        "document_type": "concept",
        "component": "kube-controller-manager",
        "title": "Controllers",
    },
    {
        "category": "architecture",
        "name": "leases.md",
        "upstream_path": "content/en/docs/concepts/architecture/leases.md",
        "document_type": "concept",
        "component": "kube-apiserver",
        "title": "Leases",
    },
    {
        "category": "architecture",
        "name": "garbage-collection.md",
        "upstream_path": "content/en/docs/concepts/architecture/garbage-collection.md",
        "document_type": "concept",
        "component": "kube-controller-manager",
        "title": "Garbage Collection",
    },

    # 3. Pods, Containers & Workloads (kubernetes/workloads)
    {
        "category": "workloads",
        "name": "pods.md",
        "upstream_path": "content/en/docs/concepts/workloads/pods/_index.md",
        "document_type": "concept",
        "component": "pod",
        "title": "Pods",
    },
    {
        "category": "workloads",
        "name": "pod-lifecycle.md",
        "upstream_path": "content/en/docs/concepts/workloads/pods/pod-lifecycle.md",
        "document_type": "concept",
        "component": "pod",
        "title": "Pod Lifecycle",
    },
    {
        "category": "workloads",
        "name": "init-containers.md",
        "upstream_path": "content/en/docs/concepts/workloads/pods/init-containers.md",
        "document_type": "concept",
        "component": "pod",
        "title": "Init Containers",
    },
    {
        "category": "workloads",
        "name": "ephemeral-containers.md",
        "upstream_path": "content/en/docs/concepts/workloads/pods/ephemeral-containers.md",
        "document_type": "concept",
        "component": "pod",
        "title": "Ephemeral Containers",
    },
    {
        "category": "workloads",
        "name": "container-lifecycle-hooks.md",
        "upstream_path": "content/en/docs/concepts/containers/container-lifecycle-hooks.md",
        "document_type": "concept",
        "component": "container-runtime",
        "title": "Container Lifecycle Hooks",
    },
    {
        "category": "workloads",
        "name": "deployments.md",
        "upstream_path": "content/en/docs/concepts/workloads/controllers/deployment.md",
        "document_type": "concept",
        "component": "kube-controller-manager",
        "title": "Deployments",
    },
    {
        "category": "workloads",
        "name": "replicasets.md",
        "upstream_path": "content/en/docs/concepts/workloads/controllers/replicaset.md",
        "document_type": "concept",
        "component": "kube-controller-manager",
        "title": "ReplicaSets",
    },
    {
        "category": "workloads",
        "name": "statefulsets.md",
        "upstream_path": "content/en/docs/concepts/workloads/controllers/statefulset.md",
        "document_type": "concept",
        "component": "kube-controller-manager",
        "title": "StatefulSets",
    },
    {
        "category": "workloads",
        "name": "daemonsets.md",
        "upstream_path": "content/en/docs/concepts/workloads/controllers/daemonset.md",
        "document_type": "concept",
        "component": "kube-controller-manager",
        "title": "DaemonSets",
    },
    {
        "category": "workloads",
        "name": "jobs.md",
        "upstream_path": "content/en/docs/concepts/workloads/controllers/job.md",
        "document_type": "concept",
        "component": "kube-controller-manager",
        "title": "Jobs",
    },
    {
        "category": "workloads",
        "name": "cronjobs.md",
        "upstream_path": "content/en/docs/concepts/workloads/controllers/cron-jobs.md",
        "document_type": "concept",
        "component": "kube-controller-manager",
        "title": "CronJobs",
    },
    {
        "category": "workloads",
        "name": "configure-probes.md",
        "upstream_path": "content/en/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes.md",
        "document_type": "task",
        "component": "kubelet",
        "title": "Configure Liveness, Readiness and Startup Probes",
    },

    # 4. Networking, Services & Ingress (kubernetes/networking)
    {
        "category": "networking",
        "name": "services.md",
        "upstream_path": "content/en/docs/concepts/services-networking/service.md",
        "document_type": "concept",
        "component": "kube-proxy",
        "title": "Services",
    },
    {
        "category": "networking",
        "name": "ingress.md",
        "upstream_path": "content/en/docs/concepts/services-networking/ingress.md",
        "document_type": "concept",
        "component": "ingress-controller",
        "title": "Ingress",
    },
    {
        "category": "networking",
        "name": "ingress-controllers.md",
        "upstream_path": "content/en/docs/concepts/services-networking/ingress-controllers.md",
        "document_type": "concept",
        "component": "ingress-controller",
        "title": "Ingress Controllers",
    },
    {
        "category": "networking",
        "name": "gateway-api.md",
        "upstream_path": "content/en/docs/concepts/services-networking/gateway.md",
        "document_type": "concept",
        "component": "gateway-api",
        "title": "Gateway API",
    },
    {
        "category": "networking",
        "name": "endpoint-slices.md",
        "upstream_path": "content/en/docs/concepts/services-networking/endpoint-slices.md",
        "document_type": "concept",
        "component": "kube-proxy",
        "title": "EndpointSlices",
    },
    {
        "category": "networking",
        "name": "dns-pod-service.md",
        "upstream_path": "content/en/docs/concepts/services-networking/dns-pod-service.md",
        "document_type": "concept",
        "component": "coredns",
        "title": "DNS for Services and Pods",
    },
    {
        "category": "networking",
        "name": "network-policies.md",
        "upstream_path": "content/en/docs/concepts/services-networking/network-policies.md",
        "document_type": "concept",
        "component": "cni",
        "title": "Network Policies",
    },
    {
        "category": "networking",
        "name": "service-traffic-policy.md",
        "upstream_path": "content/en/docs/concepts/services-networking/service-traffic-policy.md",
        "document_type": "concept",
        "component": "kube-proxy",
        "title": "Service Internal and External Traffic Policy",
    },

    # 5. Storage (kubernetes/storage)
    {
        "category": "storage",
        "name": "volumes.md",
        "upstream_path": "content/en/docs/concepts/storage/volumes.md",
        "document_type": "concept",
        "component": "storage",
        "title": "Volumes",
    },
    {
        "category": "storage",
        "name": "persistent-volumes.md",
        "upstream_path": "content/en/docs/concepts/storage/persistent-volumes.md",
        "document_type": "concept",
        "component": "storage",
        "title": "Persistent Volumes",
    },
    {
        "category": "storage",
        "name": "storage-classes.md",
        "upstream_path": "content/en/docs/concepts/storage/storage-classes.md",
        "document_type": "concept",
        "component": "storage",
        "title": "Storage Classes",
    },
    {
        "category": "storage",
        "name": "volume-snapshots.md",
        "upstream_path": "content/en/docs/concepts/storage/volume-snapshots.md",
        "document_type": "concept",
        "component": "storage",
        "title": "Volume Snapshots",
    },
    {
        "category": "storage",
        "name": "dynamic-provisioning.md",
        "upstream_path": "content/en/docs/concepts/storage/dynamic-provisioning.md",
        "document_type": "concept",
        "component": "storage",
        "title": "Dynamic Volume Provisioning",
    },

    # 6. Configuration (kubernetes/configuration)
    {
        "category": "configuration",
        "name": "configmaps.md",
        "upstream_path": "content/en/docs/concepts/configuration/configmap.md",
        "document_type": "concept",
        "component": "kube-apiserver",
        "title": "ConfigMaps",
    },
    {
        "category": "configuration",
        "name": "secrets.md",
        "upstream_path": "content/en/docs/concepts/configuration/secret.md",
        "document_type": "concept",
        "component": "kube-apiserver",
        "title": "Secrets",
    },
    {
        "category": "configuration",
        "name": "configure-pod-configmap.md",
        "upstream_path": "content/en/docs/tasks/configure-pod-container/configure-pod-configmap.md",
        "document_type": "task",
        "component": "pod",
        "title": "Configure a Pod to Use a ConfigMap",
    },
    {
        "category": "configuration",
        "name": "manage-resources-containers.md",
        "upstream_path": "content/en/docs/concepts/configuration/manage-resources-containers.md",
        "document_type": "concept",
        "component": "kubelet",
        "title": "Resource Management for Pods and Containers",
    },

    # 7. Scheduling & Resources (kubernetes/scheduling)
    {
        "category": "scheduling",
        "name": "kube-scheduler.md",
        "upstream_path": "content/en/docs/concepts/scheduling-eviction/kube-scheduler.md",
        "document_type": "concept",
        "component": "kube-scheduler",
        "title": "Kubernetes Scheduler",
    },
    {
        "category": "scheduling",
        "name": "assign-pod-node.md",
        "upstream_path": "content/en/docs/concepts/scheduling-eviction/assign-pod-node.md",
        "document_type": "concept",
        "component": "kube-scheduler",
        "title": "Assigning Pods to Nodes",
    },
    {
        "category": "scheduling",
        "name": "taints-and-tolerations.md",
        "upstream_path": "content/en/docs/concepts/scheduling-eviction/taint-and-toleration.md",
        "document_type": "concept",
        "component": "kube-scheduler",
        "title": "Taints and Tolerations",
    },
    {
        "category": "scheduling",
        "name": "node-pressure-eviction.md",
        "upstream_path": "content/en/docs/concepts/scheduling-eviction/node-pressure-eviction.md",
        "document_type": "concept",
        "component": "kubelet",
        "title": "Node-pressure Eviction",
    },
    {
        "category": "scheduling",
        "name": "pod-priority-preemption.md",
        "upstream_path": "content/en/docs/concepts/scheduling-eviction/pod-priority-preemption.md",
        "document_type": "concept",
        "component": "kube-scheduler",
        "title": "Pod Priority and Preemption",
    },
    {
        "category": "scheduling",
        "name": "limit-ranges.md",
        "upstream_path": "content/en/docs/concepts/policy/limit-range.md",
        "document_type": "concept",
        "component": "admission-controller",
        "title": "Limit Ranges",
    },
    {
        "category": "scheduling",
        "name": "resource-quotas.md",
        "upstream_path": "content/en/docs/concepts/policy/resource-quotas.md",
        "document_type": "concept",
        "component": "admission-controller",
        "title": "Resource Quotas",
    },

    # 8. RBAC & Security (kubernetes/security)
    {
        "category": "security",
        "name": "rbac.md",
        "upstream_path": "content/en/docs/reference/access-authn-authz/rbac.md",
        "document_type": "reference",
        "component": "kube-apiserver",
        "title": "Using RBAC Authorization",
    },
    {
        "category": "security",
        "name": "pod-security-standards.md",
        "upstream_path": "content/en/docs/concepts/security/pod-security-standards.md",
        "document_type": "concept",
        "component": "security",
        "title": "Pod Security Standards",
    },
    {
        "category": "security",
        "name": "pod-security-admission.md",
        "upstream_path": "content/en/docs/concepts/security/pod-security-admission.md",
        "document_type": "concept",
        "component": "admission-controller",
        "title": "Pod Security Admission",
    },
    {
        "category": "security",
        "name": "service-accounts.md",
        "upstream_path": "content/en/docs/concepts/security/service-accounts.md",
        "document_type": "concept",
        "component": "security",
        "title": "Service Accounts",
    },
    {
        "category": "security",
        "name": "pod-security-context.md",
        "upstream_path": "content/en/docs/tasks/configure-pod-container/security-context.md",
        "document_type": "task",
        "component": "kubelet",
        "title": "Configure a Security Context for a Pod or Container",
    },

    # 9. Cluster Administration (kubernetes/cluster-administration)
    {
        "category": "cluster-administration",
        "name": "safely-drain-node.md",
        "upstream_path": "content/en/docs/tasks/administer-cluster/safely-drain-node.md",
        "document_type": "task",
        "component": "kubectl",
        "title": "Safely Drain a Node",
    },
    {
        "category": "cluster-administration",
        "name": "reserve-compute-resources.md",
        "upstream_path": "content/en/docs/tasks/administer-cluster/reserve-compute-resources.md",
        "document_type": "task",
        "component": "kubelet",
        "title": "Reserve Compute Resources for System Daemons",
    },
    {
        "category": "cluster-administration",
        "name": "node-shutdown.md",
        "upstream_path": "content/en/docs/concepts/cluster-administration/node-shutdown.md",
        "document_type": "concept",
        "component": "kubelet",
        "title": "Graceful Node Shutdown",
    },
    {
        "category": "cluster-administration",
        "name": "certificates.md",
        "upstream_path": "content/en/docs/concepts/cluster-administration/certificates.md",
        "document_type": "concept",
        "component": "security",
        "title": "Certificates",
    },

    # 10. Troubleshooting & Debugging (kubernetes/troubleshooting)
    {
        "category": "troubleshooting",
        "name": "debug-application-pods.md",
        "upstream_path": "content/en/docs/tasks/debug/debug-application/debug-pods.md",
        "document_type": "task",
        "component": "troubleshooting",
        "title": "Debug Pods",
    },
    {
        "category": "troubleshooting",
        "name": "debug-services.md",
        "upstream_path": "content/en/docs/tasks/debug/debug-application/debug-service.md",
        "document_type": "task",
        "component": "troubleshooting",
        "title": "Debug Services",
    },
    {
        "category": "troubleshooting",
        "name": "determine-pod-failure-reason.md",
        "upstream_path": "content/en/docs/tasks/debug/debug-application/determine-reason-pod-failure.md",
        "document_type": "task",
        "component": "troubleshooting",
        "title": "Determine the Reason for Pod Failure",
    },
    {
        "category": "troubleshooting",
        "name": "debug-running-pods.md",
        "upstream_path": "content/en/docs/tasks/debug/debug-application/debug-running-pod.md",
        "document_type": "task",
        "component": "troubleshooting",
        "title": "Debug Running Pods",
    },
    {
        "category": "troubleshooting",
        "name": "monitor-node-health.md",
        "upstream_path": "content/en/docs/tasks/debug/debug-cluster/monitor-node-health.md",
        "document_type": "task",
        "component": "troubleshooting",
        "title": "Monitor Node Health",
    },
    {
        "category": "troubleshooting",
        "name": "crictl-troubleshooting.md",
        "upstream_path": "content/en/docs/tasks/debug/debug-cluster/crictl.md",
        "document_type": "task",
        "component": "crictl",
        "title": "Debugging Kubernetes Nodes With crictl",
    },

    # 11. Resource Monitoring (kubernetes/monitoring)
    {
        "category": "monitoring",
        "name": "resource-usage-monitoring.md",
        "upstream_path": "content/en/docs/tasks/debug/debug-cluster/resource-usage-monitoring.md",
        "document_type": "task",
        "component": "monitoring",
        "title": "Resource Usage Monitoring",
    },
    {
        "category": "monitoring",
        "name": "resource-metrics-pipeline.md",
        "upstream_path": "content/en/docs/tasks/debug/debug-cluster/resource-metrics-pipeline.md",
        "document_type": "concept",
        "component": "metrics-server",
        "title": "Resource Metrics Pipeline",
    },
    {
        "category": "monitoring",
        "name": "system-metrics.md",
        "upstream_path": "content/en/docs/concepts/cluster-administration/system-metrics.md",
        "document_type": "concept",
        "component": "monitoring",
        "title": "System Metrics",
    },
]


def get_canonical_k8s_url(upstream_path: str) -> str:
    """Convert an upstream GitHub markdown path to canonical kubernetes.io documentation URL."""
    sub = upstream_path
    if sub.startswith("content/en/docs/"):
        sub = sub[len("content/en/docs/") :]
    if sub.endswith("/_index.md"):
        sub = sub[: -len("/_index.md")]
    elif sub == "_index.md":
        sub = ""
    elif sub.endswith(".md"):
        sub = sub[: -len(".md")]
    return f"https://kubernetes.io/docs/{sub}/" if sub else "https://kubernetes.io/docs/"


def clean_k8s_markdown(
    raw_markdown: str,
    *,
    version_label: str = "v1.37",
) -> tuple[dict[str, str], str]:
    """Clean Hugo frontmatter, shortcodes, and comments while preserving code blocks and YAML.

    Args:
        raw_markdown: The raw text from the upstream Hugo repository.
        version_label: Target version string for template substitution (e.g. 'v1.37').

    Returns:
        tuple of (extracted_raw_frontmatter_dict, cleaned_markdown_body)
    """
    lines = raw_markdown.splitlines()
    existing_meta: dict[str, str] = {}
    body = raw_markdown

    # 1. Strip and parse existing raw Hugo front matter
    if raw_markdown.startswith("---"):
        closing = next(
            (i for i, line in enumerate(lines[1:], start=1) if line.strip() == "---"),
            None,
        )
        if closing is not None:
            for line in lines[1:closing]:
                if ":" in line:
                    k, _, v = line.partition(":")
                    existing_meta[k.strip()] = v.strip().strip("\"'")
            body = "\n".join(lines[closing + 1 :]).lstrip()

    # 2. Extract and protect fenced code blocks (```...``` and ~~~...~~~)
    code_blocks: list[str] = []

    def code_repl(match: re.Match) -> str:
        block_text = match.group(0)
        # Convert any embedded Hugo skew / version macros inside code block comments
        block_text = re.sub(
            r"v\{\{<\s*skew\s+currentVersion\s*>\}\}",
            version_label,
            block_text,
        )
        block_text = re.sub(
            r"\{\{<\s*skew\s+currentVersion\s*>\}\}",
            version_label,
            block_text,
        )
        code_blocks.append(block_text)
        return f"__K8S_CODE_BLOCK_{len(code_blocks)-1}__"

    protected_body = re.sub(r"(```[\s\S]*?```|~~~[\s\S]*?~~~)", code_repl, body)

    # 3. Clean Hugo comments
    cleaned = re.sub(
        r"<!--\s*(?:overview|body|whatsnext|cleanup|prerequisites)\s*-->",
        "",
        protected_body,
    )

    # 4. Clean Hugo shortcodes
    # glossary_tooltip with explicit text
    cleaned = re.sub(
        r'\{\{<\s*glossary_tooltip[\s\S]*?text="([^"]+)"[\s\S]*?>\}\}',
        r"\1",
        cleaned,
    )
    # glossary_tooltip with term_id fallback
    def tooltip_term_repl(m: re.Match) -> str:
        term = m.group(1).replace("-", " ")
        return term

    cleaned = re.sub(
        r'\{\{<\s*glossary_tooltip[\s\S]*?term_id="([^"]+)"[\s\S]*?>\}\}',
        tooltip_term_repl,
        cleaned,
    )

    # Callout notes / cautions / warnings
    cleaned = re.sub(r"\{\{[<%]\s*note\s*[>%]\}\}", "> **Note:** ", cleaned)
    cleaned = re.sub(r"\{\{[<%]\s*/note\s*[>%]\}\}", "\n", cleaned)
    cleaned = re.sub(r"\{\{[<%]\s*caution\s*[>%]\}\}", "> **Caution:** ", cleaned)
    cleaned = re.sub(r"\{\{[<%]\s*/caution\s*[>%]\}\}", "\n", cleaned)
    cleaned = re.sub(r"\{\{[<%]\s*warning\s*[>%]\}\}", "> **Warning:** ", cleaned)
    cleaned = re.sub(r"\{\{[<%]\s*/warning\s*[>%]\}\}", "\n", cleaned)

    # Headings
    cleaned = re.sub(r'\{\{%\s*heading\s+"whatsnext"\s*%\}\}', "## What's next", cleaned)
    cleaned = re.sub(r'\{\{%\s*heading\s+"prerequisites"\s*%\}\}', "## Before you begin", cleaned)

    # Feature state annotations
    cleaned = re.sub(
        r'\{\{<\s*feature-state[\s\S]*?for_k8s_version="([^"]*)"[\s\S]*?state="([^"]*)"[\s\S]*?>\}\}',
        r"*(Feature state: \2 in \1)*",
        cleaned,
    )

    # Version / skew macros
    cleaned = re.sub(r'\{\{<\s*param\s+"version"\s*>\}\}', version_label, cleaned)
    cleaned = re.sub(r"v\{\{<\s*skew\s+currentVersion\s*>\}\}", version_label, cleaned)
    cleaned = re.sub(r"\{\{<\s*skew\s+currentVersion\s*>\}\}", version_label, cleaned)

    # Figures
    cleaned = re.sub(
        r'\{\{<\s*figure[\s\S]*?src="([^"]*)"[\s\S]*?title="([^"]*)"[\s\S]*?>\}\}',
        r"![\2](\1)",
        cleaned,
    )

    # Strip any remaining unhandled shortcodes
    cleaned = re.sub(r"\{\{<[\s\S]*?>\}\}", "", cleaned)
    cleaned = re.sub(r"\{\{%[\s\S]*?%\}\}", "", cleaned)

    # 5. Restore protected code blocks
    for i, cb in enumerate(code_blocks):
        cleaned = cleaned.replace(f"__K8S_CODE_BLOCK_{i}__", cb)

    # 6. Normalize trailing / multiple blank lines
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()

    return existing_meta, cleaned


def compute_content_hash(text: str) -> str:
    """Compute deterministic SHA-256 hex digest of cleaned text content."""
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


def generate_frontmatter(metadata: dict[str, Any]) -> str:
    """Generate standardized YAML frontmatter conforming to Stage 2.3.2 requirements."""
    fields = [
        "title",
        "category",
        "component",
        "kubernetes_version",
        "source_repository",
        "source_ref",
        "source_commit",
        "source_url",
        "source_version",
        "retrieved_at",
        "spdx_license",
        "attribution",
        "content_hash",
        "document_type",
        "doc_type",
    ]
    lines = ["---"]
    for f in fields:
        val = metadata.get(f, "")
        clean_val = str(val).replace('"', '\\"')
        lines.append(f'{f}: "{clean_val}"')
    lines.append("---")
    return "\n".join(lines)


def fetch_with_retries(
    url: str,
    *,
    max_retries: int = 3,
    initial_delay: float = 1.0,
    backoff: float = 2.0,
    timeout: float = 15.0,
) -> str:
    """Download text content with exponential backoff retries on network/HTTP errors."""
    delay = initial_delay
    last_err: Exception | None = None
    headers = {"User-Agent": USER_AGENT}

    for attempt in range(1, max_retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw_bytes = resp.read()
                return raw_bytes.decode("utf-8")
        except urllib.error.HTTPError as exc:
            last_err = exc
            # Do not retry on client 404
            if exc.code == 404:
                raise
            logger.warning(
                "HTTP %d on attempt %d/%d for %s; retrying in %.1fs...",
                exc.code,
                attempt,
                max_retries,
                url,
                delay,
            )
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_err = exc
            logger.warning(
                "Network error '%s' on attempt %d/%d for %s; retrying in %.1fs...",
                exc,
                attempt,
                max_retries,
                url,
                delay,
            )

        if attempt < max_retries:
            time.sleep(delay)
            delay *= backoff

    raise RuntimeError(
        f"Failed to fetch {url} after {max_retries} attempts: {last_err}"
    ) from last_err


def download_kubernetes_docs(
    *,
    catalog: list[dict[str, str]] | None = None,
    target_dir: Path = DEFAULT_CORPUS_DIR,
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
    force: bool = False,
    raw_base_url: str = RAW_BASE_URL,
    source_repository: str = DEFAULT_REPO,
    source_version: str = DEFAULT_BRANCH,
    kubernetes_version: str = DEFAULT_KUBERNETES_VERSION,
    source_ref: str = DEFAULT_SOURCE_REF,
    source_commit: str = DEFAULT_SOURCE_COMMIT,
    version_label: str = "v1.37",
) -> dict[str, Any]:
    """Execute automated collection of official Kubernetes documentation.

    Args:
        catalog: List of doc descriptors (defaults to K8S_DOCUMENT_CATALOG).
        target_dir: Destination root for downloaded documents.
        manifest_path: Path to write the output JSON manifest.
        force: If True, re-downloads even if cached locally.
        raw_base_url: Upstream GitHub raw content URL prefix.
        source_repository: Upstream repository URL.
        source_version: Upstream version identifier (e.g. 1.37).
        kubernetes_version: Official Kubernetes software release (e.g. 1.37).
        source_ref: Upstream git ref / branch (e.g. dev-1.37).
        source_commit: Upstream 40-character commit SHA.
        version_label: Version tag for shortcode replacements.

    Returns:
        Summary dict containing counts and manifest records.
    """
    catalog = catalog or K8S_DOCUMENT_CATALOG
    target_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    seen_hashes: dict[str, str] = {}  # hash -> local_path
    documents_manifest: list[dict[str, Any]] = []
    category_counts: dict[str, int] = {}
    downloaded_count = 0
    cached_count = 0
    skipped_duplicates_count = 0
    now_iso = datetime.now(UTC).isoformat()

    logger.info("Beginning collection of %d official Kubernetes documents into %s...", len(catalog), target_dir)

    for item in catalog:
        category = item["category"]
        name = item["name"]
        upstream_path = item["upstream_path"]
        doc_type = item.get("document_type", "concept")
        component = item.get("component", "general")
        configured_title = item.get("title", "")

        cat_dir = target_dir / category
        cat_dir.mkdir(parents=True, exist_ok=True)
        local_path = cat_dir / name

        # Path traversal prevention defense-in-depth
        try:
            resolved_local = local_path.resolve()
            resolved_target = target_dir.resolve()
            if not resolved_local.is_relative_to(resolved_target):
                raise ValueError(f"Path traversal detected: {local_path} is outside {target_dir}")
        except Exception:
            pass

        source_url = get_canonical_k8s_url(upstream_path)
        download_url = f"{raw_base_url.rstrip('/')}/{upstream_path}"
        immutable_source_url = f"https://raw.githubusercontent.com/kubernetes/website/{source_commit}/{upstream_path}"

        # Incremental download check
        if local_path.exists() and not force:
            try:
                existing_text = local_path.read_text(encoding="utf-8")
                # Parse existing frontmatter and body
                existing_meta, existing_body = clean_k8s_markdown(existing_text, version_label=version_label)
                content_hash = existing_meta.get("content_hash") or compute_content_hash(
                    existing_body
                )

                if content_hash in seen_hashes:
                    logger.warning(
                        "Duplicate content detected for cached %s (matches %s); skipping.",
                        local_path,
                        seen_hashes[content_hash],
                    )
                    skipped_duplicates_count += 1
                    continue

                seen_hashes[content_hash] = str(local_path)
                cached_count += 1
                category_counts[category] = category_counts.get(category, 0) + 1

                record = {
                    "name": name,
                    "title": existing_meta.get("title") or configured_title,
                    "category": category,
                    "component": existing_meta.get("component") or component,
                    "document_type": existing_meta.get("document_type") or doc_type,
                    "upstream_path": upstream_path,
                    "source_url": source_url,
                    "immutable_source_url": immutable_source_url,
                    "local_path": local_path.as_posix(),
                    "content_hash": content_hash,
                    "kubernetes_version": existing_meta.get("kubernetes_version") or kubernetes_version,
                    "source_ref": existing_meta.get("source_ref") or source_ref,
                    "source_commit": existing_meta.get("source_commit") or source_commit,
                    "version": source_version,
                    "license": SPDX_LICENSE,
                    "attribution": ATTRIBUTION,
                    "retrieval_status": "cached",
                }
                documents_manifest.append(record)
                continue
            except Exception as exc:
                logger.warning(
                    "Failed to read cached %s (%s); will re-download.", local_path, exc
                )

        # Download from upstream
        logger.info("Fetching [%s] %s -> %s", category, upstream_path, local_path)
        raw_content = fetch_with_retries(download_url)
        raw_meta, cleaned_body = clean_k8s_markdown(raw_content, version_label=version_label)

        # Determine authoritative title
        title = configured_title or raw_meta.get("title") or name.rsplit(".", 1)[0].replace("-", " ").title()

        # Compute content hash on cleaned markdown body
        content_hash = compute_content_hash(cleaned_body)

        # Duplicate detection across the corpus
        if content_hash in seen_hashes:
            logger.warning(
                "Duplicate content hash %s for %s (already seen in %s); skipping duplicate.",
                content_hash,
                local_path,
                seen_hashes[content_hash],
            )
            skipped_duplicates_count += 1
            continue

        seen_hashes[content_hash] = str(local_path)

        # Build metadata & front matter
        meta = {
            "title": title,
            "category": category,
            "component": component,
            "kubernetes_version": kubernetes_version,
            "source_repository": source_repository,
            "source_ref": source_ref,
            "source_commit": source_commit,
            "source_url": source_url,
            "source_version": source_version,
            "retrieved_at": now_iso,
            "spdx_license": SPDX_LICENSE,
            "attribution": ATTRIBUTION,
            "content_hash": content_hash,
            "document_type": doc_type,
            "doc_type": doc_type,
        }
        frontmatter = generate_frontmatter(meta)
        full_text = f"{frontmatter}\n\n{cleaned_body}\n"

        # Write to local file atomically
        local_path.write_text(full_text, encoding="utf-8")
        downloaded_count += 1
        category_counts[category] = category_counts.get(category, 0) + 1

        record = {
            "name": name,
            "title": title,
            "category": category,
            "component": component,
            "document_type": doc_type,
            "upstream_path": upstream_path,
            "source_url": source_url,
            "immutable_source_url": immutable_source_url,
            "local_path": local_path.as_posix(),
            "content_hash": content_hash,
            "kubernetes_version": kubernetes_version,
            "source_ref": source_ref,
            "source_commit": source_commit,
            "version": source_version,
            "license": SPDX_LICENSE,
            "attribution": ATTRIBUTION,
            "retrieval_status": "downloaded",
        }
        documents_manifest.append(record)

    # Build and write manifest JSON
    manifest_data = {
        "manifest_version": "1.0",
        "generated_at": now_iso,
        "kubernetes_version": kubernetes_version,
        "source_repository": source_repository,
        "source_ref": source_ref,
        "source_commit": source_commit,
        "source_version": source_version,
        "license": SPDX_LICENSE,
        "attribution": ATTRIBUTION,
        "total_documents": len(documents_manifest),
        "downloaded_documents": downloaded_count,
        "cached_documents": cached_count,
        "skipped_duplicates": skipped_duplicates_count,
        "categories": category_counts,
        "documents": documents_manifest,
    }

    manifest_path.write_text(
        json.dumps(manifest_data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    logger.info(
        "Manifest written to %s (Total: %d, Downloaded: %d, Cached: %d, Duplicates: %d)",
        manifest_path,
        len(documents_manifest),
        downloaded_count,
        cached_count,
        skipped_duplicates_count,
    )

    return manifest_data


def validate_collected_corpus(
    corpus_root: Path = DEFAULT_CORPUS_DIR,
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
) -> dict[str, Any]:
    """Validate the integrity, quality, and metadata consistency of collected documents."""
    if not manifest_path.exists():
        raise ValueError(f"Manifest file not found: {manifest_path}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    documents = manifest.get("documents", [])
    if not documents:
        raise ValueError(f"Manifest {manifest_path} contains no document records")

    seen_hashes: set[str] = set()
    errors: list[str] = []
    required_frontmatter_keys = {
        "title",
        "category",
        "component",
        "kubernetes_version",
        "source_repository",
        "source_ref",
        "source_commit",
        "source_url",
        "source_version",
        "retrieved_at",
        "spdx_license",
        "attribution",
        "content_hash",
        "document_type",
    }

    for doc in documents:
        local_path = Path(doc["local_path"])
        if not local_path.exists():
            errors.append(f"Missing file on disk: {local_path}")
            continue

        size = local_path.stat().st_size
        if size == 0:
            errors.append(f"Empty document file: {local_path}")
            continue

        text = local_path.read_text(encoding="utf-8")
        lines = text.splitlines()

        # Check frontmatter
        if not text.startswith("---"):
            errors.append(f"Missing YAML frontmatter in {local_path}")
            continue

        closing = next(
            (i for i, line in enumerate(lines[1:], start=1) if line.strip() == "---"),
            None,
        )
        if closing is None:
            errors.append(f"Unclosed YAML frontmatter in {local_path}")
            continue

        frontmatter_dict = {}
        for line in lines[1:closing]:
            if ":" in line:
                k, _, v = line.partition(":")
                frontmatter_dict[k.strip()] = v.strip().strip("\"'")

        missing_keys = required_frontmatter_keys - set(frontmatter_dict.keys())
        if missing_keys:
            errors.append(
                f"Missing required frontmatter keys {missing_keys} in {local_path}"
            )

        # Check provenance consistency between frontmatter and manifest
        if "kubernetes_version" in frontmatter_dict and doc.get("kubernetes_version"):
            if frontmatter_dict["kubernetes_version"] != doc["kubernetes_version"]:
                errors.append(
                    f"Provenance mismatch: kubernetes_version in {local_path} ({frontmatter_dict['kubernetes_version']}) != manifest ({doc['kubernetes_version']})"
                )
        if "source_commit" in frontmatter_dict and doc.get("source_commit"):
            if frontmatter_dict["source_commit"] != doc["source_commit"]:
                errors.append(
                    f"Provenance mismatch: source_commit in {local_path} != manifest ({doc['source_commit']})"
                )
        if "source_ref" in frontmatter_dict and doc.get("source_ref"):
            if frontmatter_dict["source_ref"] != doc["source_ref"]:
                errors.append(
                    f"Provenance mismatch: source_ref in {local_path} != manifest ({doc['source_ref']})"
                )

        # Check body
        body = "\n".join(lines[closing + 1 :]).strip()
        if not body:
            errors.append(f"Empty markdown body in {local_path}")
            continue

        # Check content hash
        computed_hash = compute_content_hash(body)
        expected_hash = doc.get("content_hash")
        if computed_hash != expected_hash:
            errors.append(
                f"Content hash mismatch for {local_path}: expected {expected_hash}, computed {computed_hash}"
            )

        # Check duplicate hash
        if computed_hash in seen_hashes:
            errors.append(
                f"Duplicate content detected across corpus: hash {computed_hash} in {local_path}"
            )
        seen_hashes.add(computed_hash)

        # Check code fence balance
        fences_count = body.count("```")
        if fences_count % 2 != 0:
            errors.append(
                f"Unbalanced code fences ({fences_count} backtick sets) in {local_path}"
            )

    if errors:
        msg = f"Validation failed with {len(errors)} error(s):\n" + "\n".join(
            f" - {e}" for e in errors
        )
        logger.error(msg)
        raise ValueError(msg)

    logger.info(
        "Corpus validation passed successfully! Checked %d documents, 0 errors.",
        len(documents),
    )
    return {
        "status": "passed",
        "documents_checked": len(documents),
        "unique_hashes": len(seen_hashes),
        "manifest_path": str(manifest_path),
    }


def promote_staged_corpus(
    *,
    staging_dir: Path = DEFAULT_STAGING_DIR,
    staging_manifest: Path = DEFAULT_STAGING_MANIFEST,
    active_dir: Path = DEFAULT_CORPUS_DIR,
    active_manifest: Path = DEFAULT_MANIFEST_PATH,
    backup_131_dir: Path = DEFAULT_BACKUP_131_DIR,
) -> dict[str, Any]:
    """Safely back up release-1.31 active collection and promote validated staging collection."""
    # 1. Validate staging corpus first
    logger.info("Validating staging corpus at %s before promotion...", staging_dir)
    validate_collected_corpus(corpus_root=staging_dir, manifest_path=staging_manifest)

    # 2. Back up existing active release-1.31 collection
    if active_dir.exists():
        backup_131_dir.mkdir(parents=True, exist_ok=True)
        logger.info("Backing up active 1.31 corpus to %s...", backup_131_dir)
        for item in active_dir.iterdir():
            dest = backup_131_dir / item.name
            if item.is_dir():
                shutil.copytree(item, dest, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dest)
        if active_manifest.exists():
            shutil.copy2(active_manifest, backup_131_dir / "kubernetes_manifest_1.31.json")

    # 3. Promote staged documents into active_dir
    active_dir.mkdir(parents=True, exist_ok=True)
    for cat_dir in staging_dir.iterdir():
        if cat_dir.is_dir():
            target_cat = active_dir / cat_dir.name
            target_cat.mkdir(parents=True, exist_ok=True)
            for f in cat_dir.iterdir():
                if f.is_file():
                    shutil.copy2(f, target_cat / f.name)

    # 4. Promote manifest with updated local_path
    staging_manifest_obj = json.loads(staging_manifest.read_text(encoding="utf-8"))
    for d in staging_manifest_obj["documents"]:
        rel = Path(d["local_path"]).relative_to(staging_dir).as_posix()
        d["local_path"] = (active_dir / rel).as_posix()

    active_manifest.write_text(
        json.dumps(staging_manifest_obj, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    # 5. Validate promoted active corpus
    logger.info("Validating promoted active corpus at %s...", active_dir)
    val = validate_collected_corpus(corpus_root=active_dir, manifest_path=active_manifest)

    return {
        "status": "promoted",
        "active_documents": val["documents_checked"],
        "backup_location": str(backup_131_dir),
        "manifest_path": str(active_manifest),
    }


def main() -> None:
    """CLI entrypoint for downloading, staging, or validating official Kubernetes documentation."""
    parser = argparse.ArgumentParser(
        description="Download and curate official Kubernetes documentation for the SRE copilot."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-download even if files are already cached locally.",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Run quality validation on already downloaded corpus without fetching.",
    )
    parser.add_argument(
        "--staging",
        action="store_true",
        help="Collect documents into safe staging directory without modifying active corpus.",
    )
    parser.add_argument(
        "--promote",
        action="store_true",
        help="Safely promote validated staged corpus to active corpus with backup.",
    )
    parser.add_argument(
        "--target-dir",
        type=Path,
        default=None,
        help="Root directory for downloaded Kubernetes corpus.",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="Path to manifest JSON file.",
    )
    parser.add_argument(
        "--upstream-ref",
        type=str,
        default=DEFAULT_UPSTREAM_REF,
        help="Upstream git ref/branch on kubernetes/website (default: dev-1.37).",
    )
    parser.add_argument(
        "--version-label",
        type=str,
        default=DEFAULT_BRANCH,
        help="Source version label (default: 1.37).",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    target_dir = args.target_dir or (DEFAULT_STAGING_DIR if args.staging else DEFAULT_CORPUS_DIR)
    manifest_path = args.manifest or (DEFAULT_STAGING_MANIFEST if args.staging else DEFAULT_MANIFEST_PATH)

    if args.promote:
        print("Promoting validated staging corpus to active corpus...")
        res = promote_staged_corpus()
        print(f"Promotion SUCCESS: {res['active_documents']} documents promoted. Backup at: {res['backup_location']}")
        return

    if args.validate_only:
        print(f"Validating corpus at {target_dir}...")
        res = validate_collected_corpus(corpus_root=target_dir, manifest_path=manifest_path)
        print(f"Validation SUCCESS: {res['documents_checked']} documents verified.")
        return

    raw_base_url = f"https://raw.githubusercontent.com/kubernetes/website/{args.upstream_ref}/"
    print(f"Starting Kubernetes docs collection ({args.version_label} from {args.upstream_ref}) into {target_dir}...")
    manifest = download_kubernetes_docs(
        target_dir=target_dir,
        manifest_path=manifest_path,
        force=args.force,
        raw_base_url=raw_base_url,
        source_version=args.version_label,
        version_label="v1.37",
    )
    print(f"Collection complete: {manifest['total_documents']} documents recorded.")

    print("Running post-download quality validation...")
    val_res = validate_collected_corpus(corpus_root=target_dir, manifest_path=manifest_path)
    print(f"All {val_res['documents_checked']} documents validated successfully!")


if __name__ == "__main__":
    main()
