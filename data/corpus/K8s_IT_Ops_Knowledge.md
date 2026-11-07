# Kubernetes IT Operations Knowledge Base

## Incident KB-001: Pod CrashLoopBackOff

### Symptoms
- Application pod repeatedly restarts.
- `kubectl get pods` shows `CrashLoopBackOff`.
- Increased application downtime.

### Possible Causes
1. Missing environment variables.
2. Invalid configuration.
3. Database connection failure.
4. Insufficient memory allocation.

### Resolution Steps
```bash
kubectl logs <pod-name> -n production
kubectl describe pod <pod-name> -n production
```

Verify:
- ConfigMap values
- Secret references
- Resource limits
- Database connectivity

### Root Cause
PostgreSQL credentials were rotated but the secret was not updated.

### Preventive Actions
- Implement secret rotation automation.
- Configure Prometheus alerts for pod restart spikes.

---

## Incident KB-002: High API Latency

### Symptoms
- API response time exceeds 5 seconds.
- Grafana dashboard shows latency spikes.
- User complaints increase.

### Diagnosis
Check:
- Redis cache hit ratio
- PostgreSQL slow queries
- FastAPI worker utilization

### Resolution
1. Restart Redis cluster.
2. Enable query optimization.
3. Increase FastAPI workers.

### Root Cause
Redis cache eviction caused cache misses and excessive SQL execution.

---

## Incident KB-003: Qdrant Search Performance Issue

### Symptoms
- Vector search latency exceeds 2 seconds.
- RAG responses delayed.

### Investigation

```bash
curl http://qdrant:6333/collections
```

Verify:
- Collection size
- Index status
- Memory consumption

### Resolution
- Increase Qdrant replicas.
- Rebuild vector indexes.
- Optimize chunk size.

### Root Cause
Collection grew beyond expected capacity without reindexing.

---

## Architecture Overview

### Components

- FastAPI
- LangGraph
- PostgreSQL
- Redis
- Qdrant
- Prometheus
- Grafana
- Kubernetes

### Request Flow

1. User submits query.
2. Query passes guardrails validation.
3. Hybrid retrieval executes.
4. BM25 search runs.
5. Vector retrieval runs.
6. Results are reranked.
7. CRAG validation evaluates context quality.
8. Self-RAG reflection verifies the answer.
9. LLM generates response.
10. Response passes output guardrails.

---

## Runbook: PostgreSQL Database Recovery

### Detect Issue

```bash
kubectl get pods -n database
```

### Backup Validation

```bash
pg_restore --list backup.dump
```

### Recovery

```bash
pg_restore -d production_db backup.dump
```

### Validation

```sql
SELECT count(*) FROM incidents;
```

Expected Result:
Database restored *uccessfully.

---

## Runbook: Red*s Cache Failure

### Symptoms
- In*reased response latency
- Cache*miss ratio above 80%

### Validati*n

```bash*redis-cli INFO stats
```

### Reso*ution

```bash*kubectl rollout restart deployment*redis
```

### Monitoring Metrics
*- cache_hit_ratio
- cache*miss_ratio
- response*time_ms

---

## Enterprise Securi*y Policies

### Authentication

Re*uirements:
-*OAuth* authentication
- JWT token valida*ion
- RBAC enforcement

### Author*zation

Roles:
- Admin
- DevOps En*ineer
- Application Owner
- Read*Only User*
### Data Protection

- Encrypt al* secrets.
- Never expose passwords*in logs.
- Apply*PII masking before LLM generation.*
---

*# Sample SQL Metadata

### Inciden* Statistics Table

Table: incident*metrics

Columns:
-*incident_id
- service_name
- sever*ty
- resolution*time_minutes
- created*at

*xample Query

```sql
SELECT
    se*erity,
    AVG(resolution_time_min*tes)
FROM incident_metrics
GROUP B* severity;
```

---

##*Guardrails Policy*
### Block*d Content

- Secrets
- API*Keys
- Passwords
- Credit*Card Numbers*- Internal Access Tokens

### Vali*ation Layers

1. Input Validation
*. Query Classification
3. Prompt I*jection Detection
4. PII Detection*5. Retrieval Validation
6.*Context Re*evance Check
7. Hallucination Dete*tion
8. Output Validation
9.*Audit Logging

---

## Monitoring *lerts

### Critical Alerts

#### P*stgre*QL Down*
*ondition:
- Database unavailable f*r 5 minutes

Action:
- Notify DevO*s Team
- Trigger Recovery Runbook
*#### Redis Down

Condition:
- Cach* unavailable

Action:
- Failover t* secondary instance

#### Qdrant L*tency Alert

Condition:
- Search l*tency > 2000ms

Action:
- Scale ve*tor search cluster

---

*# Disaster*Recovery Objectives

### R*O
15 minutes

### R*O
30 minutes

### Backup*Schedule

* Incremental backups every hour
- *ull backup every day

### Recovery*Validation

- Database*integrity check
- Vector*index validation
- Redis cache war*-up
````*