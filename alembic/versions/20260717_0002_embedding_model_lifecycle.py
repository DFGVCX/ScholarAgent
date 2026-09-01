"""Allow stale embeddings during model transitions.

Revision ID: 20260717_0002
Revises: 20260716_0001
"""

from alembic import op


revision = "20260717_0002"
down_revision = "20260716_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE paper_chunks DROP CONSTRAINT IF EXISTS "
        "paper_chunks_embedding_status_check"
    )
    op.execute(
        "ALTER TABLE paper_chunks ADD CONSTRAINT paper_chunks_embedding_status_check "
        "CHECK (embedding_status IN ('pending','ready','stale','failed'))"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE paper_chunks SET embedding_status='pending', embedding=NULL "
        "WHERE embedding_status='stale'"
    )
    op.execute(
        "ALTER TABLE paper_chunks DROP CONSTRAINT IF EXISTS "
        "paper_chunks_embedding_status_check"
    )
    op.execute(
        "ALTER TABLE paper_chunks ADD CONSTRAINT paper_chunks_embedding_status_check "
        "CHECK (embedding_status IN ('pending','ready','failed'))"
    )
