from __future__ import annotations

import hashlib
import json
import time
from contextlib import contextmanager
from typing import Any, Iterator
from urllib.parse import parse_qs, unquote, urlparse

from app.config import get_settings

try:
    import pymysql
    from pymysql.cursors import DictCursor
except ImportError:  # pragma: no cover - exercised when optional dependency is absent
    pymysql = None
    DictCursor = None


class MySQLUnavailable(RuntimeError):
    """Raised when MySQL is configured but not reachable."""


_availability_cache: tuple[float, bool] = (0.0, False)


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


def _config(with_database: bool = True) -> dict[str, Any]:
    parsed = urlparse(get_settings().mysql_url)
    query = parse_qs(parsed.query)
    database = parsed.path.lstrip("/")
    return {
        "host": parsed.hostname or "127.0.0.1",
        "port": parsed.port or 3306,
        "user": unquote(parsed.username or "scholar"),
        "password": unquote(parsed.password or "scholar"),
        "database": database if with_database else None,
        "charset": query.get("charset", ["utf8mb4"])[0],
        "autocommit": True,
        "cursorclass": DictCursor,
        "connect_timeout": float(query.get("connect_timeout", ["0.8"])[0]),
        "read_timeout": float(query.get("read_timeout", ["2"])[0]),
        "write_timeout": float(query.get("write_timeout", ["2"])[0]),
    }


def configured_database_name() -> str:
    parsed = urlparse(get_settings().mysql_url)
    return parsed.path.lstrip("/") or "scholar_agent"


def should_use_mysql() -> bool:
    return get_settings().storage_backend.lower() in {"auto", "mysql"}


def reset_availability_cache() -> None:
    global _availability_cache
    _availability_cache = (0.0, False)


@contextmanager
def connection(with_database: bool = True) -> Iterator[Any]:
    if pymysql is None or not should_use_mysql():
        raise MySQLUnavailable("PyMySQL is not installed or MySQL storage is disabled")
    cfg = _config(with_database=with_database)
    if not with_database:
        cfg.pop("database", None)
    try:
        conn = pymysql.connect(**cfg)
    except Exception as exc:  # pragma: no cover - depends on local infrastructure
        raise MySQLUnavailable(str(exc)) from exc
    try:
        yield conn
    finally:
        conn.close()


def is_available() -> bool:
    global _availability_cache
    now = time.time()
    if now - _availability_cache[0] < 5:
        return _availability_cache[1]
    try:
        with connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
        _availability_cache = (now, True)
        return True
    except MySQLUnavailable:
        _availability_cache = (now, False)
        return False


def execute(sql: str, params: tuple[Any, ...] | dict[str, Any] | None = None) -> int:
    with connection() as conn:
        with conn.cursor() as cursor:
            return cursor.execute(sql, params)


def fetch_one(sql: str, params: tuple[Any, ...] | dict[str, Any] | None = None) -> dict[str, Any] | None:
    with connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchone()


def fetch_all(sql: str, params: tuple[Any, ...] | dict[str, Any] | None = None) -> list[dict[str, Any]]:
    with connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
            return list(cursor.fetchall())


SCHEMA_SQL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS scholar_tenants (
        tenant_id VARCHAR(64) PRIMARY KEY,
        name VARCHAR(200) NOT NULL,
        status VARCHAR(32) NOT NULL DEFAULT 'active',
        metadata_json JSON NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS scholar_users (
        user_id VARCHAR(64) PRIMARY KEY,
        tenant_id VARCHAR(64) NOT NULL,
        username VARCHAR(120) NOT NULL,
        password_hash CHAR(64) NOT NULL,
        display_name VARCHAR(200) NOT NULL,
        roles_json JSON NULL,
        api_key VARCHAR(160) NOT NULL UNIQUE,
        status VARCHAR(32) NOT NULL DEFAULT 'active',
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uq_scholar_users_tenant_username (tenant_id, username),
        KEY idx_scholar_users_api_key (api_key),
        CONSTRAINT fk_scholar_users_tenant
            FOREIGN KEY (tenant_id) REFERENCES scholar_tenants(tenant_id)
            ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS scholar_tasks (
        task_id CHAR(36) PRIMARY KEY,
        tenant_id VARCHAR(64) NOT NULL,
        user_id VARCHAR(64) NOT NULL,
        status VARCHAR(32) NOT NULL,
        phase VARCHAR(80) NOT NULL,
        percent INT NOT NULL DEFAULT 0,
        trace_id VARCHAR(80) NULL,
        request_json JSON NOT NULL,
        result_json JSON NULL,
        error TEXT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        KEY idx_scholar_tasks_user (tenant_id, user_id, updated_at),
        KEY idx_scholar_tasks_status (tenant_id, status),
        CONSTRAINT fk_scholar_tasks_user
            FOREIGN KEY (user_id) REFERENCES scholar_users(user_id)
            ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS scholar_conversations (
        conversation_id CHAR(36) PRIMARY KEY,
        tenant_id VARCHAR(64) NOT NULL,
        user_id VARCHAR(64) NOT NULL,
        title VARCHAR(240) NOT NULL,
        skill_id VARCHAR(120) NOT NULL DEFAULT 'general_assistant',
        status VARCHAR(32) NOT NULL DEFAULT 'active',
        metadata_json JSON NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        KEY idx_scholar_conversations_user (tenant_id, user_id, updated_at),
        KEY idx_scholar_conversations_skill (tenant_id, user_id, skill_id),
        CONSTRAINT fk_scholar_conversations_user
            FOREIGN KEY (user_id) REFERENCES scholar_users(user_id)
            ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS scholar_conversation_messages (
        message_id CHAR(36) PRIMARY KEY,
        conversation_id CHAR(36) NOT NULL,
        tenant_id VARCHAR(64) NOT NULL,
        user_id VARCHAR(64) NOT NULL,
        role VARCHAR(32) NOT NULL,
        content MEDIUMTEXT NOT NULL,
        skill_id VARCHAR(120) NULL,
        metadata_json JSON NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        KEY idx_scholar_conversation_messages_conversation (tenant_id, conversation_id, created_at),
        CONSTRAINT fk_scholar_conversation_messages_conversation
            FOREIGN KEY (conversation_id) REFERENCES scholar_conversations(conversation_id)
            ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS scholar_knowledge_papers (
        paper_id VARCHAR(260) NOT NULL,
        tenant_id VARCHAR(64) NOT NULL,
        user_id VARCHAR(64) NOT NULL,
        source VARCHAR(40) NOT NULL,
        title VARCHAR(500) NOT NULL,
        authors_json JSON NULL,
        abstract TEXT NULL,
        full_text MEDIUMTEXT NULL,
        published_at VARCHAR(40) NULL,
        doi VARCHAR(200) NULL,
        arxiv_id VARCHAR(120) NULL,
        url VARCHAR(500) NULL,
        metadata_json JSON NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (tenant_id, user_id, paper_id),
        KEY idx_scholar_knowledge_user (tenant_id, user_id, updated_at),
        KEY idx_scholar_knowledge_source (tenant_id, user_id, source),
        FULLTEXT KEY ft_scholar_knowledge_title_abstract (title, abstract),
        CONSTRAINT fk_scholar_knowledge_user
            FOREIGN KEY (user_id) REFERENCES scholar_users(user_id)
            ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS scholar_rag_chunks (
        chunk_id CHAR(64) PRIMARY KEY,
        tenant_id VARCHAR(64) NOT NULL,
        user_id VARCHAR(64) NOT NULL,
        paper_id VARCHAR(260) NOT NULL,
        chunk_index INT NOT NULL,
        content_hash CHAR(64) NOT NULL,
        content TEXT NOT NULL,
        token_count INT NOT NULL DEFAULT 0,
        keywords_json JSON NULL,
        embedding_json JSON NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE KEY uq_scholar_rag_chunk (tenant_id, user_id, paper_id, chunk_index),
        KEY idx_scholar_rag_paper (tenant_id, user_id, paper_id),
        FULLTEXT KEY ft_scholar_rag_content (content),
        CONSTRAINT fk_scholar_rag_paper
            FOREIGN KEY (tenant_id, user_id, paper_id)
            REFERENCES scholar_knowledge_papers(tenant_id, user_id, paper_id)
            ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS scholar_task_events (
        event_id BIGINT AUTO_INCREMENT PRIMARY KEY,
        task_id CHAR(36) NOT NULL,
        tenant_id VARCHAR(64) NOT NULL,
        user_id VARCHAR(64) NOT NULL,
        event VARCHAR(80) NOT NULL,
        phase VARCHAR(80) NOT NULL,
        message TEXT NULL,
        percent INT NOT NULL DEFAULT 0,
        payload_json JSON NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        KEY idx_scholar_task_events_task (tenant_id, task_id, event_id),
        KEY idx_scholar_task_events_user (tenant_id, user_id, created_at),
        CONSTRAINT fk_scholar_task_events_task
            FOREIGN KEY (task_id) REFERENCES scholar_tasks(task_id)
            ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS scholar_citation_audits (
        audit_id BIGINT AUTO_INCREMENT PRIMARY KEY,
        task_id CHAR(36) NOT NULL,
        tenant_id VARCHAR(64) NOT NULL,
        user_id VARCHAR(64) NOT NULL,
        is_valid BOOLEAN NOT NULL DEFAULT FALSE,
        found_ids_json JSON NULL,
        hallucinated_ids_json JSON NULL,
        missing_ids_json JSON NULL,
        coverage DECIMAL(8, 6) NOT NULL DEFAULT 0,
        payload_json JSON NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        KEY idx_scholar_citation_audits_task (tenant_id, task_id, created_at),
        CONSTRAINT fk_scholar_citation_audits_task
            FOREIGN KEY (task_id) REFERENCES scholar_tasks(task_id)
            ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS scholar_reflection_logs (
        reflection_id BIGINT AUTO_INCREMENT PRIMARY KEY,
        task_id CHAR(36) NOT NULL,
        tenant_id VARCHAR(64) NOT NULL,
        user_id VARCHAR(64) NOT NULL,
        phase VARCHAR(80) NOT NULL,
        section_id VARCHAR(120) NULL,
        review_json JSON NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        KEY idx_scholar_reflection_logs_task (tenant_id, task_id, created_at),
        CONSTRAINT fk_scholar_reflection_logs_task
            FOREIGN KEY (task_id) REFERENCES scholar_tasks(task_id)
            ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS scholar_user_preferences (
        tenant_id VARCHAR(64) NOT NULL,
        user_id VARCHAR(64) NOT NULL,
        preference_key VARCHAR(120) NOT NULL,
        preference_json JSON NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (tenant_id, user_id, preference_key),
        CONSTRAINT fk_scholar_user_preferences_user
            FOREIGN KEY (user_id) REFERENCES scholar_users(user_id)
            ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS scholar_trace_events (
        trace_event_id BIGINT AUTO_INCREMENT PRIMARY KEY,
        trace_id VARCHAR(120) NOT NULL,
        task_id CHAR(36) NULL,
        tenant_id VARCHAR(64) NULL,
        user_id VARCHAR(64) NULL,
        span_name VARCHAR(160) NOT NULL,
        event_type VARCHAR(80) NOT NULL,
        provider VARCHAR(80) NULL,
        model VARCHAR(160) NULL,
        latency_ms INT NULL,
        metadata_json JSON NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        KEY idx_scholar_trace_events_trace (trace_id, trace_event_id),
        KEY idx_scholar_trace_events_task (tenant_id, task_id, trace_event_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
)


def initialize_database(create_database: bool = True) -> dict[str, Any]:
    global _availability_cache
    database = configured_database_name()
    if create_database:
        with connection(with_database=False) as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"CREATE DATABASE IF NOT EXISTS `{database}` "
                    "DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
    with connection() as conn:
        with conn.cursor() as cursor:
            for statement in SCHEMA_SQL:
                cursor.execute(statement)
    seed_demo_data()
    _availability_cache = (time.time(), True)
    return {"database": database, "tables": len(SCHEMA_SQL)}


def seed_demo_data() -> None:
    tenants = (
        ("tenant_demo", "Scholar Demo Lab", {"plan": "demo"}),
        ("tenant_acme", "Acme AI Research", {"plan": "team"}),
    )
    users = (
        (
            "user_demo",
            "tenant_demo",
            "demo",
            password_hash("demo123"),
            "Demo Researcher",
            ["tenant_admin", "researcher"],
            "demo-key",
        ),
        (
            "user_acme",
            "tenant_acme",
            "acme",
            password_hash("acme123"),
            "Acme Analyst",
            ["researcher"],
            "acme-key",
        ),
    )
    with connection() as conn:
        with conn.cursor() as cursor:
            for tenant_id, name, metadata in tenants:
                cursor.execute(
                    """
                    INSERT INTO scholar_tenants (tenant_id, name, metadata_json)
                    VALUES (%s, %s, %s)
                    ON DUPLICATE KEY UPDATE name = VALUES(name), metadata_json = VALUES(metadata_json)
                    """,
                    (tenant_id, name, _json_dumps(metadata)),
                )
            for user in users:
                cursor.execute(
                    """
                    INSERT INTO scholar_users
                        (user_id, tenant_id, username, password_hash, display_name, roles_json, api_key)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        username = VALUES(username),
                        password_hash = VALUES(password_hash),
                        display_name = VALUES(display_name),
                        roles_json = VALUES(roles_json),
                        api_key = VALUES(api_key),
                        status = 'active'
                    """,
                    (*user[:5], _json_dumps(user[5]), user[6]),
                )


def decode_json(value: Any, fallback: Any) -> Any:
    return _json_loads(value, fallback)


def encode_json(value: Any) -> str:
    return _json_dumps(value)
