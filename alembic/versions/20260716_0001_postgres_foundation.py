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

PAPER_TABLES = (
    """CREATE TABLE papers (
        paper_uuid UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL, paper_id TEXT NOT NULL, source TEXT NOT NULL,
        source_identifier TEXT, normalized_doi TEXT, normalized_arxiv_id TEXT,
        title TEXT NOT NULL, authors JSONB NOT NULL DEFAULT '[]'::jsonb,
        abstract TEXT NOT NULL DEFAULT '', published_at TIMESTAMPTZ, canonical_url TEXT,
        in_knowledge_base BOOLEAN NOT NULL DEFAULT true,
        ingestion_status TEXT NOT NULL DEFAULT 'metadata_only'
            CHECK (ingestion_status IN ('metadata_only','acquiring','parsing','embedding','ready','failed')),
        current_content_version INTEGER NOT NULL DEFAULT 0, last_error TEXT,
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        deleted_at TIMESTAMPTZ, UNIQUE (tenant_id, user_id, paper_id),
        UNIQUE (tenant_id, user_id, paper_uuid))""",
    """CREATE TABLE paper_assets (
        asset_uuid UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL, paper_uuid UUID NOT NULL, asset_kind TEXT NOT NULL DEFAULT 'source',
        file_uri TEXT NOT NULL, file_name TEXT NOT NULL, mime_type TEXT NOT NULL,
        sha256 TEXT NOT NULL, file_size BIGINT NOT NULL CHECK (file_size >= 0), page_count INTEGER,
        validation_status TEXT NOT NULL DEFAULT 'pending', parser_name TEXT, parser_version TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        FOREIGN KEY (tenant_id, user_id, paper_uuid)
            REFERENCES papers(tenant_id, user_id, paper_uuid) ON DELETE CASCADE,
        UNIQUE (tenant_id, user_id, sha256))""",
    """CREATE TABLE paper_contents (
        content_uuid UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL, paper_uuid UUID NOT NULL, content_version INTEGER NOT NULL,
        full_text TEXT NOT NULL, content_hash TEXT NOT NULL, language TEXT,
        extraction_method TEXT NOT NULL, extraction_quality DOUBLE PRECISION,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        FOREIGN KEY (tenant_id, user_id, paper_uuid)
            REFERENCES papers(tenant_id, user_id, paper_uuid) ON DELETE CASCADE,
        UNIQUE (paper_uuid, content_version), UNIQUE (tenant_id, user_id, content_uuid))""",
    """CREATE TABLE paper_chunks (
        chunk_uuid UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL, paper_uuid UUID NOT NULL, content_uuid UUID NOT NULL,
        content_version INTEGER NOT NULL, chunk_index INTEGER NOT NULL, section_path TEXT,
        page_start INTEGER, page_end INTEGER, content TEXT NOT NULL, content_hash TEXT NOT NULL,
        token_count INTEGER NOT NULL, embedding vector(1024), embedding_model TEXT,
        embedding_status TEXT NOT NULL DEFAULT 'pending'
            CHECK (embedding_status IN ('pending','ready','failed')),
        embedding_error TEXT,
        search_vector tsvector GENERATED ALWAYS AS (to_tsvector('simple', coalesce(content, ''))) STORED,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        FOREIGN KEY (tenant_id, user_id, paper_uuid)
            REFERENCES papers(tenant_id, user_id, paper_uuid) ON DELETE CASCADE,
        FOREIGN KEY (tenant_id, user_id, content_uuid)
            REFERENCES paper_contents(tenant_id, user_id, content_uuid) ON DELETE CASCADE,
        UNIQUE (content_uuid, chunk_index))""",
    """CREATE TABLE paper_ingestion_jobs (
        job_uuid UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL, paper_uuid UUID NOT NULL, job_type TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending'
            CHECK (status IN ('pending','running','retry','completed','failed')),
        attempt_count INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL DEFAULT 3,
        available_at TIMESTAMPTZ NOT NULL DEFAULT now(), locked_at TIMESTAMPTZ,
        locked_by TEXT, last_error TEXT, payload JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        completed_at TIMESTAMPTZ,
        FOREIGN KEY (tenant_id, user_id, paper_uuid)
            REFERENCES papers(tenant_id, user_id, paper_uuid) ON DELETE CASCADE)""",
    """CREATE TABLE paper_annotations (
        annotation_uuid UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL, paper_uuid UUID NOT NULL, page INTEGER NOT NULL DEFAULT 0,
        annotation_type TEXT NOT NULL DEFAULT 'highlight', color TEXT,
        points JSONB NOT NULL DEFAULT '[]'::jsonb, content TEXT NOT NULL DEFAULT '',
        anchor JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        FOREIGN KEY (tenant_id, user_id, paper_uuid)
            REFERENCES papers(tenant_id, user_id, paper_uuid) ON DELETE CASCADE)""",
    """CREATE TABLE paper_translations (
        translation_uuid UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL, paper_uuid UUID NOT NULL, source_hash TEXT NOT NULL,
        source_text TEXT NOT NULL, source_language TEXT NOT NULL, target_language TEXT NOT NULL,
        translated_text TEXT NOT NULL, provider TEXT, model TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        FOREIGN KEY (tenant_id, user_id, paper_uuid)
            REFERENCES papers(tenant_id, user_id, paper_uuid) ON DELETE CASCADE,
        UNIQUE (tenant_id, user_id, paper_uuid, source_hash, target_language))""",
)

PAPER_INDEXES = (
    "CREATE UNIQUE INDEX uq_papers_doi ON papers(tenant_id, user_id, normalized_doi) WHERE normalized_doi IS NOT NULL AND deleted_at IS NULL",
    "CREATE UNIQUE INDEX uq_papers_arxiv ON papers(tenant_id, user_id, normalized_arxiv_id) WHERE normalized_arxiv_id IS NOT NULL AND deleted_at IS NULL",
    "CREATE INDEX idx_papers_tenant_user ON papers(tenant_id, user_id, in_knowledge_base, updated_at DESC) WHERE deleted_at IS NULL",
    "CREATE INDEX idx_paper_chunks_tenant_user ON paper_chunks(tenant_id, user_id, paper_uuid, content_version)",
    "CREATE INDEX idx_paper_chunks_search ON paper_chunks USING gin(search_vector)",
    "CREATE INDEX idx_paper_chunks_embedding ON paper_chunks USING hnsw (embedding vector_cosine_ops) WHERE embedding_status = 'ready'",
    "CREATE INDEX idx_ingestion_jobs_claim ON paper_ingestion_jobs(status, available_at, created_at) WHERE status IN ('pending','retry')",
)

PAPER_RLS_TABLES = (
    "papers", "paper_assets", "paper_contents", "paper_chunks",
    "paper_ingestion_jobs", "paper_annotations", "paper_translations",
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
    for statement in PAPER_TABLES:
        op.execute(statement)
    for statement in PAPER_INDEXES:
        op.execute(statement)
    for table in PAPER_RLS_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_tenant_user_policy ON {table} "
            "USING (tenant_id = current_setting('app.tenant_id', true) "
            "AND user_id = current_setting('app.user_id', true)) "
            "WITH CHECK (tenant_id = current_setting('app.tenant_id', true) "
            "AND user_id = current_setting('app.user_id', true))"
        )


def downgrade() -> None:
    for table in reversed(PAPER_RLS_TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
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
