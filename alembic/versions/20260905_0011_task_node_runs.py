"""Persist independently retryable LangGraph node runs.

Revision ID: 20260905_0011
Revises: 20260901_0010
"""

from alembic import op


revision = "20260905_0011"
down_revision = "20260901_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE IF NOT EXISTS scholar_task_node_runs (
            run_id VARCHAR(96) PRIMARY KEY,
            task_id VARCHAR(96) NOT NULL,
            tenant_id VARCHAR(96) NOT NULL,
            user_id VARCHAR(96) NOT NULL,
            node_id VARCHAR(128) NOT NULL,
            capability VARCHAR(96) NOT NULL,
            node_version VARCHAR(32) NOT NULL,
            attempt INTEGER NOT NULL,
            status VARCHAR(32) NOT NULL,
            input_fingerprint VARCHAR(128) NOT NULL,
            input_json TEXT NOT NULL,
            output_json TEXT NOT NULL,
            dependency_snapshot_json TEXT NOT NULL,
            quality_json TEXT NOT NULL,
            invalidated_by VARCHAR(128),
            reused_from_run_id VARCHAR(96),
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMPTZ
        )"""
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_task_node_runs_lookup "
        "ON scholar_task_node_runs(tenant_id, user_id, task_id, node_id, status, attempt DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_task_node_runs_fingerprint "
        "ON scholar_task_node_runs(tenant_id, user_id, task_id, node_id, input_fingerprint)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS scholar_task_node_runs")
