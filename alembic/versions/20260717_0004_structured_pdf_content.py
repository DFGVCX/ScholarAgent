"""Add page- and section-aware PDF content persistence.

Revision ID: 20260717_0004
Revises: 20260717_0003
"""
from alembic import op


revision = "20260717_0004"
down_revision = "20260717_0003"
branch_labels = None
depends_on = None


def _tenant_policy(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table}_tenant_user_policy ON {table} "
        "USING (tenant_id = current_setting('app.tenant_id', true) "
        "AND user_id = current_setting('app.user_id', true)) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true) "
        "AND user_id = current_setting('app.user_id', true))"
    )


def upgrade() -> None:
    op.execute("ALTER TABLE paper_contents ADD COLUMN parser_name TEXT NOT NULL DEFAULT 'legacy_fixed'")
    op.execute("ALTER TABLE paper_contents ADD COLUMN parser_version TEXT NOT NULL DEFAULT '1'")
    op.execute("ALTER TABLE paper_contents ADD COLUMN chunk_strategy TEXT NOT NULL DEFAULT 'legacy_fixed'")
    op.execute("ALTER TABLE paper_contents ADD COLUMN chunker_version TEXT NOT NULL DEFAULT '1'")
    op.execute(
        "ALTER TABLE paper_contents ADD COLUMN parse_status TEXT NOT NULL DEFAULT 'ready' "
        "CHECK (parse_status IN ('ready','needs_ocr','failed','manual'))"
    )
    op.execute("ALTER TABLE paper_contents ADD COLUMN parse_manifest JSONB NOT NULL DEFAULT '{}'::jsonb")
    op.execute("ALTER TABLE paper_chunks ADD COLUMN section_id TEXT")
    op.execute("ALTER TABLE paper_chunks ADD COLUMN char_start INTEGER")
    op.execute("ALTER TABLE paper_chunks ADD COLUMN char_end INTEGER")

    op.execute(
        """CREATE TABLE paper_pages (
            page_uuid UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id TEXT NOT NULL, user_id TEXT NOT NULL,
            paper_uuid UUID NOT NULL, content_uuid UUID NOT NULL,
            content_version INTEGER NOT NULL, page_number INTEGER NOT NULL CHECK (page_number >= 1),
            text TEXT NOT NULL DEFAULT '', text_hash TEXT NOT NULL,
            extraction_method TEXT NOT NULL, quality_status TEXT NOT NULL,
            searchable_chars INTEGER NOT NULL DEFAULT 0 CHECK (searchable_chars >= 0),
            blocks JSONB NOT NULL DEFAULT '[]'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            FOREIGN KEY (tenant_id, user_id, paper_uuid)
                REFERENCES papers(tenant_id, user_id, paper_uuid) ON DELETE CASCADE,
            FOREIGN KEY (tenant_id, user_id, content_uuid)
                REFERENCES paper_contents(tenant_id, user_id, content_uuid) ON DELETE CASCADE,
            UNIQUE (content_uuid, page_number))"""
    )
    op.execute(
        """CREATE TABLE paper_sections (
            section_uuid UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id TEXT NOT NULL, user_id TEXT NOT NULL,
            paper_uuid UUID NOT NULL, content_uuid UUID NOT NULL,
            content_version INTEGER NOT NULL, section_id TEXT NOT NULL,
            section_index INTEGER NOT NULL CHECK (section_index >= 0),
            kind TEXT NOT NULL, title TEXT NOT NULL,
            page_start INTEGER NOT NULL, page_end INTEGER NOT NULL,
            content TEXT NOT NULL, char_count INTEGER NOT NULL CHECK (char_count >= 0),
            char_start INTEGER NOT NULL, char_end INTEGER NOT NULL, text_hash TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            FOREIGN KEY (tenant_id, user_id, paper_uuid)
                REFERENCES papers(tenant_id, user_id, paper_uuid) ON DELETE CASCADE,
            FOREIGN KEY (tenant_id, user_id, content_uuid)
                REFERENCES paper_contents(tenant_id, user_id, content_uuid) ON DELETE CASCADE,
            UNIQUE (content_uuid, section_id), UNIQUE (content_uuid, section_index))"""
    )
    op.execute("CREATE INDEX idx_paper_pages_content ON paper_pages(tenant_id, user_id, paper_uuid, content_version, page_number)")
    op.execute("CREATE INDEX idx_paper_sections_content ON paper_sections(tenant_id, user_id, paper_uuid, content_version, section_index)")
    op.execute("CREATE INDEX idx_paper_chunks_section ON paper_chunks(tenant_id, user_id, paper_uuid, content_version, section_id)")
    _tenant_policy("paper_pages")
    _tenant_policy("paper_sections")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS paper_sections CASCADE")
    op.execute("DROP TABLE IF EXISTS paper_pages CASCADE")
    op.execute("DROP INDEX IF EXISTS idx_paper_chunks_section")
    op.execute("ALTER TABLE paper_chunks DROP COLUMN IF EXISTS char_end")
    op.execute("ALTER TABLE paper_chunks DROP COLUMN IF EXISTS char_start")
    op.execute("ALTER TABLE paper_chunks DROP COLUMN IF EXISTS section_id")
    op.execute("ALTER TABLE paper_contents DROP COLUMN IF EXISTS parse_manifest")
    op.execute("ALTER TABLE paper_contents DROP COLUMN IF EXISTS parse_status")
    op.execute("ALTER TABLE paper_contents DROP COLUMN IF EXISTS chunker_version")
    op.execute("ALTER TABLE paper_contents DROP COLUMN IF EXISTS chunk_strategy")
    op.execute("ALTER TABLE paper_contents DROP COLUMN IF EXISTS parser_version")
    op.execute("ALTER TABLE paper_contents DROP COLUMN IF EXISTS parser_name")
