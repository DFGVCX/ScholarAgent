"""Add hierarchy-aware chunk provenance and embedding context.

Revision ID: 20260828_0005
Revises: 20260717_0004
"""
from alembic import op


revision = "20260828_0005"
down_revision = "20260717_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE paper_chunks ADD COLUMN chunk_type TEXT NOT NULL DEFAULT 'prose'")
    op.execute("ALTER TABLE paper_chunks ADD COLUMN parent_section_id TEXT")
    op.execute("ALTER TABLE paper_chunks ADD COLUMN source_block_ids JSONB NOT NULL DEFAULT '[]'::jsonb")
    op.execute("ALTER TABLE paper_chunks ADD COLUMN context_before TEXT NOT NULL DEFAULT ''")
    op.execute("ALTER TABLE paper_chunks ADD COLUMN context_after TEXT NOT NULL DEFAULT ''")
    op.execute("ALTER TABLE paper_chunks ADD COLUMN embedding_content TEXT NOT NULL DEFAULT ''")
    op.execute("ALTER TABLE paper_chunks ADD COLUMN chunk_metadata JSONB NOT NULL DEFAULT '{}'::jsonb")
    op.execute(
        "ALTER TABLE paper_chunks ADD CONSTRAINT ck_paper_chunks_type "
        "CHECK (chunk_type IN ('prose','equation','table','figure','algorithm'))"
    )
    op.execute(
        "CREATE INDEX idx_paper_chunks_type ON paper_chunks"
        "(tenant_id, user_id, paper_uuid, content_version, chunk_type)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_paper_chunks_type")
    op.execute("ALTER TABLE paper_chunks DROP CONSTRAINT IF EXISTS ck_paper_chunks_type")
    op.execute("ALTER TABLE paper_chunks DROP COLUMN IF EXISTS chunk_metadata")
    op.execute("ALTER TABLE paper_chunks DROP COLUMN IF EXISTS embedding_content")
    op.execute("ALTER TABLE paper_chunks DROP COLUMN IF EXISTS context_after")
    op.execute("ALTER TABLE paper_chunks DROP COLUMN IF EXISTS context_before")
    op.execute("ALTER TABLE paper_chunks DROP COLUMN IF EXISTS source_block_ids")
    op.execute("ALTER TABLE paper_chunks DROP COLUMN IF EXISTS parent_section_id")
    op.execute("ALTER TABLE paper_chunks DROP COLUMN IF EXISTS chunk_type")
