from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator



class MySQLUnavailable(RuntimeError):
    """Raised when MySQL is configured but not reachable."""


# ---------------------------------------------------------------------------
# Connection management (per-thread sqlite3 connection)
# ---------------------------------------------------------------------------

_storage_path: Path | None = None
_local = threading.local()


def _db_path() -> Path:
    global _storage_path
    if _storage_path is not None:
        return _storage_path
    import os as _os
    storage_dir = Path(_os.getenv("SCHOLAR_STORAGE_DIR", "storage/runtime"))
    storage_dir.mkdir(parents=True, exist_ok=True)
    _storage_path = storage_dir / "scholar.db"
    return _storage_path


def _get_conn() -> sqlite3.Connection:
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(str(_db_path()))
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA foreign_keys=ON")
    return _local.conn


# ---------------------------------------------------------------------------
# SQL translation: MySQL -> SQLite
# ---------------------------------------------------------------------------

def _translate_sql(sql: str) -> str:
    sql = re.sub(r'\s+ENGINE=\S+', '', sql)
    sql = re.sub(r'\s+DEFAULT\s+CHARSET=\S+', '', sql)
    sql = re.sub(r'\s+COLLATE=\S+', '', sql)
    sql = sql.replace('AUTO_INCREMENT', 'AUTOINCREMENT')
    sql = re.sub(r'\bJSON\b', 'TEXT', sql)
    sql = re.sub(r'\bMEDIUMTEXT\b', 'TEXT', sql)
    sql = re.sub(r'\bTINYINT\(1\)\b', 'INTEGER', sql)
    sql = re.sub(r'\bDECIMAL\(\d+,\s*\d+\)', 'REAL', sql)
    sql = re.sub(r'\s+ON\s+UPDATE\s+CURRENT_TIMESTAMP', '', sql)
    sql = re.sub(r',?\s*FULLTEXT\s+KEY\s+\S+\s+\([^)]+\)', '', sql)
    sql = re.sub(r'UNIQUE\s+KEY\s+(\S+)\s+\(', r'UNIQUE (', sql)
    sql = re.sub(r',?\s*KEY\s+\S+\s+\([^)]+\)', '', sql)
    sql = re.sub(
        r',?\s*CONSTRAINT\s+\S+\s+FOREIGN\s+KEY\s+\([^)]+\)\s+REFERENCES\s+\S+\s*\([^)]+\)(\s+ON\s+DELETE\s+\S+)?',
        '', sql,
    )
    sql = re.sub(r'/\*.*?\*/', '', sql, flags=re.DOTALL)
    # Convert MySQL placeholders to SQLite placeholders
    sql = sql.replace('%s', '?')
    # Convert ON DUPLICATE KEY UPDATE to SQLite ON CONFLICT ... DO UPDATE SET
    pk_match = re.search(r'INSERT\s+INTO\s+\S+\s*\((\w+)', sql, re.IGNORECASE)
    if pk_match:
        pk_col = pk_match.group(1)
        sql = re.sub(
            r'ON\s+DUPLICATE\s+KEY\s+UPDATE\s+',
            f'ON CONFLICT({pk_col}) DO UPDATE SET ',
            sql,
            flags=re.IGNORECASE,
        )
        sql = re.sub(r'VALUES\((\w+)\)', r'excluded.\1', sql)
    return sql


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
    if isinstance(v, bool):
        return 1 if v else 0
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
    return "scholar_agent"


def should_use_mysql() -> bool:
    return True


def reset_availability_cache() -> None:
    pass


@contextmanager
def connection(with_database: bool = True) -> Iterator[Any]:
    """Yield the per-thread sqlite3 connection (backward compatible API)."""
    conn = _get_conn()
    try:
        yield conn
    finally:
        pass  # connection is reused per thread


def is_available() -> bool:
    return True


def execute(
    sql: str,
    params: tuple[Any, ...] | dict[str, Any] | None = None,
) -> int:
    conn = _get_conn()
    sql = _translate_sql(sql)
    params = _adapt_params(params)
    cursor = conn.execute(sql, params)
    conn.commit()
    return cursor.rowcount


def fetch_one(
    sql: str,
    params: tuple[Any, ...] | dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    conn = _get_conn()
    sql = _translate_sql(sql)
    params = _adapt_params(params)
    cursor = conn.execute(sql, params)
    row = cursor.fetchone()
    return dict(row) if row else None


def fetch_all(
    sql: str,
    params: tuple[Any, ...] | dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    conn = _get_conn()
    sql = _translate_sql(sql)
    params = _adapt_params(params)
    cursor = conn.execute(sql, params)
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
    "CREATE INDEX IF NOT EXISTS idx_institution_profiles_user ON scholar_institution_profiles(tenant_id, user_id, updated_at)",
    "CREATE INDEX IF NOT EXISTS idx_institution_sessions_user ON scholar_institution_sessions(tenant_id, user_id, status, updated_at)",
    "CREATE INDEX IF NOT EXISTS idx_institution_downloads_user ON scholar_institution_downloads(tenant_id, user_id, status, created_at)",
)


# ---------------------------------------------------------------------------
# Initialization & seeding
# ---------------------------------------------------------------------------

def initialize_database(create_database: bool = True) -> dict[str, Any]:
    conn = _get_conn()
    for statement in SCHEMA_SQL:
        conn.execute(statement)
    for index_sql in _INDEXES_SQL:
        conn.execute(index_sql)
    seed_demo_data()
    migrated = migrate_annotations_json()
    result = {"database": "scholar_agent", "tables": len(SCHEMA_SQL)}
    if migrated:
        result["migrated_annotations"] = migrated
    return result


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
    conn = _get_conn()
    for tid, name, meta in tenants:
        conn.execute(
            "INSERT OR REPLACE INTO scholar_tenants (tenant_id, name, metadata_json) VALUES (?, ?, ?)",
            (tid, name, json.dumps(meta, ensure_ascii=False)),
        )
    for u in users:
        conn.execute(
            "INSERT OR REPLACE INTO scholar_users "
            "(user_id, tenant_id, username, password_hash, display_name, roles_json, api_key) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (*u[:5], json.dumps(u[5], ensure_ascii=False), u[6]),
        )
    conn.commit()


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
        "INSERT OR REPLACE INTO scholar_settings (key, value, updated_at) VALUES (?, ?, datetime('now'))",
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
    conn = _get_conn()
    # Flush any pending implicit transaction so we can start a clean one
    if conn.in_transaction:
        conn.execute("COMMIT")
    conn.execute("BEGIN")
    try:
        conn.execute(
            "DELETE FROM scholar_annotations WHERE tenant_id = ? AND user_id = ? AND paper_id = ?",
            (tenant_id, user_id, paper_id),
        )
        count = 0
        for ann in annotations:
            conn.execute(
                "INSERT INTO scholar_annotations "
                "(paper_id, tenant_id, user_id, page, annotation_type, color, points_json, content) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    paper_id, tenant_id, user_id,
                    int(ann.get("page", 0)),
                    str(ann.get("annotation_type", "highlight")),
                    ann.get("color"),
                    encode_json(ann.get("points", [])),
                    str(ann.get("content", "")),
                ),
            )
            count += 1
        conn.execute("COMMIT")
        return count
    except Exception:
        conn.execute("ROLLBACK")
        raise


def get_annotations(tenant_id: str, user_id: str, paper_id: str) -> list[dict[str, Any]]:
    """Get all annotations for a paper."""
    rows = fetch_all(
        "SELECT id, page, annotation_type, color, points_json, content, created_at, updated_at "
        "FROM scholar_annotations "
        "WHERE tenant_id = ? AND user_id = ? AND paper_id = ? "
        "ORDER BY page, id",
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
        "SELECT translation_id, source_text, source_language, target_language, "
        "translated_text, provider, model, created_at FROM scholar_translations "
        "WHERE tenant_id = ? AND user_id = ? AND paper_id = ? "
        "AND source_hash = ? AND target_language = ?",
        (tenant_id, user_id, paper_id, source_hash, target_language),
    )


def save_translation(
    *, translation_id: str, tenant_id: str, user_id: str, paper_id: str,
    source_hash: str, source_text: str, source_language: str,
    target_language: str, translated_text: str, provider: str, model: str,
) -> None:
    execute(
        "INSERT OR REPLACE INTO scholar_translations "
        "(translation_id, tenant_id, user_id, paper_id, source_hash, source_text, "
        "source_language, target_language, translated_text, provider, model) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (translation_id, tenant_id, user_id, paper_id, source_hash, source_text,
         source_language, target_language, translated_text, provider, model),
    )


def migrate_annotations_json() -> int:
    """Migrate legacy JSON annotation files to SQLite. Returns count of migrated papers."""
    import os as _os
    from pathlib import Path as _Path
    annotations_root = _Path(_os.getenv("SCHOLAR_STORAGE_DIR", "storage/runtime")) / "annotations"
    if not annotations_root.exists():
        return 0
    count = 0
    for json_file in annotations_root.rglob("*.json"):
        try:
            data = json.loads(json_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        paper_id = data.get("paper_id", "")
        if not paper_id:
            continue
        # Read tenant_id/user_id from directory structure: annotations/{tenant}/{user}/{digest}.json
        parts = json_file.relative_to(annotations_root).parts
        if len(parts) < 2:
            continue
        tenant_id, user_id = parts[0], parts[1]
        strokes = data.get("strokes", [])
        notes = data.get("notes", "")
        # Convert old format to new: each stroke becomes an annotation row
        annotations: list[dict[str, Any]] = []
        for stroke in strokes:
            annotations.append({
                "page": stroke.get("page", 0),
                "annotation_type": stroke.get("type", "highlight"),
                "color": stroke.get("color"),
                "points": stroke.get("points", []),
                "content": "",
            })
        if notes:
            annotations.append({
                "page": 0,
                "annotation_type": "note",
                "color": None,
                "points": [],
                "content": notes,
            })
        if annotations:
            try:
                save_annotations(tenant_id, user_id, paper_id, annotations)
                count += 1
            except Exception:
                continue  # skip papers that don't exist yet
        # Rename migrated file
        try:
            json_file.rename(json_file.with_suffix(".json.bak"))
        except OSError:
            pass
    return count
