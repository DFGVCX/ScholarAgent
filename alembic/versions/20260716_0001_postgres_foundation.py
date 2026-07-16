"""Create PostgreSQL extensions required by ScholarAgent.

Revision ID: 20260716_0001
Revises:
"""
from alembic import op


revision = "20260716_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")


def downgrade() -> None:
    op.execute("DROP EXTENSION IF EXISTS vector")
