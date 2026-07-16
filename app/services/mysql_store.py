from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from contextlib import contextmanager
from typing import Any, Iterator
from urllib.parse import urlparse

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

class PostgreSQLUnavailable(RuntimeError):
    """Raised when the configured PostgreSQL database is not reachable or ready."""


# Kept as an import-compatible alias while callers are renamed incrementally.
MySQLUnavailable = PostgreSQLUnavailable


# ---------------------------------------------------------------------------
# PostgreSQL connection pool
# ---------------------------------------------------------------------------

_pool: ConnectionPool | None = None
_pool_lock = threading.Lock()
_availability: bool | None = None


def _sync_database_url() -> str:
    url = os.getenv(
        "SCHOLAR_DATABASE_URL",
        "postgresql+psycopg://scholar:scholar@localhost:5432/scholar_agent",
    )
    if not url.startswith(("postgresql://", "postgresql+psycopg://")):
        raise ValueError("SCHOLAR_DATABASE_URL must use PostgreSQL")
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def _get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = ConnectionPool(
                    conninfo=_sync_database_url(),
                    min_size=1,
                    max_size=10,
                    timeout=3,
                    kwargs={"row_factory": dict_row},
                    open=False,
                )
                _pool.open(wait=False)
    return _pool


# ---------------------------------------------------------------------------
# Temporary SQL compatibility: legacy call sites -> PostgreSQL
# ---------------------------------------------------------------------------

def _translate_sql(sql: str) -> str:
    translated = re.sub(r"datetime\(\s*'now'\s*\)", "CURRENT_TIMESTAMP", sql, flags=re.I)
    translated = translated.replace("`", '"')
    translated = re.sub(r"\bINSERT\s+OR\s+IGNORE\b", "INSERT", translated, flags=re.I)
    translated = re.sub(r"\bVALUES\((\w+)\)", r"EXCLUDED.\1", translated, flags=re.I)

    result: list[str] = []
    single_quoted = False
    double_quoted = False
    index = 0
    while index < len(translated):
        char = translated[index]
        next_char = translated[index + 1] if index + 1 < len(translated) else ""
        if char == "'" and not double_quoted:
            result.append(char)
            if single_quoted and next_char == "'":
                result.append(next_char)
                index += 2
                continue
            single_quoted = not single_quoted
        elif char == '"' and not single_quoted:
            result.append(char)
            if double_quoted and next_char == '"':
                result.append(next_char)
                index += 2
                continue
            double_quoted = not double_quoted
        elif char == "?" and not single_quoted and not double_quoted:
            result.append("%s")
        else:
            result.append(char)
        index += 1
    return "".join(result)


# ---------------------------------------------------------------------------
# Parameter adaptation
# ---------------------------------------------------------------------------

def _adapt_params(params: Any) -> Any:
    if params is None:
        return ()
    if isinstance(params, dict):
        return {k: _adapt_single(v) for k, v in params.items()}
    return tuple(_adapt_single(v) for v in params)


def _adapt_single(v: Any) -> Any:
    if isinstance(v, (list, dict)):
        return json.dumps(v, ensure_ascii=False)
    return v


# ---------------------------------------------------------------------------
# Hash & JSON helpers (same as before, internal)
# ---------------------------------------------------------------------------

def password_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _json_dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False)


def _json_loads(value: Any, fallback: Any) -> Any:
    if value in (None, ""):
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


# ---------------------------------------------------------------------------
# Public API (identical signatures)
# ---------------------------------------------------------------------------

def configured_database_name() -> str:
    return urlparse(_sync_database_url()).path.lstrip("/") or "scholar_agent"


def should_use_mysql() -> bool:
    return False


def reset_availability_cache() -> None:
    global _availability
    _availability = None


@contextmanager
def connection(with_database: bool = True) -> Iterator[Any]:
    """Yield a pooled PostgreSQL connection (legacy signature retained)."""
    del with_database
    with _get_pool().connection() as conn:
        yield conn


def is_available() -> bool:
    global _availability
    if _availability is not None:
        return _availability
    try:
        with connection() as conn, conn.cursor() as cursor:
            cursor.execute("SELECT 1")
            _availability = cursor.fetchone() is not None
    except Exception:
        _availability = False
    return _availability


def execute(
    sql: str,
    params: tuple[Any, ...] | dict[str, Any] | None = None,
) -> int:
    with connection() as conn, conn.cursor() as cursor:
        cursor.execute(_translate_sql(sql), _adapt_params(params))
        affected = cursor.rowcount
        conn.commit()
        return affected


def fetch_one(
    sql: str,
    params: tuple[Any, ...] | dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    with connection() as conn, conn.cursor() as cursor:
        cursor.execute(_translate_sql(sql), _adapt_params(params))
        row = cursor.fetchone()
        return dict(row) if row else None


def fetch_all(
    sql: str,
    params: tuple[Any, ...] | dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    with connection() as conn, conn.cursor() as cursor:
        cursor.execute(_translate_sql(sql), _adapt_params(params))
        return [dict(row) for row in cursor.fetchall()]


# ---------------------------------------------------------------------------
# Schema (SQLite syntax)
# ---------------------------------------------------------------------------

SCHEMA_SQL: tuple[str, ...] = (
    """CREATE TABLE IF NOT EXISTS scholar_tenants (
        tenant_id TEXT PRIMARY KEY, name TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'active', metadata_json TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now')))""",

    """CREATE TABLE IF NOT EXISTS scholar_users (
        user_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
        username TEXT NOT NULL, password_hash TEXT NOT NULL,
        display_name TEXT NOT NULL, roles_json TEXT,
        api_key TEXT NOT NULL UNIQUE, status TEXT NOT NULL DEFAULT 'active',
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE (tenant_id, username))""",

    """CREATE TABLE IF NOT EXISTS scholar_tasks (
        task_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL, status TEXT NOT NULL, phase TEXT NOT NULL,
        percent INTEGER NOT NULL DEFAULT 0, trace_id TEXT,
        request_json TEXT NOT NULL, result_json TEXT, error TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now')))""",

    """CREATE TABLE IF NOT EXISTS scholar_conversations (
        conversation_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL, title TEXT NOT NULL,
        skill_id TEXT NOT NULL DEFAULT 'general_assistant',
        status TEXT NOT NULL DEFAULT 'active', metadata_json TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now')))""",

    """CREATE TABLE IF NOT EXISTS scholar_conversation_messages (
        message_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL,
        tenant_id TEXT NOT NULL, user_id TEXT NOT NULL,
        role TEXT NOT NULL, content TEXT NOT NULL, skill_id TEXT,
        metadata_json TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')))""",

    """CREATE TABLE IF NOT EXISTS scholar_conversation_tool_calls (
        call_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL,
        tenant_id TEXT NOT NULL, user_id TEXT NOT NULL,
        tool_name TEXT NOT NULL, arguments_json TEXT NOT NULL,
        status TEXT NOT NULL, result_json TEXT, error TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now')),
        FOREIGN KEY (conversation_id) REFERENCES scholar_conversations(conversation_id)
            ON DELETE CASCADE)""",

    """CREATE TABLE IF NOT EXISTS scholar_conversation_context (
        conversation_id TEXT NOT NULL, tenant_id TEXT NOT NULL, user_id TEXT NOT NULL,
        summary TEXT NOT NULL DEFAULT '', state_json TEXT, token_estimate INTEGER NOT NULL DEFAULT 0,
        compression_count INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now')),
        PRIMARY KEY (tenant_id, user_id, conversation_id),
        FOREIGN KEY (conversation_id) REFERENCES scholar_conversations(conversation_id)
            ON DELETE CASCADE)""",

    """CREATE TABLE IF NOT EXISTS scholar_conversation_working_state (
        conversation_id TEXT NOT NULL, tenant_id TEXT NOT NULL, user_id TEXT NOT NULL,
        state_version INTEGER NOT NULL DEFAULT 1, state_json TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now')),
        PRIMARY KEY (tenant_id, user_id, conversation_id))""",

    """CREATE TABLE IF NOT EXISTS scholar_conversation_events (
        event_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL,
        tenant_id TEXT NOT NULL, user_id TEXT NOT NULL,
        event_type TEXT NOT NULL, status TEXT NOT NULL, summary TEXT NOT NULL,
        payload_json TEXT, created_at TEXT NOT NULL DEFAULT (datetime('now')),
        FOREIGN KEY (conversation_id) REFERENCES scholar_conversations(conversation_id)
            ON DELETE CASCADE)""",

    """CREATE TABLE IF NOT EXISTS scholar_agent_runs (
        run_id TEXT PRIMARY KEY, parent_run_id TEXT, conversation_id TEXT,
        task_id TEXT, tenant_id TEXT NOT NULL, user_id TEXT NOT NULL,
        agent_name TEXT NOT NULL, agent_role TEXT NOT NULL, execution_mode TEXT NOT NULL,
        goal TEXT NOT NULL, status TEXT NOT NULL, depth INTEGER NOT NULL DEFAULT 0,
        input_json TEXT, result_json TEXT, error TEXT,
        started_at TEXT NOT NULL DEFAULT (datetime('now')), completed_at TEXT)""",

    """CREATE TABLE IF NOT EXISTS scholar_knowledge_papers (
        paper_id TEXT NOT NULL, tenant_id TEXT NOT NULL, user_id TEXT NOT NULL,
        source TEXT NOT NULL, title TEXT NOT NULL, authors_json TEXT,
        abstract TEXT, full_text TEXT, published_at TEXT, doi TEXT,
        arxiv_id TEXT, url TEXT, file_path TEXT,
        in_knowledge_base INTEGER NOT NULL DEFAULT 1, metadata_json TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now')),
        PRIMARY KEY (tenant_id, user_id, paper_id))""",

    """CREATE TABLE IF NOT EXISTS scholar_task_events (
        event_id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
        tenant_id TEXT NOT NULL, user_id TEXT NOT NULL, event TEXT NOT NULL,
        phase TEXT NOT NULL, message TEXT, percent INTEGER NOT NULL DEFAULT 0,
        payload_json TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')))""",

    """CREATE TABLE IF NOT EXISTS scholar_citation_audits (
        audit_id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
        tenant_id TEXT NOT NULL, user_id TEXT NOT NULL,
        is_valid INTEGER NOT NULL DEFAULT 0, found_ids_json TEXT,
        hallucinated_ids_json TEXT, missing_ids_json TEXT,
        coverage REAL NOT NULL DEFAULT 0, payload_json TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')))""",

    """CREATE TABLE IF NOT EXISTS scholar_reflection_logs (
        reflection_id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
        tenant_id TEXT NOT NULL, user_id TEXT NOT NULL, phase TEXT NOT NULL,
        section_id TEXT, review_json TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')))""",

    """CREATE TABLE IF NOT EXISTS scholar_user_preferences (
        tenant_id TEXT NOT NULL, user_id TEXT NOT NULL,
        preference_key TEXT NOT NULL, preference_json TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now')),
        PRIMARY KEY (tenant_id, user_id, preference_key))""",

    """CREATE TABLE IF NOT EXISTS scholar_memories (
        memory_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, user_id TEXT NOT NULL,
        conversation_id TEXT, memory_type TEXT NOT NULL, content TEXT NOT NULL,
        normalized_content TEXT NOT NULL, importance REAL NOT NULL DEFAULT 0.5,
        confidence REAL NOT NULL DEFAULT 1.0, source_message_id TEXT, metadata_json TEXT,
        access_count INTEGER NOT NULL DEFAULT 0, last_accessed_at TEXT,
        status TEXT NOT NULL DEFAULT 'active',
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE (tenant_id, user_id, memory_type, normalized_content))""",

    """CREATE TABLE IF NOT EXISTS scholar_trace_events (
        trace_event_id INTEGER PRIMARY KEY AUTOINCREMENT,
        trace_id TEXT NOT NULL, task_id TEXT, tenant_id TEXT, user_id TEXT,
        span_name TEXT NOT NULL, event_type TEXT NOT NULL,
        provider TEXT, model TEXT, latency_ms INTEGER, metadata_json TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')))""",

    """CREATE TABLE IF NOT EXISTS scholar_operation_patterns (
        pattern_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, user_id TEXT NOT NULL,
        operation_name TEXT NOT NULL, signature TEXT NOT NULL, recipe_json TEXT NOT NULL,
        success_count INTEGER NOT NULL DEFAULT 0, failure_count INTEGER NOT NULL DEFAULT 0,
        first_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
        last_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE (tenant_id, user_id, signature))""",

    """CREATE TABLE IF NOT EXISTS scholar_skill_candidates (
        candidate_id TEXT PRIMARY KEY, pattern_id TEXT NOT NULL,
        tenant_id TEXT NOT NULL, user_id TEXT NOT NULL,
        name TEXT NOT NULL, description TEXT NOT NULL, manifest_json TEXT NOT NULL,
        evidence_count INTEGER NOT NULL DEFAULT 0, success_rate REAL NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'draft',
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE (tenant_id, user_id, pattern_id))""",

    """CREATE TABLE IF NOT EXISTS scholar_annotations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        paper_id TEXT NOT NULL,
        tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        page INTEGER NOT NULL DEFAULT 0,
        annotation_type TEXT NOT NULL DEFAULT 'highlight',
        color TEXT,
        points_json TEXT,
        content TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now')),
        FOREIGN KEY (tenant_id, user_id, paper_id)
            REFERENCES scholar_knowledge_papers(tenant_id, user_id, paper_id)
            ON DELETE CASCADE)""",

    """CREATE TABLE IF NOT EXISTS scholar_translations (
        translation_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL, user_id TEXT NOT NULL, paper_id TEXT NOT NULL,
        source_hash TEXT NOT NULL, source_text TEXT NOT NULL,
        source_language TEXT NOT NULL, target_language TEXT NOT NULL,
        translated_text TEXT NOT NULL, provider TEXT, model TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE (tenant_id, user_id, paper_id, source_hash, target_language),
        FOREIGN KEY (tenant_id, user_id, paper_id)
            REFERENCES scholar_knowledge_papers(tenant_id, user_id, paper_id)
            ON DELETE CASCADE)""",

    """CREATE TABLE IF NOT EXISTS scholar_settings (
        key TEXT PRIMARY KEY, value TEXT NOT NULL,
        updated_at TEXT NOT NULL DEFAULT (datetime('now')))""",

    """CREATE TABLE IF NOT EXISTS scholar_institution_profiles (
        profile_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, user_id TEXT NOT NULL,
        institution_name TEXT NOT NULL, access_type TEXT NOT NULL,
        login_url TEXT, proxy_prefix TEXT, enabled INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now')))""",

    """CREATE TABLE IF NOT EXISTS scholar_institution_sessions (
        session_id TEXT PRIMARY KEY, profile_id TEXT NOT NULL,
        tenant_id TEXT NOT NULL, user_id TEXT NOT NULL, status TEXT NOT NULL,
        authenticated_domains_json TEXT, verified_at TEXT, expires_at TEXT,
        revoked_at TEXT, last_error TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now')))""",

    """CREATE TABLE IF NOT EXISTS scholar_institution_downloads (
        download_id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
        tenant_id TEXT NOT NULL, user_id TEXT NOT NULL, conversation_id TEXT,
        source TEXT NOT NULL, source_url TEXT NOT NULL, title TEXT, doi TEXT,
        status TEXT NOT NULL, file_type TEXT, file_path TEXT, file_sha256 TEXT,
        file_size INTEGER, paper_id TEXT, failure_code TEXT, failure_message TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')), completed_at TEXT)""",
)

_INDEXES_SQL: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS idx_scholar_tasks_user ON scholar_tasks(tenant_id, user_id, updated_at)",
    "CREATE INDEX IF NOT EXISTS idx_scholar_tasks_status ON scholar_tasks(tenant_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_scholar_conversations_user ON scholar_conversations(tenant_id, user_id, updated_at)",
    "CREATE INDEX IF NOT EXISTS idx_scholar_conversation_messages_conversation ON scholar_conversation_messages(tenant_id, conversation_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_scholar_conversation_tool_calls_status ON scholar_conversation_tool_calls(tenant_id, user_id, conversation_id, status, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_scholar_knowledge_user ON scholar_knowledge_papers(tenant_id, user_id, updated_at)",
    "CREATE INDEX IF NOT EXISTS idx_scholar_task_events_task ON scholar_task_events(tenant_id, task_id, event_id)",
    "CREATE INDEX IF NOT EXISTS idx_scholar_citation_audits_task ON scholar_citation_audits(tenant_id, task_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_scholar_reflection_logs_task ON scholar_reflection_logs(tenant_id, task_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_scholar_memories_recall ON scholar_memories(tenant_id, user_id, status, memory_type, updated_at)",
    "CREATE INDEX IF NOT EXISTS idx_scholar_trace_events_trace ON scholar_trace_events(trace_id, trace_event_id)",
    "CREATE INDEX IF NOT EXISTS idx_operation_patterns_user ON scholar_operation_patterns(tenant_id, user_id, last_seen_at)",
    "CREATE INDEX IF NOT EXISTS idx_skill_candidates_user ON scholar_skill_candidates(tenant_id, user_id, status, updated_at)",
    "CREATE INDEX IF NOT EXISTS idx_institution_profiles_user ON scholar_institution_profiles(tenant_id, user_id, updated_at)",
    "CREATE INDEX IF NOT EXISTS idx_institution_sessions_user ON scholar_institution_sessions(tenant_id, user_id, status, updated_at)",
    "CREATE INDEX IF NOT EXISTS idx_institution_downloads_user ON scholar_institution_downloads(tenant_id, user_id, status, created_at)",
)


# ---------------------------------------------------------------------------
# Initialization & seeding
# ---------------------------------------------------------------------------

def initialize_database(create_database: bool = True) -> dict[str, Any]:
    del create_database
    if not is_available():
        raise PostgreSQLUnavailable("PostgreSQL is unavailable")
    row = fetch_one(
        "SELECT current_database() AS database, "
        "EXISTS(SELECT 1 FROM pg_extension WHERE extname = 'vector') AS vector_enabled, "
        "to_regclass('public.alembic_version') IS NOT NULL AS migration_table"
    ) or {}
    if not row.get("vector_enabled") or not row.get("migration_table"):
        raise PostgreSQLUnavailable("run 'alembic upgrade head' before starting ScholarAgent")
    revision = fetch_one("SELECT version_num FROM alembic_version LIMIT 1") or {}
    return {
        "database": row.get("database") or configured_database_name(),
        "pgvector": True,
        "revision": revision.get("version_num"),
    }


def seed_demo_data() -> None:
    tenants = (
        ("tenant_demo", "Scholar Demo Lab", {"plan": "demo"}),
        ("tenant_acme", "Acme AI Research", {"plan": "team"}),
    )
    users = (
        ("user_demo", "tenant_demo", "demo", password_hash("demo123"),
         "Demo Researcher", ["tenant_admin", "researcher"], "demo-key"),
        ("user_acme", "tenant_acme", "acme", password_hash("acme123"),
         "Acme Analyst", ["researcher"], "acme-key"),
    )
    for tid, name, meta in tenants:
        execute(
            "INSERT INTO scholar_tenants (tenant_id, name, metadata_json) VALUES (?, ?, ?) "
            "ON CONFLICT (tenant_id) DO UPDATE SET name = EXCLUDED.name, metadata_json = EXCLUDED.metadata_json",
            (tid, name, json.dumps(meta, ensure_ascii=False)),
        )
    for u in users:
        execute(
            "INSERT INTO scholar_users "
            "(user_id, tenant_id, username, password_hash, display_name, roles_json, api_key) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (user_id) DO UPDATE SET display_name = EXCLUDED.display_name, "
            "roles_json = EXCLUDED.roles_json, api_key = EXCLUDED.api_key",
            (*u[:5], json.dumps(u[5], ensure_ascii=False), u[6]),
        )


# ---------------------------------------------------------------------------
# JSON helpers (public)
# ---------------------------------------------------------------------------

def decode_json(value: Any, fallback: Any) -> Any:
    return _json_loads(value, fallback)


def encode_json(value: Any) -> str:
    return _json_dumps(value)


# ---------------------------------------------------------------------------
# New functions for settings (used by subsequent Tasks)
# ---------------------------------------------------------------------------

def get_setting(key: str, default: Any = None) -> Any:
    row = fetch_one("SELECT value FROM scholar_settings WHERE key = ?", (key,))
    if row is None:
        return default
    return decode_json(row["value"], default)


def set_setting(key: str, value: Any) -> None:
    execute(
        "INSERT INTO scholar_settings (key, value, updated_at) VALUES (?, ?, datetime('now')) "
        "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=CURRENT_TIMESTAMP",
        (key, encode_json(value)),
    )


def get_all_settings() -> dict[str, Any]:
    rows = fetch_all("SELECT key, value FROM scholar_settings")
    result: dict[str, Any] = {}
    for row in rows:
        result[row["key"]] = decode_json(row["value"], row["value"])
    return result


# ---------------------------------------------------------------------------
# Annotation CRUD
# ---------------------------------------------------------------------------

def save_annotations(tenant_id: str, user_id: str, paper_id: str,
                     annotations: list[dict[str, Any]]) -> int:
    """Replace all annotations for a paper (transactional). Returns count saved."""
    with connection() as conn, conn.cursor() as cursor:
        cursor.execute(
            _translate_sql(
                "DELETE FROM paper_annotations WHERE tenant_id = ? AND user_id = ? "
                "AND paper_uuid = (SELECT paper_uuid FROM papers WHERE tenant_id = ? "
                "AND user_id = ? AND paper_id = ? AND deleted_at IS NULL)"
            ),
            (tenant_id, user_id, tenant_id, user_id, paper_id),
        )
        for ann in annotations:
            cursor.execute(
                _translate_sql(
                "INSERT INTO paper_annotations "
                "(paper_uuid, tenant_id, user_id, page, annotation_type, color, points, content) "
                "SELECT paper_uuid, ?, ?, ?, ?, ?, CAST(? AS jsonb), ? FROM papers "
                "WHERE tenant_id = ? AND user_id = ? AND paper_id = ? AND deleted_at IS NULL",
                ),
                (
                    tenant_id, user_id,
                    int(ann.get("page", 0)),
                    str(ann.get("annotation_type", "highlight")),
                    ann.get("color"),
                    encode_json(ann.get("points", [])),
                    str(ann.get("content", "")),
                    tenant_id, user_id, paper_id,
                ),
            )
        conn.commit()
    return len(annotations)


def get_annotations(tenant_id: str, user_id: str, paper_id: str) -> list[dict[str, Any]]:
    """Get all annotations for a paper."""
    rows = fetch_all(
        "SELECT a.annotation_uuid AS id, a.page, a.annotation_type, a.color, "
        "a.points AS points_json, a.content, a.created_at, a.updated_at "
        "FROM paper_annotations a JOIN papers p ON p.paper_uuid = a.paper_uuid "
        "AND p.tenant_id = a.tenant_id AND p.user_id = a.user_id "
        "WHERE a.tenant_id = ? AND a.user_id = ? AND p.paper_id = ? AND p.deleted_at IS NULL "
        "ORDER BY a.page, a.created_at",
        (tenant_id, user_id, paper_id),
    )
    result: list[dict[str, Any]] = []
    for row in rows:
        result.append({
            "id": row["id"],
            "page": row["page"],
            "annotation_type": row["annotation_type"],
            "color": row.get("color"),
            "points": decode_json(row.get("points_json"), []),
            "content": row.get("content") or "",
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        })
    return result


def get_translation(
    tenant_id: str, user_id: str, paper_id: str, source_hash: str, target_language: str
) -> dict[str, Any] | None:
    return fetch_one(
        "SELECT t.translation_uuid AS translation_id, source_text, source_language, target_language, "
        "translated_text, provider, model, t.created_at FROM paper_translations t "
        "JOIN papers p ON p.paper_uuid=t.paper_uuid AND p.tenant_id=t.tenant_id AND p.user_id=t.user_id "
        "WHERE t.tenant_id = ? AND t.user_id = ? AND p.paper_id = ? "
        "AND t.source_hash = ? AND t.target_language = ? AND p.deleted_at IS NULL",
        (tenant_id, user_id, paper_id, source_hash, target_language),
    )


def save_translation(
    *, translation_id: str, tenant_id: str, user_id: str, paper_id: str,
    source_hash: str, source_text: str, source_language: str,
    target_language: str, translated_text: str, provider: str, model: str,
) -> None:
    execute(
        "INSERT INTO paper_translations "
        "(translation_uuid, tenant_id, user_id, paper_uuid, source_hash, source_text, "
        "source_language, target_language, translated_text, provider, model) "
        "SELECT ?, ?, ?, paper_uuid, ?, ?, ?, ?, ?, ?, ? FROM papers "
        "WHERE tenant_id = ? AND user_id = ? AND paper_id = ? AND deleted_at IS NULL "
        "ON CONFLICT (tenant_id, user_id, paper_uuid, source_hash, target_language) "
        "DO UPDATE SET translated_text=EXCLUDED.translated_text, provider=EXCLUDED.provider, "
        "model=EXCLUDED.model, created_at=CURRENT_TIMESTAMP",
        (translation_id, tenant_id, user_id, source_hash, source_text,
         source_language, target_language, translated_text, provider, model,
         tenant_id, user_id, paper_id),
    )


def migrate_annotations_json() -> int:
    """Legacy no-op: the PostgreSQL branch intentionally starts from a fresh database."""
    return 0
