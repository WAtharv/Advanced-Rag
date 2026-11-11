"""Baseline migration for document metadata and ingestion runs.

Revision ID: 001_baseline_registry
Revises: None
Create Date: 2026-11-11 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "001_baseline_registry"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = inspector.get_table_names()

    # 1. document_metadata table
    if "document_metadata" not in existing_tables:
        op.create_table(
            "document_metadata",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("document_id", sa.String(length=64), nullable=False),
            sa.Column("source", sa.String(length=512), nullable=False),
            sa.Column("title", sa.String(length=256), server_default="", nullable=False),
            sa.Column("file_type", sa.String(length=32), server_default="", nullable=False),
            sa.Column("content_hash", sa.String(length=64), server_default="", nullable=False),
            sa.Column("category", sa.String(length=64), nullable=True),
            sa.Column("component", sa.String(length=64), nullable=True),
            sa.Column("doc_type", sa.String(length=64), server_default="runbook", nullable=False),
            sa.Column("source_url", sa.String(length=1024), nullable=True),
            sa.Column("source_repository", sa.String(length=512), nullable=True),
            sa.Column("source_version", sa.String(length=64), nullable=True),
            sa.Column("source_commit", sa.String(length=64), nullable=True),
            sa.Column("spdx_license", sa.String(length=64), nullable=True),
            sa.Column("attribution", sa.String(length=256), nullable=True),
            sa.Column("document_status", sa.String(length=32), server_default="pending", nullable=False),
            sa.Column("indexed_chunk_count", sa.Integer(), server_default="0", nullable=False),
            sa.Column("collection_name", sa.String(length=64), server_default="sre_copilot_v1", nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_indexed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_document_metadata_document_id", "document_metadata", ["document_id"], unique=True)
        op.create_index("ix_document_metadata_source", "document_metadata", ["source"], unique=False)
        op.create_index("ix_document_metadata_category", "document_metadata", ["category"], unique=False)
        op.create_index("ix_document_metadata_component", "document_metadata", ["component"], unique=False)
        op.create_index("ix_document_metadata_document_status", "document_metadata", ["document_status"], unique=False)
        op.create_index("ix_document_metadata_collection_name", "document_metadata", ["collection_name"], unique=False)
        op.create_index("idx_doc_cat_status", "document_metadata", ["category", "document_status"])
        op.create_index("idx_doc_source_col", "document_metadata", ["source", "collection_name"])

    # 2. ingestion_runs table
    if "ingestion_runs" not in existing_tables:
        op.create_table(
            "ingestion_runs",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("run_id", sa.String(length=64), nullable=False),
            sa.Column("operation_type", sa.String(length=32), nullable=False),
            sa.Column("status", sa.String(length=32), server_default="in_progress", nullable=False),
            sa.Column("collection_name", sa.String(length=64), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("duration_seconds", sa.Float(), nullable=True),
            sa.Column("successful_files", sa.Integer(), server_default="0", nullable=False),
            sa.Column("failed_files", sa.Integer(), server_default="0", nullable=False),
            sa.Column("total_chunks", sa.Integer(), server_default="0", nullable=False),
            sa.Column("details", sa.Text(), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_ingestion_runs_run_id", "ingestion_runs", ["run_id"], unique=True)
        op.create_index("ix_ingestion_runs_status", "ingestion_runs", ["status"], unique=False)


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = inspector.get_table_names()

    if "ingestion_runs" in existing_tables:
        op.drop_table("ingestion_runs")
    if "document_metadata" in existing_tables:
        op.drop_table("document_metadata")
