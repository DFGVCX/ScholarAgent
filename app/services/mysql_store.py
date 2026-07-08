from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from app.config import get_settings


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
    settings = get_settings()
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    _storage_path = settings.storage_dir / "scholar.db"
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

    """CREATE TABLE IF NOT EXISTS scholar_trace_events (
        trace_event_id INTEGER PRIMARY KEY AUTOINCREMENT,
        trace_id TEXT NOT NULL, task_id TEXT, tenant_id TEXT, user_id TEXT,
        span_name TEXT NOT NULL, event_type TEXT NOT NULL,
        provider TEXT, model TEXT, latency_ms INTEGER, metadata_json TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')))""",

    """CREATE TABLE IF NOT EXISTS scholar_settings (
        key TEXT PRIMARY KEY, value TEXT NOT NULL,
        updated_at TEXT NOT NULL DEFAULT (datetime('now')))""",
)

_INDEXES_SQL: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS idx_scholar_tasks_user ON scholar_tasks(tenant_id, user_id, updated_at)",
    "CREATE INDEX IF NOT EXISTS idx_scholar_tasks_status ON scholar_tasks(tenant_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_scholar_conversations_user ON scholar_conversations(tenant_id, user_id, updated_at)",
    "CREATE INDEX IF NOT EXISTS idx_scholar_conversation_messages_conversation ON scholar_conversation_messages(tenant_id, conversation_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_scholar_knowledge_user ON scholar_knowledge_papers(tenant_id, user_id, updated_at)",
    "CREATE INDEX IF NOT EXISTS idx_scholar_task_events_task ON scholar_task_events(tenant_id, task_id, event_id)",
    "CREATE INDEX IF NOT EXISTS idx_scholar_citation_audits_task ON scholar_citation_audits(tenant_id, task_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_scholar_reflection_logs_task ON scholar_reflection_logs(tenant_id, task_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_scholar_trace_events_trace ON scholar_trace_events(trace_id, trace_event_id)",
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
    return {"database": "scholar_agent", "tables": len(SCHEMA_SQL)}


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
