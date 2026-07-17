"""Prevent duplicate active re-embedding jobs.

Revision ID: 20260717_0003
Revises: 20260717_0002
"""

from alembic import op


revision = "20260717_0003"
down_revision = "20260717_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE UNIQUE INDEX uq_active_reembedding_job "
        "ON paper_ingestion_jobs(tenant_id, user_id, paper_uuid, job_type) "
        "WHERE job_type='reembed' AND status IN ('pending','running','retry')"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_active_reembedding_job")
