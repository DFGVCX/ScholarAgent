"""Track tenant-scoped embedding calls and provider-reported token usage.

Revision ID: 20260901_0007
Revises: 20260828_0006
"""
from alembic import op


revision = "20260901_0007"
down_revision = "20260828_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE embedding_usage_events (
            event_id BIGSERIAL PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            operation TEXT NOT NULL
                CHECK (operation IN ('probe','ingestion','reindex','retrieval','evaluation')),
            provider TEXT NOT NULL DEFAULT 'qwen',
            model TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('succeeded','failed','cancelled')),
            input_count INTEGER NOT NULL DEFAULT 0 CHECK (input_count >= 0),
            request_count INTEGER NOT NULL DEFAULT 0 CHECK (request_count >= 0),
            successful_request_count INTEGER NOT NULL DEFAULT 0
                CHECK (successful_request_count >= 0),
            failed_request_count INTEGER NOT NULL DEFAULT 0 CHECK (failed_request_count >= 0),
            cancelled_request_count INTEGER NOT NULL DEFAULT 0
                CHECK (cancelled_request_count >= 0),
            reported_tokens BIGINT NOT NULL DEFAULT 0 CHECK (reported_tokens >= 0),
            usage_reported_requests INTEGER NOT NULL DEFAULT 0
                CHECK (usage_reported_requests >= 0),
            successful_usage_reported_requests INTEGER NOT NULL DEFAULT 0
                CHECK (successful_usage_reported_requests >= 0),
            duration_ms INTEGER NOT NULL DEFAULT 0 CHECK (duration_ms >= 0),
            error_type TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CHECK (successful_request_count + failed_request_count = request_count),
            CHECK (cancelled_request_count <= failed_request_count),
            CHECK (usage_reported_requests <= request_count),
            CHECK (successful_usage_reported_requests <= successful_request_count),
            CHECK (successful_usage_reported_requests <= usage_reported_requests)
        )"""
    )
    op.execute(
        "CREATE INDEX idx_embedding_usage_scope_created "
        "ON embedding_usage_events(tenant_id, user_id, created_at DESC)"
    )
    op.execute("ALTER TABLE embedding_usage_events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE embedding_usage_events FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY embedding_usage_events_tenant_user_policy "
        "ON embedding_usage_events "
        "USING (tenant_id = current_setting('app.tenant_id', true) "
        "AND user_id = current_setting('app.user_id', true)) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true) "
        "AND user_id = current_setting('app.user_id', true))"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS embedding_usage_events")
