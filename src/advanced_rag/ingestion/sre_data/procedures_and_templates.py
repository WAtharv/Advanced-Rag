"""Operational Incident Response Procedures and Standard Templates (SRE-PROC-001 through SRE-PROC-009, plus 3 templates).

Covers:
- Incident declaration, severity assignment, initial triage, evidence collection
- Incident timeline, mitigation and recovery, communication and escalation
- Resolution verification, incident closure
- Incident response template, declaration checklist, postmortem template
"""

from __future__ import annotations

RESPONSE_PROCEDURES: list[dict[str, any]] = [
    {
        "category": "response-procedures",
        "name": "incident-declaration.md",
        "title": "Standard Procedure: Incident Declaration and Initial Assessment",
        "incident_id": "SRE-PROC-001",
        "severity": "Standard",
        "component": "incident-framework",
        "applicable_versions": "All Environments",
        "related_upstream_urls": [],
        "content": """# Standard Procedure: Incident Declaration and Initial Assessment

## 1. Purpose
Define the mandatory operational steps for declaring an active production incident, establishing initial communication channels, and appointing the Incident Commander (IC).

## 2. Trigger Criteria
An incident MUST be formally declared when any of the following occur:
- Automated P1/P2 alert fires without auto-recovery within 5 minutes.
- User-facing error rate exceeds SLA threshold (>1% 5xx errors for >3 minutes).
- Security breach, unauthorized access, or credential exposure is detected.
- External dependency outage halts payment, checkout, or core user journeys.

## 3. Initial Assessment (First 5 Minutes)
1. **Confirm Production Impact:** Is the issue affecting live users, data integrity, or core SLAs?
2. **Determine Initial Scope:** Which services, regions, and clusters are degraded?
3. **Appoint Incident Commander (IC):** The on-call engineer who acknowledges the page assumes the role of IC until explicitly handed off.

## 4. Declaration Actions
Execute within 5 minutes of confirmation:
```bash
# 1. Open dedicated incident bridge (Zoom/Meet/Slack)
# Format: #incident-<YYYYMMDD>-<service-name>

# 2. Post Initial Declaration Message:
# "INCIDENT DECLARED: [P1/P2] <Short Description>
#  Incident Commander: @username
#  Bridge: <link>
#  Impacted Services: <service-list>
#  Initial Symptoms: <brief bullet points>"
```

## 5. Escalation Rules
If impact is confirmed as P1, immediately notify the Incident Commander On-Call and Engineering Lead.
""",
    },
    {
        "category": "response-procedures",
        "name": "severity-assignment.md",
        "title": "Standard Procedure: Severity Assignment Matrix (P1-P4) and SLA Standards",
        "incident_id": "SRE-PROC-002",
        "severity": "Standard",
        "component": "incident-framework",
        "applicable_versions": "All Environments",
        "related_upstream_urls": [],
        "content": """# Standard Procedure: Severity Assignment Matrix (P1-P4) and SLA Standards

## 1. Severity Classification Matrix

| Severity Level | Definition | Impact Scope | Response SLA | Update Cadence |
| :--- | :--- | :--- | :--- | :--- |
| **P1 (Critical)** | Core service down; catastrophic data loss; critical security breach. | >25% of users impacted or core transaction pipeline halted. | Immediate (<5 min page ack) | Every 15 minutes |
| **P2 (High)** | Major functionality degraded; no immediate workaround; redundancy lost. | 5-25% of users impacted; single AZ failure; database secondary down. | <15 min page ack | Every 30 minutes |
| **P3 (Medium)** | Minor feature impacted; viable workaround exists; internal tooling broken. | Non-critical path or isolated subset (<5%) of users. | <1 hour response | Every 2 hours |
| **P4 (Low)** | Cosmetic issue, minor bug, non-urgent maintenance alert. | No end-user impact. | Next business day | On resolution |

## 2. Severity Reclassification
- **Upgrading:** If a P2 incident shows signs of cascading failure or user complaints surge, the IC must immediately upgrade to P1.
- **Downgrading:** Severity can be downgraded only after mitigation is confirmed and error rates return to normal.
""",
    },
    {
        "category": "response-procedures",
        "name": "incident-triage.md",
        "title": "Standard Procedure: Initial Triage and Role Handover Protocol",
        "incident_id": "SRE-PROC-003",
        "severity": "Standard",
        "component": "incident-framework",
        "applicable_versions": "All Environments",
        "related_upstream_urls": [],
        "content": """# Standard Procedure: Initial Triage and Role Handover Protocol

## 1. Incident Command Roles
For P1 and complex P2 incidents, the following roles must be assigned:
1. **Incident Commander (IC):** Leads response, coordinates actions, assigns tasks, protects responders from distractions. Does NOT troubleshoot directly.
2. **Operations Lead (Ops Lead):** Executes diagnostics, reads telemetry, implements remediation actions.
3. **Communications Lead (Comms Lead):** Writes status updates, notifies stakeholders, coordinates public status page.
4. **Scribe:** Maintains the chronological incident timeline, logs commands executed, and records findings.

## 2. Role Handover Protocol
Handovers must occur verbally on the bridge and be recorded in the incident channel:
```markdown
ROLE HANDOVER:
- Outgoing IC: @alice
- Incoming IC: @bob
- Current Status: Mitigation in progress (rollout undo initiated)
- Next Action: Awaiting replica readiness verification
- Accepted by: @bob at 14:32 UTC
```
""",
    },
    {
        "category": "response-procedures",
        "name": "evidence-collection.md",
        "title": "Standard Procedure: Non-Destructive Evidence Collection and State Snapshotting",
        "incident_id": "SRE-PROC-004",
        "severity": "Standard",
        "component": "incident-framework",
        "applicable_versions": "All Environments",
        "related_upstream_urls": [],
        "content": """# Standard Procedure: Non-Destructive Evidence Collection and State Snapshotting

## 1. Principle
Never restart a pod, drain a node, or delete a resource without first preserving volatile runtime evidence.

## 2. Mandatory Evidence Snapshot Checklist
Before mutating or restarting failed workloads:
```bash
# 1. Capture pod events and describe state
kubectl describe pod <pod-name> -n <namespace> > /tmp/evidence-<pod-name>-describe.txt

# 2. Capture current and previous container logs
kubectl logs <pod-name> -n <namespace> --all-containers --tail=500 > /tmp/evidence-<pod-name>-current.log
kubectl logs <pod-name> -n <namespace> --all-containers --previous --tail=500 > /tmp/evidence-<pod-name>-previous.log

# 3. Capture namespace events
kubectl get events -n <namespace> --sort-by=.lastTimestamp > /tmp/evidence-<namespace>-events.txt

# 4. Capture resource YAML definition
kubectl get pod <pod-name> -n <namespace> -o yaml > /tmp/evidence-<pod-name>-spec.yaml
```

## 3. Storage
Upload all evidence files to the designated incident evidence bucket or attach to the postmortem ticket.
""",
    },
    {
        "category": "response-procedures",
        "name": "incident-timeline.md",
        "title": "Standard Procedure: Incident Timeline Tracking and Log Recording",
        "incident_id": "SRE-PROC-005",
        "severity": "Standard",
        "component": "incident-framework",
        "applicable_versions": "All Environments",
        "related_upstream_urls": [],
        "content": """# Standard Procedure: Incident Timeline Tracking and Log Recording

## 1. Timeline Standard
All timestamps must be recorded in UTC in ISO-8601 format (`YYYY-MM-DD HH:MM:SS UTC`).

## 2. Key Milestones to Record
1. **T0 (Incident Inception):** When the underlying failure began (derived from metrics retrospectively).
2. **T_detect (Detection):** When alert fired or customer report arrived.
3. **T_declare (Declaration):** When incident was declared and IC assigned.
4. **T_mitigate (Mitigation):** When mitigation action took effect and customer impact ceased.
5. **T_resolve (Resolution):** When root cause was corrected and soak period ended.

## 3. Real-Time Timeline Format
```markdown
- 14:02 UTC: Prometheus alert KubePodCrashLooping starts firing for payment-service.
- 14:05 UTC: On-call acknowledges alert; declares P1 incident.
- 14:10 UTC: Ops Lead identifies Exit Code 137 (OOMKilled) after recent v2.4.1 release.
- 14:15 UTC: Rollback initiated to v2.4.0 via `kubectl rollout undo`.
- 14:20 UTC: Replicas achieve 1/1 READY; error rate drops to 0%.
- 14:35 UTC: 15-minute soak period completed; mitigation verified.
```
""",
    },
    {
        "category": "response-procedures",
        "name": "mitigation-and-recovery.md",
        "title": "Standard Procedure: Mitigation Strategies, Rollbacks, and Safe Restoral",
        "incident_id": "SRE-PROC-006",
        "severity": "Standard",
        "component": "incident-framework",
        "applicable_versions": "All Environments",
        "related_upstream_urls": [],
        "content": """# Standard Procedure: Mitigation Strategies, Rollbacks, and Safe Restoral

## 1. Philosophy: Mitigate First, Root-Cause Later
During an active P1/P2 incident, the sole objective is to restore customer service as rapidly and safely as possible. Deep root cause analysis occurs during the postmortem.

## 2. Standard Mitigation Hierarchy
Order of preference for rapid mitigation:
1. **Rollback Recent Deployment:** Revert code or config change to last known healthy state.
2. **Scale Up Compute Capacity:** Add replicas or worker nodes to absorb traffic pressure.
3. **Route Traffic Away (Failover):** Divert ingress traffic to alternate healthy region or cluster.
4. **Shed Load / Degrade Gracefully:** Disable non-critical features via feature flags (e.g. recommendation engine, search autocomplete).
5. **Restart Degraded Component:** Only after capturing evidence.

## 3. Rollback Safety Rules
- Verify database backwards compatibility before rolling back application code.
- If database migration cannot be rolled back, use forward-fix or feature flag disabling.
""",
    },
    {
        "category": "response-procedures",
        "name": "communication-and-escalation.md",
        "title": "Standard Procedure: Stakeholder Communications and Escalation Matrix",
        "incident_id": "SRE-PROC-007",
        "severity": "Standard",
        "component": "incident-framework",
        "applicable_versions": "All Environments",
        "related_upstream_urls": [],
        "content": """# Standard Procedure: Stakeholder Communications and Escalation Matrix

## 1. Stakeholder Communication Cadence
- **P1 Incidents:** Internal update every 15 minutes; public status page update within 15 minutes of declaration.
- **P2 Incidents:** Internal update every 30 minutes; public status page if user-visible.

## 2. Standard Status Update Template
```markdown
CURRENT STATUS UPDATE: [INVESTIGATING / MITIGATING / MONITORING / RESOLVED]
- Incident: [P1/P2] <Title>
- Summary: <1-2 sentences on what is happening>
- Customer Impact: <% of users affected, specific error symptoms>
- Current Actions: <What the team is actively doing right now>
- Next Update: <Time in UTC, e.g. 15:00 UTC>
```

## 3. Escalation Contacts
- Secondary On-Call: Auto-escalated after 10 minutes of unacknowledged page.
- Engineering Manager: Escalated after 30 minutes of unresolved P1.
- VP / Executive On-Call: Escalated after 60 minutes of ongoing P1 outage.
""",
    },
    {
        "category": "response-procedures",
        "name": "resolution-verification.md",
        "title": "Standard Procedure: Post-Mitigation Verification and Soak Period Standards",
        "incident_id": "SRE-PROC-008",
        "severity": "Standard",
        "component": "incident-framework",
        "applicable_versions": "All Environments",
        "related_upstream_urls": [],
        "content": """# Standard Procedure: Post-Mitigation Verification and Soak Period Standards

## 1. Purpose
Prevent premature incident closure by enforcing rigorous verification and a mandatory observational soak period.

## 2. Mandatory Verification Steps
Before declaring an incident mitigated:
1. **User-Facing Telemetry:** 5xx error rates must return below 0.1% baseline across all endpoints.
2. **Latency Metrics:** P95 and P99 latency must return to pre-incident baseline.
3. **Synthetic Canaries:** End-to-end synthetic user transactions must succeed consecutively for 5 runs.
4. **Workload Health:** All deployment pods must report `1/1 READY` with 0 crashlooping restarts.

## 3. Mandatory Soak Period
- **P1 Incidents:** Minimum 30 minutes of continuous normal operation before closing the incident.
- **P2 Incidents:** Minimum 15 minutes of continuous normal operation before closing.

If symptoms recur during the soak period, the incident timer resets immediately.
""",
    },
    {
        "category": "response-procedures",
        "name": "incident-closure.md",
        "title": "Standard Procedure: Incident Closure and Handoff to Postmortem",
        "incident_id": "SRE-PROC-009",
        "severity": "Standard",
        "component": "incident-framework",
        "applicable_versions": "All Environments",
        "related_upstream_urls": [],
        "content": """# Standard Procedure: Incident Closure and Handoff to Postmortem

## 1. Incident Closure Criteria
An incident may be closed ONLY when:
- Mitigation is verified.
- Mandatory soak period has elapsed without recurrence.
- All temporary workarounds are documented.
- Postmortem ticket has been created and assigned to an owner.

## 2. Final Closure Notification
Post to incident channel:
```markdown
INCIDENT RESOLVED: [P1/P2] <Title>
- Resolved at: <Timestamp UTC>
- Total Outage Duration: <X minutes>
- Final Mitigation: <Brief summary of how service was restored>
- Postmortem Owner: @username
- Postmortem Review Date: <Date within 5 business days>
```

## 3. Next Steps
The postmortem owner must draft the blameless postmortem within 48 hours using the standard template.
""",
    },
]

TEMPLATES: list[dict[str, any]] = [
    {
        "category": "templates",
        "name": "incident-response-template.md",
        "title": "Template: Operational Incident Tracking and Handover Document",
        "incident_id": "TMPL-INC-001",
        "severity": "Standard",
        "component": "templates",
        "applicable_versions": "All Environments",
        "related_upstream_urls": [],
        "content": """# Template: Operational Incident Tracking and Handover Document

## 1. Incident Overview
- **Incident Title:** [P1/P2/P3] <Descriptive Title>
- **Incident ID:** INC-YYYYMMDD-XXX
- **Current Severity:** [P1 / P2 / P3 / P4]
- **Current State:** [Investigating / Mitigating / Monitoring / Resolved]
- **Incident Commander (IC):** @name
- **Operations Lead:** @name
- **Communications Lead:** @name
- **Scribe:** @name
- **Incident Channel:** `#incident-YYYYMMDD-name`
- **Bridge Link:** `<url>`

## 2. Impact Summary
- **Impacted Services:** `<service-names>`
- **Impacted Regions / Clusters:** `<regions>`
- **Customer Experience:** `<What are users seeing? Error codes, latency, timeouts>`
- **SLO Breach:** `[Yes / No - Details]`

## 3. Current Hypotheses and Investigation Log
| Timestamp (UTC) | Investigator | Observation / Hypothesis | Command / Action Taken | Outcome |
| :--- | :--- | :--- | :--- | :--- |
| HH:MM | @name | E.g. Pods failing OOM | `kubectl describe pod` | Exit code 137 confirmed |

## 4. Remediation Plan
- [ ] Action 1: `<Immediate mitigation>`
- [ ] Action 2: `<Verification step>`
- [ ] Action 3: `<Rollback contingency>`

## 5. Communications Log
- **HH:MM UTC:** Initial declaration posted to #general-announcements.
- **HH:MM UTC:** Public status page updated.
""",
    },
    {
        "category": "templates",
        "name": "incident-declaration-checklist.md",
        "title": "Template: Incident Declaration and First 15-Minute Triage Checklist",
        "incident_id": "TMPL-INC-002",
        "severity": "Standard",
        "component": "templates",
        "applicable_versions": "All Environments",
        "related_upstream_urls": [],
        "content": """# Template: Incident Declaration and First 15-Minute Triage Checklist

## First 5 Minutes: Declaration & Roles
- [ ] Confirm issue is affecting production workloads or customer traffic.
- [ ] Declare incident in Slack: `/incident declare [P1/P2] <Title>`.
- [ ] Appoint Incident Commander (IC).
- [ ] Open dedicated incident bridge (Zoom/Meet).
- [ ] Assign Operations Lead to drive technical diagnostics.
- [ ] Assign Communications Lead to handle internal/external status updates.

## 5 to 10 Minutes: Triage & Blast Radius
- [ ] Identify which Kubernetes cluster and namespace are affected.
- [ ] Identify if recent deployment / rollout occurred within the last 60 minutes.
- [ ] Capture non-destructive evidence snapshot (logs, describe, events).
- [ ] Determine if blast radius is expanding or isolated.

## 10 to 15 Minutes: First Stakeholder Communication
- [ ] Post first status update to internal stakeholder channel.
- [ ] If P1, update public/customer status page.
- [ ] Formulate initial mitigation strategy (Rollback vs Scale vs Failover).
- [ ] Set timer for next status update (15 minutes for P1, 30 minutes for P2).
""",
    },
    {
        "category": "templates",
        "name": "postmortem-template.md",
        "title": "Template: Blameless Postmortem and Root Cause Analysis",
        "incident_id": "TMPL-INC-003",
        "severity": "Standard",
        "component": "templates",
        "applicable_versions": "All Environments",
        "related_upstream_urls": [],
        "content": """# Template: Blameless Postmortem and Root Cause Analysis

## Incident Summary
- **Incident Title:** <Title>
- **Date of Incident:** YYYY-MM-DD
- **Severity:** P1 / P2
- **Total Duration:** <Hours:Minutes>
- **Incident Commander:** @name
- **Postmortem Author:** @name
- **Service(s) Impacted:** `<services>`

## Executive Summary
<2-3 concise paragraphs explaining what happened, the customer impact, how it was mitigated, and the primary root cause. Written for technical leadership.>

## Customer & Business Impact
- **User Impact:** `<e.g. 14,000 users experienced 500 Internal Server Error during checkout>`
- **Error Rate:** `<Peak error rate percentage>`
- **SLO Impact:** `<Error budget consumed, e.g. 42% of monthly budget>`

## Timeline (UTC)
| Timestamp (UTC) | Event Description |
| :--- | :--- |
| **YYYY-MM-DD HH:MM** | T0: Underlying failure begins (retrospectively identified) |
| **YYYY-MM-DD HH:MM** | Automated alert fires |
| **YYYY-MM-DD HH:MM** | Incident declared; IC assigned |
| **YYYY-MM-DD HH:MM** | Mitigation action initiated |
| **YYYY-MM-DD HH:MM** | Customer impact ceases; service restored |
| **YYYY-MM-DD HH:MM** | Soak period ends; incident closed |

## Root Cause Analysis (5 Whys)
1. **Why did the service fail?** <Answer>
2. **Why?** <Answer>
3. **Why?** <Answer>
4. **Why?** <Answer>
5. **Why?** <Answer (Root Cause)>

## Contributing Factors
- Trigger: `<What triggered the failure today?>`
- Detection Gap: `<Why didn't automated testing or monitoring catch this earlier?>`
- Architectural Factor: `<What system design allowed this failure to cascade?>`

## What Went Well / What Went Poorly
### What Went Well
- Quick rollback execution restored service within 10 minutes of identification.
- Automated alerts notified on-call within 2 minutes.

### What Went Poorly
- Monitoring lacked container memory saturation alerts prior to OOM kill.
- Initial triage was delayed due to missing preStop hooks.

## Action Items (Preventative Measures)
| Action Item | Type | Priority | Owner | Target Date | Tracking Ticket |
| :--- | :--- | :--- | :--- | :--- | :--- |
| Add memory limit alert | Prevent | High | @sre | YYYY-MM-DD | JIRA-101 |
| Implement startupProbe | Mitigate | Med | @dev | YYYY-MM-DD | JIRA-102 |
| Load test new release | Detect | High | @qa | YYYY-MM-DD | JIRA-103 |
""",
    },
]
