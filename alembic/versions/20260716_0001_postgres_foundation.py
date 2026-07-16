"""Create PostgreSQL extensions required by ScholarAgent.

Revision ID: 20260716_0001
Revises:
"""
from alembic import op


RUNTIME_TABLES = (
    """CREATE TABLE scholar_tenants (
        tenant_id TEXT PRIMARY KEY, name TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
        metadata_json TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
    """CREATE TABLE scholar_users (
        user_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL REFERENCES scholar_tenants(tenant_id),
        username TEXT NOT NULL, password_hash TEXT NOT NULL, display_name TEXT NOT NULL,
        roles_json TEXT, api_key TEXT NOT NULL UNIQUE, status TEXT NOT NULL DEFAULT 'active',
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (tenant_id, username))""",
    """CREATE TABLE scholar_tasks (
        task_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, user_id TEXT NOT NULL,
        status TEXT NOT NULL, phase TEXT NOT NULL, percent INTEGER NOT NULL DEFAULT 0,
        trace_id TEXT, request_json TEXT NOT NULL, result_json TEXT, error TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
    """CREATE TABLE scholar_conversations (
        conversation_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, user_id TEXT NOT NULL,
        title TEXT NOT NULL, skill_id TEXT NOT NULL DEFAULT 'general_assistant',
        status TEXT NOT NULL DEFAULT 'active', metadata_json TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
    """CREATE TABLE scholar_conversation_messages (
        message_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES scholar_conversations(conversation_id) ON DELETE CASCADE,
        tenant_id TEXT NOT NULL, user_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL,
        skill_id TEXT, metadata_json TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
    """CREATE TABLE scholar_conversation_tool_calls (
        call_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES scholar_conversations(conversation_id) ON DELETE CASCADE,
        tenant_id TEXT NOT NULL, user_id TEXT NOT NULL, tool_name TEXT NOT NULL,
        arguments_json TEXT NOT NULL, status TEXT NOT NULL, result_json TEXT, error TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
    """CREATE TABLE scholar_conversation_context (
        conversation_id TEXT NOT NULL REFERENCES scholar_conversations(conversation_id) ON DELETE CASCADE,
        tenant_id TEXT NOT NULL, user_id TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '',
        state_json TEXT, token_estimate INTEGER NOT NULL DEFAULT 0, compression_count INTEGER NOT NULL DEFAULT 0,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (tenant_id, user_id, conversation_id))""",
    """CREATE TABLE scholar_conversation_working_state (
        conversation_id TEXT NOT NULL, tenant_id TEXT NOT NULL, user_id TEXT NOT NULL,
        state_version INTEGER NOT NULL DEFAULT 1, state_json TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (tenant_id, user_id, conversation_id))""",
    """CREATE TABLE scholar_conversation_events (
        event_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES scholar_conversations(conversation_id) ON DELETE CASCADE,
        tenant_id TEXT NOT NULL, user_id TEXT NOT NULL, event_type TEXT NOT NULL,
        status TEXT NOT NULL, summary TEXT NOT NULL, payload_json TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
    """CREATE TABLE scholar_agent_runs (
        run_id TEXT PRIMARY KEY, parent_run_id TEXT, conversation_id TEXT, task_id TEXT,
        tenant_id TEXT NOT NULL, user_id TEXT NOT NULL, agent_name TEXT NOT NULL,
        agent_role TEXT NOT NULL, execution_mode TEXT NOT NULL, goal TEXT NOT NULL,
        status TEXT NOT NULL, depth INTEGER NOT NULL DEFAULT 0, input_json TEXT,
        result_json TEXT, error TEXT, started_at TIMESTAMPTZ NOT NULL DEFAULT now(), completed_at TIMESTAMPTZ)""",
    """CREATE TABLE scholar_task_events (
        event_id BIGSERIAL PRIMARY KEY, task_id TEXT NOT NULL, tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL, event TEXT NOT NULL, phase TEXT NOT NULL, message TEXT,
        percent INTEGER NOT NULL DEFAULT 0, payload_json TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
    """CREATE TABLE scholar_citation_audits (
        audit_id BIGSERIAL PRIMARY KEY, task_id TEXT NOT NULL, tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL, is_valid INTEGER NOT NULL DEFAULT 0, found_ids_json TEXT,
        hallucinated_ids_json TEXT, missing_ids_json TEXT, coverage DOUBLE PRECISION NOT NULL DEFAULT 0,
        payload_json TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
    """CREATE TABLE scholar_reflection_logs (
        reflection_id BIGSERIAL PRIMARY KEY, task_id TEXT NOT NULL, tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL, phase TEXT NOT NULL, section_id TEXT, review_json TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
    """CREATE TABLE scholar_user_preferences (
        tenant_id TEXT NOT NULL, user_id TEXT NOT NULL, preference_key TEXT NOT NULL,
        preference_json TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY (tenant_id, user_id, preference_key))""",
    """CREATE TABLE scholar_memories (
        memory_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, user_id TEXT NOT NULL,
        conversation_id TEXT, memory_type TEXT NOT NULL, content TEXT NOT NULL,
        normalized_content TEXT NOT NULL, importance DOUBLE PRECISION NOT NULL DEFAULT 0.5,
        confidence DOUBLE PRECISION NOT NULL DEFAULT 1.0, source_message_id TEXT,
        metadata_json TEXT, access_count INTEGER NOT NULL DEFAULT 0, last_accessed_at TIMESTAMPTZ,
        status TEXT NOT NULL DEFAULT 'active', created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (tenant_id, user_id, memory_type, normalized_content))""",
    """CREATE TABLE scholar_trace_events (
        trace_event_id BIGSERIAL PRIMARY KEY, trace_id TEXT NOT NULL, task_id TEXT,
        tenant_id TEXT, user_id TEXT, span_name TEXT NOT NULL, event_type TEXT NOT NULL,
        provider TEXT, model TEXT, latency_ms INTEGER, metadata_json TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
    """CREATE TABLE scholar_operation_patterns (
        pattern_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, user_id TEXT NOT NULL,
        operation_name TEXT NOT NULL, signature TEXT NOT NULL, recipe_json TEXT NOT NULL,
        success_count INTEGER NOT NULL DEFAULT 0, failure_count INTEGER NOT NULL DEFAULT 0,
        first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(), last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (tenant_id, user_id, signature))""",
    """CREATE TABLE scholar_skill_candidates (
        candidate_id TEXT PRIMARY KEY, pattern_id TEXT NOT NULL, tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL, name TEXT NOT NULL, description TEXT NOT NULL,
        manifest_json TEXT NOT NULL, evidence_count INTEGER NOT NULL DEFAULT 0,
        success_rate DOUBLE PRECISION NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'draft',
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (tenant_id, user_id, pattern_id))""",
    """CREATE TABLE scholar_settings (
        key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
    """CREATE TABLE scholar_institution_profiles (
        profile_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, user_id TEXT NOT NULL,
        institution_name TEXT NOT NULL, access_type TEXT NOT NULL, login_url TEXT,
        proxy_prefix TEXT, enabled INTEGER NOT NULL DEFAULT 1,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
    """CREATE TABLE scholar_institution_sessions (
        session_id TEXT PRIMARY KEY, profile_id TEXT NOT NULL, tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL, status TEXT NOT NULL, authenticated_domains_json TEXT,
        verified_at TIMESTAMPTZ, expires_at TIMESTAMPTZ, revoked_at TIMESTAMPTZ, last_error TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
    """CREATE TABLE scholar_institution_downloads (
        download_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL, conversation_id TEXT, source TEXT NOT NULL, source_url TEXT NOT NULL,
        title TEXT, doi TEXT, status TEXT NOT NULL, file_type TEXT, file_path TEXT, file_sha256 TEXT,
        file_size BIGINT, paper_id TEXT, failure_code TEXT, failure_message TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(), completed_at TIMESTAMPTZ)""",
)

RUNTIME_INDEXES = (
    "CREATE INDEX idx_scholar_tasks_user ON scholar_tasks(tenant_id, user_id, updated_at)",
    "CREATE INDEX idx_scholar_conversations_user ON scholar_conversations(tenant_id, user_id, updated_at)",
    "CREATE INDEX idx_scholar_messages_conversation ON scholar_conversation_messages(tenant_id, conversation_id, created_at)",
    "CREATE INDEX idx_scholar_tool_calls_status ON scholar_conversation_tool_calls(tenant_id, user_id, conversation_id, status, created_at)",
    "CREATE INDEX idx_scholar_task_events_task ON scholar_task_events(tenant_id, task_id, event_id)",
    "CREATE INDEX idx_scholar_memories_recall ON scholar_memories(tenant_id, user_id, status, memory_type, updated_at)",
    "CREATE INDEX idx_scholar_trace_events_trace ON scholar_trace_events(trace_id, trace_event_id)",
)


revision = "20260716_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    for statement in RUNTIME_TABLES:
        op.execute(statement)
    for statement in RUNTIME_INDEXES:
        op.execute(statement)


def downgrade() -> None:
    for table in reversed(
        (
            "scholar_institution_downloads", "scholar_institution_sessions",
            "scholar_institution_profiles", "scholar_settings", "scholar_skill_candidates",
            "scholar_operation_patterns", "scholar_trace_events", "scholar_memories",
            "scholar_user_preferences", "scholar_reflection_logs", "scholar_citation_audits",
            "scholar_task_events", "scholar_agent_runs", "scholar_conversation_events",
            "scholar_conversation_working_state", "scholar_conversation_context",
            "scholar_conversation_tool_calls", "scholar_conversation_messages",
            "scholar_conversations", "scholar_tasks", "scholar_users", "scholar_tenants",
        )
    ):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.execute("DROP EXTENSION IF EXISTS vector")
