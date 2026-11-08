---
title: "Runbook: ImagePullBackOff and ErrImagePull Triage"
incident_id: "RB-K8S-002"
severity: "P2"
category: "runbooks/kubernetes"
component: "kubelet-cri"
document_type: "runbook"
doc_type: "runbook"
source_type: "project-authored-sre"
applicable_versions: "Kubernetes 1.28-1.37"
authoring_date: "2026-11-08"
spdx_license: "Apache-2.0"
attribution: "Enterprise SRE Copilot Authors"
content_hash: "2a31297eff4faf77819aa995f132a173344e525a563b34b8653da4d61e304cef"
related_upstream_urls:
  - "https://kubernetes.io/docs/concepts/containers/images/"
  - "https://kubernetes.io/docs/tasks/configure-pod-container/pull-image-private-registry/"
---

# Runbook: ImagePullBackOff and ErrImagePull Triage

## 1. Incident Identifier
- **ID:** RB-K8S-002
- **Severity:** P2 (High) — Container runtime cannot pull the designated image, preventing pod startup.

## 2. Severity Classification
- **P1:** New deployment rollout blocked during emergency patch or automated auto-scaling failure during traffic surge.
- **P2:** Stalled deployment update or canary pod unable to pull image while existing pods remain functional.
- **P3:** Staging or development cluster image pull failure.

## 3. Symptoms
- `kubectl get pods` shows `STATUS: ImagePullBackOff` or `ErrImagePull`.
- Deployment rollouts stall indefinitely waiting for new replica pods to initialize.
- Kubelet event logs report `Failed to pull image ... rpc error: code = NotFound` or `401 Unauthorized`.

## 4. Common Causes
1. **Typo in Image Tag or Name:** Incorrect image registry URL, repository name, or nonexistent tag (e.g., `v1.2.3` instead of `v1.2.4`).
2. **Missing or Expired ImagePullSecret:** PodSpec lacks `imagePullSecrets`, or docker-registry secret credentials have expired.
3. **Registry Outage or Rate Limiting:** Docker Hub / ECR / GCR / Harbor throttling or network outage.
4. **Node Network / DNS Failure:** Node hosting the pod cannot resolve registry hostname or route outbound internet traffic.
5. **Architecture Mismatch:** Container image built for `linux/arm64` scheduled on a `linux/amd64` node or vice versa.

## 5. Impact Assessment
- Deployment rollouts stall, preventing code releases or security patches from propagating.
- Cluster auto-scaler may scale up nodes that cannot start pods, wasting compute resources.

## 6. Diagnostic Procedure
1. Inspect the pod's `Events` block for exact CRI error message.
2. Verify image tag existence in target container registry.
3. Verify presence and validity of `imagePullSecrets`.
4. Verify node outbound connectivity to container registry.

## 7. Read-Only Investigation Commands
```bash
# 1. Retrieve the exact failure reason from pod events
kubectl describe pod <pod-name> -n <namespace> | grep -E "(Image|Failed|Error|Back-off)"

# 2. Inspect the exact image string specified in PodSpec
kubectl get pod <pod-name> -n <namespace> -o jsonpath='{.spec.containers[*].image}'

# 3. Check configured imagePullSecrets on the pod
kubectl get pod <pod-name> -n <namespace> -o jsonpath='{.spec.imagePullSecrets[*].name}'

# 4. Check if the specified Secret exists and is populated
kubectl get secret <secret-name> -n <namespace> -o jsonpath='{.data.\.dockerconfigjson}' | base64 --decode | jq .

# 5. Check if the default ServiceAccount in the namespace has imagePullSecrets attached
kubectl get serviceaccount default -n <namespace> -o yaml
```

## 8. Expected Outputs
- `rpc error: code = NotFound desc = failed to pull and unpack image ... not found`: The tag or repository does not exist in registry.
- `rpc error: code = Unknown desc = failed to pull and unpack image ... 401 Unauthorized`: Missing or invalid registry credentials.
- `rpc error: code = Unknown desc = failed to pull ... net/http: TLS handshake timeout`: Node DNS or firewall blocking egress to registry.
- `no matching manifest for linux/amd64 in the manifest list entries`: Multi-arch build missing target architecture.

## 9. Decision Tree
```
                    [ImagePullBackOff Detected]
                                 |
                     Inspect Describe Pod Events
                                 |
         +-----------------------+-----------------------+
         |                       |                       |
   [404 Not Found]         [401 Unauthorized]       [TLS/DNS Timeout]
         |                       |                       |
   Verify CI/CD pipeline   Check imagePullSecrets;  Test node egress DNS:
   built & pushed tag      renew registry tokens    check security groups
```

## 10. Remediation Steps

### Action A: Fix Image Tag in Deployment (Safe)
```bash
# Update container image to the correct existing tag
kubectl set image deployment/<deployment-name> <container-name>=<valid-image-url>:<valid-tag> -n <namespace>
```

### Action B: Recreate or Patch ImagePullSecret (Safe)
```bash
# 1. Recreate docker-registry secret with fresh auth credentials
kubectl create secret docker-registry <secret-name>   --docker-server=<registry-server>   --docker-username=<user>   --docker-password=<token>   --docker-email=<email>   -n <namespace> --dry-run=client -o yaml | kubectl apply -f -

# 2. Patch Deployment or ServiceAccount to utilize the secret
kubectl patch serviceaccount default -n <namespace> -p '{"imagePullSecrets": [{"name": "'<secret-name>'"}]}'
```

### Action C: Force Kubelet Re-evaluation (Safe)
```bash
# Delete the failing pod so deployment controller launches fresh pod with updated secrets
kubectl delete pod <pod-name> -n <namespace>
```

## 11. Verification After Remediation
1. Confirm pod transitions from `ContainerCreating` directly to `Running`:
   ```bash
   kubectl get pod -n <namespace> -l app=<app-label> -w
   ```
2. Verify image pull event reports `Successfully pulled image ... in 4.2s`.

## 12. Rollback Procedure
If newly specified image introduces regression:
```bash
kubectl rollout undo deployment/<deployment-name> -n <namespace>
```

## 13. Escalation Criteria
- If registry is internal (Harbor/Artifactory) and unreachable, escalate to DevOps / Infrastructure team.
- If third-party public registry is throttling entire cluster IP range, escalate to Network/Security team to configure pull-through caching mirror.

## 14. Prevention Recommendations
1. Enforce automated image build & push verification in CI before initiating Kubernetes deployment manifests.
2. Avoid using `latest` tag; always use immutable semantic versions or git commit SHA tags.
3. Configure in-cluster registry mirrors (e.g., Spegel, Harbor pull-through cache) to prevent public registry rate-limiting.

## 15. Related Documentation References
- Kubernetes Container Images: `https://kubernetes.io/docs/concepts/containers/images/`
- Private Registry Auth: `https://kubernetes.io/docs/tasks/configure-pod-container/pull-image-private-registry/`
