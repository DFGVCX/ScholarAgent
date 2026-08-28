"""Allow source-code chunks produced by the hierarchical parser.

Revision ID: 20260828_0006
Revises: 20260828_0005
"""
from alembic import op


revision = "20260828_0006"
down_revision = "20260828_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE paper_chunks DROP CONSTRAINT IF EXISTS ck_paper_chunks_type")
    op.execute(
        "ALTER TABLE paper_chunks ADD CONSTRAINT ck_paper_chunks_type "
        "CHECK (chunk_type IN ('prose','equation','table','figure','algorithm','code'))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE paper_chunks DROP CONSTRAINT IF EXISTS ck_paper_chunks_type")
    op.execute(
        "ALTER TABLE paper_chunks ADD CONSTRAINT ck_paper_chunks_type "
        "CHECK (chunk_type IN ('prose','equation','table','figure','algorithm'))"
    )
