"""Audit logging and record-keeping for SQL proposals and human approval decisions.

Provides an auditable, append-only history of proposals, approval/rejection decisions,
expiry timestamps, execution outcomes, and actors backed by persistent database storage
with in-process caching.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from advanced_rag.models import ApprovalStatus, SqlProposal

logger = logging.getLogger(__name__)


@dataclass
class ApprovalAuditRecord:
    proposal_id: str
    thread_id: str
    sql_hash: str
    sql_query: str
    tables: list[str]
    status: str
    requested_at: str
    expires_at: str
    decided_at: str | None = None
    actor: str | None = None
    rejection_reason: str | None = None
    execution_started_at: str | None = None
    execution_completed_at: str | None = None
    execution_outcome: str | None = None
    dialect: str = "sqlite"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ApprovalAuditStore:
    """Thread-safe persistent audit log for human approval lifecycle tracking.

    When persist_db=True, persists all lifecycle events into the database
    `approval_audit` table while keeping an in-memory cache for fast lookups.
    When persist_db=False, operates strictly in-memory.
    """

    def __init__(self, persist_db: bool = False) -> None:
        self.persist_db = persist_db
        self._lock = threading.Lock()
        self._records: dict[str, ApprovalAuditRecord] = {}
        self._history: list[str] = []  # Ordered proposal_ids

    def _persist_event(
        self,
        event_type: str,
        proposal_id: str,
        thread_id: str,
        sql_hash: str,
        sql_query: str,
        dialect: str = "sqlite",
        tables: list[str] | None = None,
        actor: str | None = None,
        rejection_reason: str | None = None,
        event_timestamp: datetime | None = None,
        execution_started_at: datetime | None = None,
        execution_completed_at: datetime | None = None,
        execution_outcome: str | None = None,
    ) -> None:
        """Safely write an audit event record to the database."""
        if not self.persist_db:
            return
        try:
            from advanced_rag.db.models import ApprovalAuditEventRecord
            from advanced_rag.db.session import get_db_session

            tables_json = json.dumps(tables or [])
            ts = event_timestamp or datetime.now(UTC)
            with get_db_session() as session:
                rec = ApprovalAuditEventRecord(
                    proposal_id=proposal_id,
                    thread_id=thread_id,
                    event_type=event_type,
                    sql_hash=sql_hash,
                    sql_query=sql_query,
                    dialect=dialect,
                    tables_used=tables_json,
                    actor=actor,
                    rejection_reason=rejection_reason,
                    event_timestamp=ts,
                    execution_started_at=execution_started_at,
                    execution_completed_at=execution_completed_at,
                    execution_outcome=execution_outcome,
                )
                session.add(rec)
        except Exception as exc:
            logger.debug("Database audit event persistence skipped/failed: %s", exc)

    def record_proposal(
        self,
        proposal: SqlProposal | None = None,
        thread_id: str = "",
        *,
        proposal_id: str | None = None,
        sql: str | None = None,
        sql_hash: str | None = None,
        dialect: str = "sqlite",
        tables: list[str] | None = None,
        expires_at: str | None = None,
    ) -> ApprovalAuditRecord:
        with self._lock:
            pid = proposal.proposal_id if proposal else (proposal_id or uuid.uuid4().hex)
            s_hash = proposal.sql_hash if proposal else (sql_hash or "")
            s_query = proposal.sql if proposal else (sql or "")
            s_tables = list(proposal.tables) if proposal else list(tables or [])
            s_status = proposal.status.value if proposal else ApprovalStatus.PENDING.value
            s_req = (
                (proposal.requested_at if proposal else "")
                or datetime.now(UTC).isoformat()
            )
            s_exp = proposal.expires_at if proposal else (expires_at or "")

            rec = ApprovalAuditRecord(
                proposal_id=pid,
                thread_id=thread_id,
                sql_hash=s_hash,
                sql_query=s_query,
                tables=s_tables,
                status=s_status,
                requested_at=s_req,
                expires_at=s_exp,
                dialect=dialect,
            )
            self._records[pid] = rec
            if pid not in self._history:
                self._history.append(pid)

        self._persist_event(
            event_type="proposed",
            proposal_id=pid,
            thread_id=thread_id,
            sql_hash=s_hash,
            sql_query=s_query,
            dialect=dialect,
            tables=s_tables,
        )
        logger.info(
            "Audit: Proposal %s created for thread %s [hash=%s, tables=%s]",
            pid,
            thread_id,
            s_hash[:12] if s_hash else "",
            s_tables,
        )
        return rec

    def record_decision(
        self,
        proposal_id: str,
        status: ApprovalStatus,
        *,
        actor: str | None = None,
        reason: str | None = None,
        decided_at: str | None = None,
    ) -> ApprovalAuditRecord | None:
        rec = self.get(proposal_id)
        if not rec:
            return None

        with self._lock:
            rec.status = status.value
            rec.decided_at = decided_at or datetime.now(UTC).isoformat()
            if actor:
                rec.actor = actor
            if reason:
                rec.rejection_reason = reason
            self._records[proposal_id] = rec

        self._persist_event(
            event_type=status.value,
            proposal_id=proposal_id,
            thread_id=rec.thread_id,
            sql_hash=rec.sql_hash,
            sql_query=rec.sql_query,
            dialect=rec.dialect,
            tables=rec.tables,
            actor=actor,
            rejection_reason=reason,
        )
        logger.info(
            "Audit: Proposal %s decision -> %s (actor=%s)", proposal_id, status.value, actor
        )
        return rec

    def record_execution_start(
        self, proposal_id: str, started_at: str | None = None
    ) -> ApprovalAuditRecord | None:
        rec = self.get(proposal_id)
        if not rec:
            return None

        iso_now = started_at or datetime.now(UTC).isoformat()
        with self._lock:
            rec.status = ApprovalStatus.EXECUTING.value
            rec.execution_started_at = iso_now
            self._records[proposal_id] = rec

        dt_started = datetime.fromisoformat(iso_now) if iso_now else None
        self._persist_event(
            event_type=ApprovalStatus.EXECUTING.value,
            proposal_id=proposal_id,
            thread_id=rec.thread_id,
            sql_hash=rec.sql_hash,
            sql_query=rec.sql_query,
            dialect=rec.dialect,
            tables=rec.tables,
            actor=rec.actor,
            execution_started_at=dt_started,
        )
        return rec

    def record_execution_end(
        self,
        proposal_id: str,
        status: ApprovalStatus,
        outcome: str,
        completed_at: str | None = None,
    ) -> ApprovalAuditRecord | None:
        rec = self.get(proposal_id)
        if not rec:
            return None

        iso_now = completed_at or datetime.now(UTC).isoformat()
        with self._lock:
            rec.status = status.value
            rec.execution_completed_at = iso_now
            rec.execution_outcome = outcome
            self._records[proposal_id] = rec

        dt_completed = datetime.fromisoformat(iso_now) if iso_now else None
        self._persist_event(
            event_type=status.value,
            proposal_id=proposal_id,
            thread_id=rec.thread_id,
            sql_hash=rec.sql_hash,
            sql_query=rec.sql_query,
            dialect=rec.dialect,
            tables=rec.tables,
            actor=rec.actor,
            execution_completed_at=dt_completed,
            execution_outcome=outcome,
        )
        logger.info("Audit: Proposal %s execution complete -> %s", proposal_id, status.value)
        return rec

    def _reconstruct_from_db_events(self, proposal_id: str) -> ApprovalAuditRecord | None:
        """Attempt to restore a proposal record from the database events."""
        if not self.persist_db:
            return None
        try:
            from advanced_rag.db.models import ApprovalAuditEventRecord
            from advanced_rag.db.session import get_db_session

            with get_db_session() as session:
                stmt = (
                    select(ApprovalAuditEventRecord)
                    .where(ApprovalAuditEventRecord.proposal_id == proposal_id)
                    .order_by(ApprovalAuditEventRecord.event_timestamp.asc())
                )
                rows = session.scalars(stmt).all()
                if not rows:
                    return None

                first = rows[0]
                tables = []
                try:
                    tables = json.loads(first.tables_used)
                except Exception:
                    pass

                last = rows[-1]
                rec = ApprovalAuditRecord(
                    proposal_id=first.proposal_id,
                    thread_id=first.thread_id,
                    sql_hash=first.sql_hash,
                    sql_query=first.sql_query,
                    tables=tables,
                    status=last.event_type,
                    requested_at=first.event_timestamp.isoformat() if first.event_timestamp else "",
                    expires_at="",
                    dialect=first.dialect,
                )

                for r in rows:
                    if r.actor:
                        rec.actor = r.actor
                    if r.rejection_reason:
                        rec.rejection_reason = r.rejection_reason
                    if r.event_type in (
                        ApprovalStatus.APPROVED.value,
                        ApprovalStatus.REJECTED.value,
                    ):
                        rec.decided_at = (
                            r.event_timestamp.isoformat() if r.event_timestamp else None
                        )
                    if r.execution_started_at:
                        rec.execution_started_at = r.execution_started_at.isoformat()
                    if r.execution_completed_at:
                        rec.execution_completed_at = r.execution_completed_at.isoformat()
                    if r.execution_outcome:
                        rec.execution_outcome = r.execution_outcome
                return rec
        except Exception as exc:
            logger.debug("Reconstructing audit from db failed: %s", exc)
            return None

    def get(self, proposal_id: str) -> ApprovalAuditRecord | None:
        with self._lock:
            rec = self._records.get(proposal_id)
            if rec:
                return rec

        # Check DB if not present in-memory
        from_db = self._reconstruct_from_db_events(proposal_id)
        if from_db:
            with self._lock:
                self._records[proposal_id] = from_db
                if proposal_id not in self._history:
                    self._history.append(proposal_id)
            return from_db
        return None

    def get_by_thread(self, thread_id: str) -> list[ApprovalAuditRecord]:
        return self.list_by_thread(thread_id)

    def list_by_thread(self, thread_id: str) -> list[ApprovalAuditRecord]:
        results: list[ApprovalAuditRecord] = []
        if self.persist_db:
            try:
                from advanced_rag.db.models import ApprovalAuditEventRecord
                from advanced_rag.db.session import get_db_session

                with get_db_session() as session:
                    stmt = (
                        select(ApprovalAuditEventRecord.proposal_id)
                        .where(ApprovalAuditEventRecord.thread_id == thread_id)
                        .distinct()
                    )
                    prop_ids = session.scalars(stmt).all()
                    for pid in prop_ids:
                        r = self.get(pid)
                        if r and r not in results:
                            results.append(r)
            except Exception:
                pass

        with self._lock:
            for rec in self._records.values():
                if rec.thread_id == thread_id and rec not in results:
                    results.append(rec)
        return results

    def list_recent(self, limit: int = 50) -> list[ApprovalAuditRecord]:
        results: list[ApprovalAuditRecord] = []
        if self.persist_db:
            try:
                from advanced_rag.db.models import ApprovalAuditEventRecord
                from advanced_rag.db.session import get_db_session

                with get_db_session() as session:
                    stmt = (
                        select(ApprovalAuditEventRecord.proposal_id)
                        .order_by(ApprovalAuditEventRecord.event_timestamp.desc())
                        .distinct()
                        .limit(limit)
                    )
                    prop_ids = session.scalars(stmt).all()
                    for pid in prop_ids:
                        r = self.get(pid)
                        if r and r not in results:
                            results.append(r)
            except Exception:
                pass

        with self._lock:
            ids = self._history[-limit:]
            for pid in reversed(ids):
                if pid in self._records:
                    rec = self._records[pid]
                    if rec not in results:
                        results.append(rec)
        return results[:limit]

    def clear(self) -> None:
        with self._lock:
            self._records.clear()
            self._history.clear()


_audit_store = ApprovalAuditStore(persist_db=True)


def get_audit_store() -> ApprovalAuditStore:
    return _audit_store
