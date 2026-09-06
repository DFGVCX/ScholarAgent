"""Fence PDF ingestion jobs by upload generation and worker lease.

Revision ID: 20260901_0009
Revises: 20260901_0008
"""

from alembic import op


revision = "20260901_0009"
down_revision = "20260901_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE papers ADD COLUMN ingestion_generation UUID")
    op.execute("ALTER TABLE paper_ingestion_jobs ADD COLUMN generation_uuid UUID")
    op.execute("ALTER TABLE paper_ingestion_jobs ADD COLUMN asset_sha256 TEXT")
    op.execute("ALTER TABLE paper_ingestion_jobs ADD COLUMN lease_token UUID")
    op.execute(
        "UPDATE paper_ingestion_jobs "
        "SET generation_uuid=gen_random_uuid(), "
        "asset_sha256=NULLIF(payload->>'file_sha256', '') "
        "WHERE job_type='ingest_pdf' AND status IN ('pending','running','retry')"
    )
    op.execute(
        "UPDATE papers p SET ingestion_generation=("
        "SELECT j.generation_uuid FROM paper_ingestion_jobs j "
        "WHERE j.tenant_id=p.tenant_id AND j.user_id=p.user_id "
        "AND j.paper_uuid=p.paper_uuid AND j.job_type='ingest_pdf' "
        "AND j.status IN ('pending','running','retry') "
        "ORDER BY j.created_at DESC LIMIT 1) "
        "WHERE EXISTS (SELECT 1 FROM paper_ingestion_jobs j "
        "WHERE j.tenant_id=p.tenant_id AND j.user_id=p.user_id "
        "AND j.paper_uuid=p.paper_uuid AND j.job_type='ingest_pdf' "
        "AND j.status IN ('pending','running','retry'))"
    )
    op.execute("DROP INDEX IF EXISTS uq_active_pdf_ingestion_job")
    op.execute(
        "CREATE UNIQUE INDEX uq_waiting_pdf_ingestion_job "
        "ON paper_ingestion_jobs(tenant_id, user_id, paper_uuid, job_type) "
        "WHERE job_type='ingest_pdf' AND status IN ('pending','retry')"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_waiting_pdf_ingestion_job")
    op.execute(
        "WITH ranked AS ("
        "SELECT job_uuid, ROW_NUMBER() OVER ("
        "PARTITION BY tenant_id, user_id, paper_uuid, job_type "
        "ORDER BY updated_at DESC, created_at DESC, job_uuid DESC"
        ") AS active_rank FROM paper_ingestion_jobs "
        "WHERE job_type='ingest_pdf' AND status IN ('pending','running','retry')"
        ") UPDATE paper_ingestion_jobs jobs SET status='failed', "
        "completed_at=now(), locked_at=NULL, locked_by=NULL, lease_token=NULL, "
        "last_error='cancelled by migration downgrade', updated_at=now() "
        "FROM ranked WHERE jobs.job_uuid=ranked.job_uuid AND ranked.active_rank > 1"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_active_pdf_ingestion_job "
        "ON paper_ingestion_jobs(tenant_id, user_id, paper_uuid, job_type) "
        "WHERE job_type='ingest_pdf' AND status IN ('pending','running','retry')"
    )
    op.execute("ALTER TABLE paper_ingestion_jobs DROP COLUMN lease_token")
    op.execute("ALTER TABLE paper_ingestion_jobs DROP COLUMN asset_sha256")
    op.execute("ALTER TABLE paper_ingestion_jobs DROP COLUMN generation_uuid")
    op.execute("ALTER TABLE papers DROP COLUMN ingestion_generation")
