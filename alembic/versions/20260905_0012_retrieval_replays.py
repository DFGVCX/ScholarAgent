"""Persist tenant-scoped retrieval replays and Agent evidence adoption.

Revision ID: 20260905_0012
Revises: 20260905_0011
"""

from alembic import op


revision = "20260905_0012"
down_revision = "20260905_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE IF NOT EXISTS rag_retrieval_replays (
            replay_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            conversation_id TEXT,
            task_id TEXT,
            consumer TEXT NOT NULL,
            query TEXT NOT NULL,
            requested_mode TEXT NOT NULL,
            effective_mode TEXT NOT NULL,
            candidate_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
            context_snapshot JSONB NOT NULL DEFAULT '[]'::jsonb,
            adopted_chunk_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
            attribution TEXT NOT NULL,
            warnings JSONB NOT NULL DEFAULT '[]'::jsonb,
            timings JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )"""
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_rag_replays_scope_created "
        "ON rag_retrieval_replays(tenant_id, user_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_rag_replays_conversation "
        "ON rag_retrieval_replays(tenant_id, user_id, conversation_id, created_at DESC)"
    )
    op.execute("ALTER TABLE rag_retrieval_replays ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE rag_retrieval_replays FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY rag_retrieval_replays_tenant_user_policy ON rag_retrieval_replays "
        "USING (tenant_id = current_setting('app.tenant_id', true) "
        "AND user_id = current_setting('app.user_id', true)) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true) "
        "AND user_id = current_setting('app.user_id', true))"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS rag_retrieval_replays")
