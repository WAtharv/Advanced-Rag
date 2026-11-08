"""Curated 50-question evaluation benchmark for Kubernetes, Helm, Prometheus, and SRE.

Every question is grounded in the 252 documents of the active corpus.
Expected sources name the canonical documents that a correct retrieval must surface.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class EvalCase50:
    id: str
    question: str
    category: str  # kubernetes | helm | prometheus | sre
    expected_sources: list[str] = field(default_factory=list)
    expected_sections: list[str] = field(default_factory=list)
    expected_facts: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "question": self.question,
            "category": self.category,
            "expected_sources": self.expected_sources,
            "expected_sections": self.expected_sections,
            "expected_facts": self.expected_facts,
            "tags": self.tags,
        }


EVAL_BENCHMARK_50: list[EvalCase50] = [
    # ==========================================
    # KUBERNETES (15 Cases)
    # ==========================================
    EvalCase50(
        id="k8s-01",
        question="How do I troubleshoot a container in CrashLoopBackOff exiting with code 1?",
        category="kubernetes",
        expected_sources=[
            "sre/runbooks/kubernetes/crashloopbackoff.md",
            "kubernetes/troubleshooting/determine-pod-failure-reason.md",
            "kubernetes/troubleshooting/debug-application-pods.md",
        ],
        expected_sections=["Initial Triage", "Diagnostic Procedure", "Remediation Steps"],
        expected_facts=["kubectl logs", "--previous", "lastState.terminated.exitCode"],
        tags=["troubleshooting", "workloads", "pods"],
    ),
    EvalCase50(
        id="k8s-02",
        question="A container was terminated with exit code 137. How do I determine if it was OOMKilled and what was the memory limit?",
        category="kubernetes",
        expected_sources=[
            "sre/runbooks/kubernetes/oomkilled.md",
            "kubernetes/troubleshooting/determine-pod-failure-reason.md",
            "kubernetes/configuration/manage-resources-containers.md",
        ],
        expected_sections=["Initial Triage", "Diagnostic Procedure"],
        expected_facts=["137", "oomkilled", "memory limit", "lastState.terminated.reason"],
        tags=["troubleshooting", "memory", "resources"],
    ),
    EvalCase50(
        id="k8s-03",
        question="What causes ImagePullBackOff or ErrImagePull errors in Kubernetes pods?",
        category="kubernetes",
        expected_sources=[
            "sre/runbooks/kubernetes/imagepullbackoff.md",
            "kubernetes/troubleshooting/determine-pod-failure-reason.md",
            "kubernetes/troubleshooting/debug-application-pods.md",
        ],
        expected_sections=["Common Root Causes", "Diagnostic Procedure"],
        expected_facts=["imagePullSecrets", "repository", "tag", "authorization"],
        tags=["troubleshooting", "images", "containers"],
    ),
    EvalCase50(
        id="k8s-04",
        question="A Kubernetes node reports NotReady. How do I inspect kubelet status and node conditions?",
        category="kubernetes",
        expected_sources=[
            "sre/runbooks/kubernetes/nodenotready.md",
            "kubernetes/troubleshooting/monitor-node-health.md",
            "kubernetes/architecture/nodes.md",
        ],
        expected_sections=["Diagnostic Procedure", "Remediation Steps"],
        expected_facts=["Ready", "kubelet", "systemctl status kubelet", "Ready condition"],
        tags=["cluster-admin", "nodes", "infrastructure"],
    ),
    EvalCase50(
        id="k8s-05",
        question="Why are pods stuck in Pending with 0/N nodes available due to Insufficient cpu or Insufficient memory?",
        category="kubernetes",
        expected_sources=[
            "sre/runbooks/kubernetes/pending-pods.md",
            "sre/runbooks/kubernetes/pod-scheduling-failures.md",
            "kubernetes/scheduling/kube-scheduler.md",
        ],
        expected_sections=["Diagnostic Procedure", "Common Root Causes"],
        expected_facts=["resources.requests", "allocatable", "filter", "scheduler"],
        tags=["scheduling", "resources", "capacity"],
    ),
    EvalCase50(
        id="k8s-06",
        question="How do I configure liveness, readiness, and startup probes to avoid killing containers during slow initialization?",
        category="kubernetes",
        expected_sources=[
            "kubernetes/workloads/configure-probes.md",
            "sre/runbooks/kubernetes/probe-failures.md",
        ],
        expected_sections=["Define a startup probe", "Diagnostic Procedure"],
        expected_facts=["startupProbe", "failureThreshold", "periodSeconds", "initialDelaySeconds"],
        tags=["probes", "lifecycle", "reliability"],
    ),
    EvalCase50(
        id="k8s-07",
        question="What are the differences between Guaranteed, Burstable, and BestEffort QoS classes and eviction order?",
        category="kubernetes",
        expected_sources=[
            "kubernetes/scheduling/limit-ranges.md",
            "sre/runbooks/kubernetes/node-pressure-eviction.md",
            "kubernetes/workloads/pod-lifecycle.md",
        ],
        expected_sections=["Quality of Service", "Diagnostic Procedure"],
        expected_facts=["Guaranteed", "Burstable", "BestEffort", "requests", "limits"],
        tags=["qos", "eviction", "resources"],
    ),
    EvalCase50(
        id="k8s-08",
        question="How does NetworkPolicy ingress and egress isolation work and why are pod connections being dropped?",
        category="kubernetes",
        expected_sources=[
            "kubernetes/networking/network-policies.md",
            "sre/runbooks/networking/networkpolicy-blocking.md",
        ],
        expected_sections=["NetworkPolicy", "Diagnostic Procedure"],
        expected_facts=["policyTypes", "Ingress", "Egress", "podSelector", "matchLabels"],
        tags=["networking", "security", "firewall"],
    ),
    EvalCase50(
        id="k8s-09",
        question="How do PersistentVolumeClaims bind to PersistentVolumes and why is a PVC stuck in Pending state?",
        category="kubernetes",
        expected_sources=[
            "kubernetes/storage/persistent-volumes.md",
            "sre/runbooks/storage/pvc-pending.md",
            "kubernetes/storage/storage-classes.md",
        ],
        expected_sections=["Binding", "Diagnostic Procedure"],
        expected_facts=["storageClassName", "accessModes", "provisioner", "Pending"],
        tags=["storage", "pv", "pvc"],
    ),
    EvalCase50(
        id="k8s-10",
        question="How do I safely drain a Kubernetes node for maintenance without violating PodDisruptionBudgets?",
        category="kubernetes",
        expected_sources=[
            "kubernetes/cluster-administration/safely-drain-node.md",
            "sre/runbooks/kubernetes/node-pressure-eviction.md",
        ],
        expected_sections=["Safely Drain a Node", "Diagnostic Procedure"],
        expected_facts=["kubectl drain", "--ignore-daemonsets", "--delete-emptydir-data", "minAvailable"],
        tags=["cluster-admin", "pdb", "drain"],
    ),
    EvalCase50(
        id="k8s-11",
        question="How do Kubernetes Service clusterIP and selectors route traffic to EndpointSlices and healthy pods?",
        category="kubernetes",
        expected_sources=[
            "kubernetes/networking/services.md",
            "kubernetes/networking/endpoint-slices.md",
            "sre/runbooks/networking/service-connectivity-failures.md",
        ],
        expected_sections=["Service Definition", "EndpointSlices"],
        expected_facts=["selector", "clusterIP", "endpointslices", "endpoints"],
        tags=["networking", "services", "routing"],
    ),
    EvalCase50(
        id="k8s-12",
        question="How do taints and tolerations prevent unwanted pods from scheduling on dedicated GPU or control-plane nodes?",
        category="kubernetes",
        expected_sources=[
            "kubernetes/scheduling/taints-and-tolerations.md",
            "kubernetes/scheduling/assign-pod-node.md",
        ],
        expected_sections=["Taints and Tolerations", "Concepts"],
        expected_facts=["NoSchedule", "NoExecute", "effect", "tolerations", "taint"],
        tags=["scheduling", "taints", "affinity"],
    ),
    EvalCase50(
        id="k8s-13",
        question="How does RBAC RoleBinding grant permissions to a ServiceAccount across a namespace?",
        category="kubernetes",
        expected_sources=[
            "kubernetes/security/rbac.md",
            "sre/runbooks/security/rbac-authorization-failures.md",
            "kubernetes/security/service-accounts.md",
        ],
        expected_sections=["Role and ClusterRole", "RoleBinding and ClusterRoleBinding"],
        expected_facts=["RoleBinding", "subjects", "ServiceAccount", "roleRef", "apiGroups"],
        tags=["security", "rbac", "authorization"],
    ),
    EvalCase50(
        id="k8s-14",
        question="What is the recommended procedure for troubleshooting CoreDNS query timeouts and SERVFAIL errors in cluster-dns?",
        category="kubernetes",
        expected_sources=[
            "sre/runbooks/networking/coredns-failures.md",
            "kubernetes/networking/dns-pod-service.md",
        ],
        expected_sections=["Diagnostic Procedure", "Remediation Steps"],
        expected_facts=["CoreDNS", "kube-dns", "resolv.conf", "SERVFAIL", "forward"],
        tags=["networking", "dns", "coredns"],
    ),
    EvalCase50(
        id="k8s-15",
        question="Why is a StatefulSet pod not starting when its PersistentVolume is mounted by another node?",
        category="kubernetes",
        expected_sources=[
            "sre/runbooks/kubernetes/statefulset-failures.md",
            "sre/runbooks/storage/volume-mount-failures.md",
            "kubernetes/workloads/statefulsets.md",
        ],
        expected_sections=["Diagnostic Procedure", "Volume Attachment Issues"],
        expected_facts=["Multi-Attach error", "ReadWriteOnce", "VolumeAttachment", "ReadWriteOncePod"],
        tags=["storage", "statefulset", "volumes"],
    ),

    # ==========================================
    # HELM (10 Cases)
    # ==========================================
    EvalCase50(
        id="helm-01",
        question="How do I recover a release stuck in 'pending-upgrade' or 'pending-install' status?",
        category="helm",
        expected_sources=[
            "sre/runbooks/helm/pending-release-state.md",
            "helm/operations/helm-status.md",
            "helm/operations/helm-rollback.md",
        ],
        expected_sections=["Initial Triage", "Diagnostic Procedure", "Remediation Steps"],
        expected_facts=["pending-upgrade", "helm rollback", "release secret", "helm status"],
        tags=["troubleshooting", "releases", "lifecycle"],
    ),
    EvalCase50(
        id="helm-02",
        question="Why did `helm upgrade` fail with error 'field is immutable' on a StatefulSet or Service?",
        category="helm",
        expected_sources=[
            "sre/runbooks/helm/upgrade-failures.md",
            "helm/operations/helm-upgrade.md",
            "helm/troubleshooting/charts-tips-and-tricks.md",
        ],
        expected_sections=["Common Root Causes", "Remediation Steps"],
        expected_facts=["field is immutable", "spec.clusterIP", "spec.selector", "force"],
        tags=["troubleshooting", "upgrade", "manifests"],
    ),
    EvalCase50(
        id="helm-03",
        question="How do I roll back a failed Helm release to the previous successful revision?",
        category="helm",
        expected_sources=[
            "helm/operations/helm-rollback.md",
            "sre/runbooks/helm/rollback-failures.md",
            "helm/operations/helm-history.md",
        ],
        expected_sections=["helm rollback", "Diagnostic Procedure"],
        expected_facts=["helm rollback", "revision", "helm history", "release status"],
        tags=["operations", "rollback", "revisions"],
    ),
    EvalCase50(
        id="helm-04",
        question="How do I validate chart values and troubleshoot template rendering syntax errors?",
        category="helm",
        expected_sources=[
            "sre/runbooks/helm/chart-rendering-failures.md",
            "helm/templating/debugging-templates.md",
            "helm/advanced/helm-lint.md",
        ],
        expected_sections=["Debugging Templates", "Diagnostic Procedure"],
        expected_facts=["helm lint", "helm template", "values.yaml", "syntax error"],
        tags=["templating", "schema", "validation"],
    ),
    EvalCase50(
        id="helm-05",
        question="How do I resolve chart dependency update errors and authenticate to private chart repositories?",
        category="helm",
        expected_sources=[
            "helm/charts/chart-dependencies.md",
            "sre/runbooks/helm/dependency-problems.md",
            "helm/advanced/helm-dependency.md",
        ],
        expected_sections=["Managing Dependencies", "Diagnostic Procedure"],
        expected_facts=["Chart.lock", "helm dependency build", "helm dependency update", "repository"],
        tags=["charts", "dependencies", "repos"],
    ),
    EvalCase50(
        id="helm-06",
        question="What are Helm lifecycle hooks and in what order do pre-install, post-install, and pre-delete hooks execute?",
        category="helm",
        expected_sources=[
            "helm/advanced/chart-hooks.md",
        ],
        expected_sections=["The Available Hooks", "Hook weight"],
        expected_facts=["helm.sh/hook", "pre-install", "post-install", "hook-weight", "pre-delete"],
        tags=["advanced", "hooks", "lifecycle"],
    ),
    EvalCase50(
        id="helm-07",
        question="How do I use `helm template` and `--dry-run` to inspect rendered Kubernetes manifests before deployment?",
        category="helm",
        expected_sources=[
            "helm/advanced/helm-template.md",
            "helm/templating/debugging-templates.md",
            "helm/operations/helm-install.md",
        ],
        expected_sections=["helm template", "Debugging Templates"],
        expected_facts=["helm template", "--dry-run", "--debug", "render"],
        tags=["debugging", "templates", "inspection"],
    ),
    EvalCase50(
        id="helm-08",
        question="How do I package, push, and pull Helm charts using OCI-compliant container registries?",
        category="helm",
        expected_sources=[
            "helm/charts/registries-and-oci.md",
            "helm/charts/helm-push.md",
            "helm/charts/helm-pull.md",
        ],
        expected_sections=["OCI Feature", "helm package", "helm push"],
        expected_facts=["oci://", "helm registry login", "helm package", "helm push"],
        tags=["oci", "registries", "packaging"],
    ),
    EvalCase50(
        id="helm-09",
        question="What are the differences between Helm 3 release secret storage and Helm 4 architecture?",
        category="helm",
        expected_sources=[
            "helm/fundamentals/architecture-overview.md",
            "helm/troubleshooting/troubleshooting-faq.md",
        ],
        expected_sections=["Architecture Overview", "Helm 3", "Release Storage"],
        expected_facts=["Secret", "sh.helm.release.v1", "storage", "Tiller"],
        tags=["architecture", "versioning", "storage"],
    ),
    EvalCase50(
        id="helm-10",
        question="How do subcharts inherit global values and how do named templates work with `include` versus `template`?",
        category="helm",
        expected_sources=[
            "helm/templating/subcharts-and-globals.md",
            "helm/templating/named-templates.md",
        ],
        expected_sections=["Global Values", "Setting the scope of a template"],
        expected_facts=["global", "include", "define", "indent", "subchart"],
        tags=["templating", "subcharts", "values"],
    ),

    # ==========================================
    # PROMETHEUS (10 Cases)
    # ==========================================
    EvalCase50(
        id="prom-01",
        question="Why is a Prometheus scrape target reported as DOWN with 'context deadline exceeded'?",
        category="prometheus",
        expected_sources=[
            "sre/runbooks/prometheus/targets-down.md",
            "sre/runbooks/prometheus/scrape-failures.md",
            "prometheus/troubleshooting/faq.md",
        ],
        expected_sections=["Initial Triage", "Diagnostic Procedure", "Remediation Steps"],
        expected_facts=["scrape_timeout", "scrape_interval", "context deadline exceeded", "UP == 0"],
        tags=["troubleshooting", "targets", "scraping"],
    ),
    EvalCase50(
        id="prom-02",
        question="How does Alertmanager group, inhibit, and route alerts using matchers and receivers?",
        category="prometheus",
        expected_sources=[
            "prometheus/alertmanager/configuration.md",
            "sre/runbooks/prometheus/alertmanager-notification-failures.md",
            "prometheus/alertmanager/alerting-practices.md",
        ],
        expected_sections=["Route Configuration", "Inhibition Rules"],
        expected_facts=["group_by", "group_wait", "group_interval", "inhibit_rules", "receiver"],
        tags=["alertmanager", "routing", "inhibition"],
    ),
    EvalCase50(
        id="prom-03",
        question="How do I detect and mitigate high-cardinality metric label explosions that cause high TSDB memory consumption?",
        category="prometheus",
        expected_sources=[
            "sre/runbooks/prometheus/high-cardinality-problems.md",
            "prometheus/troubleshooting/histograms-cardinality.md",
            "prometheus/fundamentals/storage.md",
        ],
        expected_sections=["Diagnostic Procedure", "Remediation Steps", "TSDB Status"],
        expected_facts=["tsdb status", "cardinality", "labeldrop", "metric_relabel_configs"],
        tags=["troubleshooting", "tsdb", "cardinality"],
    ),
    EvalCase50(
        id="prom-04",
        question="What is the difference between `rate()` and `irate()` functions in PromQL and when should each be used?",
        category="prometheus",
        expected_sources=[
            "prometheus/promql/functions.md",
            "prometheus/promql/basics.md",
        ],
        expected_sections=["rate()", "irate()"],
        expected_facts=["rate", "irate", "counter reset", "range vector", "instant"],
        tags=["promql", "functions", "counters"],
    ),
    EvalCase50(
        id="prom-05",
        question="How do metric relabeling rules drop or modify labels before metrics are ingested into TSDB?",
        category="prometheus",
        expected_sources=[
            "prometheus/kubernetes-monitoring/configuration.md",
            "prometheus/kubernetes-monitoring/remote-write-practices.md",
        ],
        expected_sections=["relabel_configs", "metric_relabel_configs"],
        expected_facts=["action", "replace", "drop", "keep", "regex", "target_label"],
        tags=["discovery", "relabeling", "configuration"],
    ),
    EvalCase50(
        id="prom-06",
        question="How do I test PromQL alert rules and unit test rule files using `promtool check rules`?",
        category="prometheus",
        expected_sources=[
            "prometheus/promql/promtool.md",
            "prometheus/promql/unit-testing-rules.md",
            "prometheus/alertmanager/alerting-rules.md",
        ],
        expected_sections=["check rules", "Unit Testing Rules"],
        expected_facts=["promtool check rules", "test_rules", "promtool", "evaluation_time"],
        tags=["promtool", "testing", "rules"],
    ),
    EvalCase50(
        id="prom-07",
        question="Why are expected Kubernetes pod metrics missing from Prometheus after deployment?",
        category="prometheus",
        expected_sources=[
            "sre/runbooks/prometheus/missing-metrics.md",
            "prometheus/kubernetes-monitoring/configuration.md",
            "prometheus/kubernetes-monitoring/cadvisor.md",
        ],
        expected_sections=["Diagnostic Procedure", "Kubernetes SD Configuration"],
        expected_facts=["prometheus.io/scrape", "kubernetes_sd_configs", "relabel_configs", "annotations"],
        tags=["troubleshooting", "service-discovery", "metrics"],
    ),
    EvalCase50(
        id="prom-08",
        question="How do I optimize slow PromQL queries that time out on large time ranges or high-volume series?",
        category="prometheus",
        expected_sources=[
            "sre/runbooks/prometheus/promql-performance.md",
            "prometheus/troubleshooting/query-log.md",
        ],
        expected_sections=["Diagnostic Procedure", "Optimization Techniques"],
        expected_facts=["recording rules", "subqueries", "regex matchers", "query timeout"],
        tags=["promql", "performance", "optimization"],
    ),
    EvalCase50(
        id="prom-09",
        question="What is the role of Prometheus recording rules in precomputing expensive aggregations?",
        category="prometheus",
        expected_sources=[
            "prometheus/promql/recording-rules.md",
            "prometheus/promql/basics.md",
        ],
        expected_sections=["Configuring rules", "Recording rules"],
        expected_facts=["record", "expr", "recording rule", "precompute", "evaluation_interval"],
        tags=["rules", "recording-rules", "operations"],
    ),
    EvalCase50(
        id="prom-10",
        question="How does Prometheus handle WAL replay and TSDB compaction when restarting after an abrupt termination?",
        category="prometheus",
        expected_sources=[
            "prometheus/fundamentals/storage.md",
        ],
        expected_sections=["WAL", "Compaction"],
        expected_facts=["write-ahead log", "WAL", "block", "compaction", "head block"],
        tags=["storage", "wal", "tsdb"],
    ),

    # ==========================================
    # SRE & INCIDENT RESPONSE (15 Cases)
    # ==========================================
    EvalCase50(
        id="sre-01",
        question="What criteria and metrics determine declaring a P1 critical incident versus a P2 major incident?",
        category="sre",
        expected_sources=[
            "sre/response-procedures/severity-assignment.md",
            "sre/response-procedures/incident-declaration.md",
        ],
        expected_sections=["Severity Level Definitions", "Severity Classification Matrix"],
        expected_facts=["P1", "P2", "customer impact", "data loss", "SLO breach"],
        tags=["incident-response", "severity", "sla"],
    ),
    EvalCase50(
        id="sre-02",
        question="What critical diagnostic evidence must be collected from pods and nodes before restarting or deleting failed workloads?",
        category="sre",
        expected_sources=[
            "sre/response-procedures/evidence-collection.md",
        ],
        expected_sections=["Pre-Action Collection Protocol", "Core Evidence Checklist"],
        expected_facts=["kubectl describe", "kubectl logs --previous", "heap dump", "thread dump", "core dump"],
        tags=["evidence", "diagnostics", "procedures"],
    ),
    EvalCase50(
        id="sre-03",
        question="What structured briefing information must an outgoing Incident Commander transfer during an incident handover?",
        category="sre",
        expected_sources=[
            "sre/response-procedures/communication-and-escalation.md",
            "sre/response-procedures/incident-triage.md",
        ],
        expected_sections=["Handover Briefing Format", "Status Synchronization Protocol"],
        expected_facts=["current impact", "active hypotheses", "mitigations tried", "Incident Commander"],
        tags=["roles", "handover", "communication"],
    ),
    EvalCase50(
        id="sre-04",
        question="What is the difference between immediate tactical mitigation and root-cause resolution during active outages?",
        category="sre",
        expected_sources=[
            "sre/response-procedures/mitigation-and-recovery.md",
        ],
        expected_sections=["Mitigation vs Root Cause Distinction", "Decision Matrix"],
        expected_facts=["mitigation", "restore service", "root cause", "post-incident", "rollback"],
        tags=["strategy", "mitigation", "incident-response"],
    ),
    EvalCase50(
        id="sre-05",
        question="In the simulated OOMKilled cascade postmortem, what was the contributing trigger that caused cascading pod crashes?",
        category="sre",
        expected_sources=[
            "sre/postmortems/simulated-incident-oomkilled-cascade.md",
            "sre/runbooks/kubernetes/oomkilled.md",
        ],
        expected_sections=["Root Cause & Contributing Factors", "Timeline"],
        expected_facts=["memory limit", "cascading", "OOMKilled", "load shedding"],
        tags=["postmortem", "cascading-failure", "simulation"],
    ),
    EvalCase50(
        id="sre-06",
        question="How are incident communication channels established and what cadence is required for stakeholder status updates?",
        category="sre",
        expected_sources=[
            "sre/response-procedures/communication-and-escalation.md",
        ],
        expected_sections=["Channel Establishment", "Update Frequency & Cadence"],
        expected_facts=["slack channel", "bridge call", "every 15 minutes", "status page", "cadence"],
        tags=["communication", "incident-management", "stakeholders"],
    ),
    EvalCase50(
        id="sre-07",
        question="In the simulated CoreDNS saturation postmortem, how did query volume cause DNS query storms?",
        category="sre",
        expected_sources=[
            "sre/postmortems/simulated-incident-coredns-saturation.md",
            "sre/runbooks/networking/coredns-failures.md",
        ],
        expected_sections=["Root Cause & Contributing Factors", "Technical Deep Dive"],
        expected_facts=["CoreDNS", "query amplification", "cache", "saturation"],
        tags=["postmortem", "dns", "simulation"],
    ),
    EvalCase50(
        id="sre-08",
        question="How do you prioritize and track blameless postmortem action items to prevent repeat production incidents?",
        category="sre",
        expected_sources=[
            "sre/response-procedures/incident-closure.md",
            "sre/templates/postmortem-template.md",
        ],
        expected_sections=["Action Item Categorization", "Prioritization Framework"],
        expected_facts=["preventative", "detective", "mitigative", "SLA", "owner"],
        tags=["postmortem", "action-items", "governance"],
    ),
    EvalCase50(
        id="sre-09",
        question="What are the key stages of an incident retrospective and how is psychological safety maintained?",
        category="sre",
        expected_sources=[
            "sre/response-procedures/incident-closure.md",
            "sre/templates/postmortem-template.md",
        ],
        expected_sections=["Retrospective Agenda", "Blameless Culture Principles"],
        expected_facts=["blameless", "psychological safety", "timeline review", "systemic factors"],
        tags=["retrospective", "culture", "blameless"],
    ),
    EvalCase50(
        id="sre-10",
        question="In the simulated Helm failed upgrade postmortem, why did release state prevent rolling back a failed deployment?",
        category="sre",
        expected_sources=[
            "sre/postmortems/simulated-incident-helm-failed-upgrade.md",
            "sre/runbooks/helm/rollback-failures.md",
        ],
        expected_sections=["Root Cause & Contributing Factors", "Timeline"],
        expected_facts=["pending-upgrade", "release secret", "Helm lock", "rollback failure"],
        tags=["postmortem", "helm", "simulation"],
    ),
    EvalCase50(
        id="sre-11",
        question="How do you recover a stateful service after a split-brain condition in StatefulSet replicas?",
        category="sre",
        expected_sources=[
            "sre/incidents/simulated-case-study-split-brain-statefulset.md",
            "sre/runbooks/kubernetes/statefulset-failures.md",
        ],
        expected_sections=["Emergency Quorum Restoration", "Disaster Recovery"],
        expected_facts=["split-brain", "quorum", "StatefulSet", "data reconciliation"],
        tags=["incident", "statefulset", "disaster-recovery"],
    ),
    EvalCase50(
        id="sre-12",
        question="In the simulated Prometheus TSDB OOM postmortem, what was the primary cause of TSDB memory exhaustion?",
        category="sre",
        expected_sources=[
            "sre/postmortems/simulated-incident-prometheus-cardinality-tsdb-oom.md",
            "sre/runbooks/prometheus/high-cardinality-problems.md",
        ],
        expected_sections=["Root Cause & Contributing Factors", "Timeline"],
        expected_facts=["cardinality explosion", "ephemeral label", "transaction ID", "OOMKilled"],
        tags=["postmortem", "prometheus", "simulation"],
    ),
    EvalCase50(
        id="sre-13",
        question="What checklist steps should an engineer follow immediately after declaring a production incident?",
        category="sre",
        expected_sources=[
            "sre/templates/incident-declaration-checklist.md",
            "sre/response-procedures/incident-declaration.md",
        ],
        expected_sections=["Immediate Actions (First 5 Minutes)", "Communication Tasks"],
        expected_facts=["page incident commander", "create war room", "declare severity", "stakeholder notification"],
        tags=["checklist", "templates", "procedures"],
    ),
    EvalCase50(
        id="sre-14",
        question="What steps are required to emergency-rotate an expired Ingress TLS secret to restore HTTPS traffic?",
        category="sre",
        expected_sources=[
            "sre/incidents/simulated-case-study-cert-expiration-outage.md",
            "sre/runbooks/security/certificate-tls-failures.md",
            "sre/runbooks/kubernetes/certificate-expiration.md",
        ],
        expected_sections=["Emergency Remediation Procedure", "Verification"],
        expected_facts=["tls.crt", "tls.key", "kubectl create secret tls", "cert-manager"],
        tags=["incident", "security", "certificates"],
    ),
    EvalCase50(
        id="sre-15",
        question="How should an SRE postmortem document the incident timeline, timeline gaps, and contributing factors?",
        category="sre",
        expected_sources=[
            "sre/templates/postmortem-template.md",
            "sre/response-procedures/incident-timeline.md",
        ],
        expected_sections=["Incident Timeline Template", "Contributing Factors"],
        expected_facts=["timestamp", "detection time", "mitigation time", "root cause", "contributing factors"],
        tags=["postmortem", "templates", "documentation"],
    ),
]
