"""Make queued PDF content commits idempotent per upload generation.

Revision ID: 20260901_0010
Revises: 20260901_0009
"""

from alembic import op


revision = "20260901_0010"
down_revision = "20260901_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE paper_contents ADD COLUMN ingestion_generation UUID")
    op.execute(
        "CREATE UNIQUE INDEX uq_paper_content_ingestion_generation "
        "ON paper_contents(tenant_id, user_id, paper_uuid, ingestion_generation) "
        "WHERE ingestion_generation IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_paper_content_ingestion_generation")
    op.execute("ALTER TABLE paper_contents DROP COLUMN ingestion_generation")
