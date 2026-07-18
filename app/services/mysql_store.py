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
    """Raised when the configured PostgreSQL database is not reachable or migrated."""


MySQLUnavailable = PostgreSQLUnavailable

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


def _translate_sql(sql: str) -> str:
    """Translate only the small placeholder/date subset used by legacy callers."""
    translated = re.sub(r"datetime\(\s*'now'\s*\)", "CURRENT_TIMESTAMP", sql, flags=re.I)
    translated = translated.replace("`", '"')
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


def _adapt_params(params: Any) -> Any:
    if params is None:
        return ()
    if isinstance(params, dict):
        return {key: _adapt_single(value) for key, value in params.items()}
    return tuple(_adapt_single(value) for value in params)


def _adapt_single(value: Any) -> Any:
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return value


def password_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def encode_json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False)


def decode_json(value: Any, fallback: Any) -> Any:
    if value in (None, ""):
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def configured_database_name() -> str:
    return urlparse(_sync_database_url()).path.lstrip("/") or "scholar_agent"


def should_use_mysql() -> bool:
    return False


def reset_availability_cache() -> None:
    global _availability
    _availability = None


@contextmanager
def connection(with_database: bool = True) -> Iterator[Any]:
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


def execute(sql: str, params: tuple[Any, ...] | dict[str, Any] | None = None) -> int:
    with connection() as conn, conn.cursor() as cursor:
        cursor.execute(_translate_sql(sql), _adapt_params(params))
        affected = cursor.rowcount
        conn.commit()
        return affected


def fetch_one(
    sql: str, params: tuple[Any, ...] | dict[str, Any] | None = None
) -> dict[str, Any] | None:
    with connection() as conn, conn.cursor() as cursor:
        cursor.execute(_translate_sql(sql), _adapt_params(params))
        row = cursor.fetchone()
        return dict(row) if row else None


def fetch_all(
    sql: str, params: tuple[Any, ...] | dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    with connection() as conn, conn.cursor() as cursor:
        cursor.execute(_translate_sql(sql), _adapt_params(params))
        return [dict(row) for row in cursor.fetchall()]


def initialize_database(create_database: bool = True) -> dict[str, Any]:
    del create_database
    reset_availability_cache()
    if not is_available():
        raise PostgreSQLUnavailable("PostgreSQL is unavailable")
    row = fetch_one(
        "SELECT current_database() AS database, "
        "EXISTS(SELECT 1 FROM pg_extension WHERE extname='vector') AS vector_enabled, "
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
        ("user_demo", "tenant_demo", "demo", password_hash("demo123"), "Demo Researcher", ["tenant_admin", "researcher"], "demo-key"),
        ("user_acme", "tenant_acme", "acme", password_hash("acme123"), "Acme Analyst", ["researcher"], "acme-key"),
    )
    for tenant_id, name, metadata in tenants:
        execute(
            "INSERT INTO scholar_tenants (tenant_id, name, metadata_json) VALUES (?, ?, ?) "
            "ON CONFLICT (tenant_id) DO UPDATE SET name=EXCLUDED.name, metadata_json=EXCLUDED.metadata_json",
            (tenant_id, name, encode_json(metadata)),
        )
    for user in users:
        execute(
            "INSERT INTO scholar_users "
            "(user_id, tenant_id, username, password_hash, display_name, roles_json, api_key) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (user_id) DO UPDATE SET "
            "display_name=EXCLUDED.display_name, roles_json=EXCLUDED.roles_json, api_key=EXCLUDED.api_key",
            (*user[:5], encode_json(user[5]), user[6]),
        )


def get_setting(key: str, default: Any = None) -> Any:
    row = fetch_one("SELECT value FROM scholar_settings WHERE key=?", (key,))
    return default if row is None else decode_json(row["value"], default)


def set_setting(key: str, value: Any) -> None:
    execute(
        "INSERT INTO scholar_settings (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) "
        "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=CURRENT_TIMESTAMP",
        (key, encode_json(value)),
    )


def get_all_settings() -> dict[str, Any]:
    return {
        row["key"]: decode_json(row["value"], row["value"])
        for row in fetch_all("SELECT key, value FROM scholar_settings")
    }


def _set_tenant_context(cursor: Any, tenant_id: str, user_id: str) -> None:
    cursor.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
    cursor.execute("SELECT set_config('app.user_id', %s, true)", (user_id,))


def save_annotations(
    tenant_id: str, user_id: str, paper_id: str, annotations: list[dict[str, Any]]
) -> int:
    with connection() as conn, conn.cursor() as cursor:
        _set_tenant_context(cursor, tenant_id, user_id)
        cursor.execute(
            _translate_sql(
                "DELETE FROM paper_annotations WHERE tenant_id=? AND user_id=? "
                "AND paper_uuid=(SELECT paper_uuid FROM papers WHERE tenant_id=? "
                "AND user_id=? AND paper_id=? AND deleted_at IS NULL)"
            ),
            (tenant_id, user_id, tenant_id, user_id, paper_id),
        )
        for annotation in annotations:
            cursor.execute(
                _translate_sql(
                    "INSERT INTO paper_annotations "
                    "(paper_uuid, tenant_id, user_id, page, annotation_type, color, points, content) "
                    "SELECT paper_uuid, ?, ?, ?, ?, ?, CAST(? AS jsonb), ? FROM papers "
                    "WHERE tenant_id=? AND user_id=? AND paper_id=? AND deleted_at IS NULL"
                ),
                (
                    tenant_id,
                    user_id,
                    int(annotation.get("page", 0)),
                    str(annotation.get("annotation_type", "highlight")),
                    annotation.get("color"),
                    encode_json(annotation.get("points", [])),
                    str(annotation.get("content", "")),
                    tenant_id,
                    user_id,
                    paper_id,
                ),
            )
        conn.commit()
    return len(annotations)


def get_annotations(tenant_id: str, user_id: str, paper_id: str) -> list[dict[str, Any]]:
    with connection() as conn, conn.cursor() as cursor:
        _set_tenant_context(cursor, tenant_id, user_id)
        cursor.execute(
            _translate_sql(
                "SELECT a.annotation_uuid AS id, a.page, a.annotation_type, a.color, "
                "a.points AS points_json, a.content, a.created_at, a.updated_at "
                "FROM paper_annotations a JOIN papers p ON p.paper_uuid=a.paper_uuid "
                "AND p.tenant_id=a.tenant_id AND p.user_id=a.user_id "
                "WHERE a.tenant_id=? AND a.user_id=? AND p.paper_id=? AND p.deleted_at IS NULL "
                "ORDER BY a.page, a.created_at"
            ),
            (tenant_id, user_id, paper_id),
        )
        rows = [dict(row) for row in cursor.fetchall()]
    return [
        {
            "id": row["id"],
            "page": row["page"],
            "annotation_type": row["annotation_type"],
            "color": row.get("color"),
            "points": decode_json(row.get("points_json"), []),
            "content": row.get("content") or "",
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        for row in rows
    ]


def get_translation(
    tenant_id: str, user_id: str, paper_id: str, source_hash: str, target_language: str
) -> dict[str, Any] | None:
    with connection() as conn, conn.cursor() as cursor:
        _set_tenant_context(cursor, tenant_id, user_id)
        cursor.execute(
            _translate_sql(
                "SELECT t.translation_uuid AS translation_id, source_text, source_language, "
                "target_language, translated_text, provider, model, t.created_at "
                "FROM paper_translations t JOIN papers p ON p.paper_uuid=t.paper_uuid "
                "AND p.tenant_id=t.tenant_id AND p.user_id=t.user_id "
                "WHERE t.tenant_id=? AND t.user_id=? AND p.paper_id=? AND t.source_hash=? "
                "AND t.target_language=? AND p.deleted_at IS NULL"
            ),
            (tenant_id, user_id, paper_id, source_hash, target_language),
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def save_translation(
    *, translation_id: str, tenant_id: str, user_id: str, paper_id: str,
    source_hash: str, source_text: str, source_language: str,
    target_language: str, translated_text: str, provider: str, model: str,
) -> None:
    with connection() as conn, conn.cursor() as cursor:
        _set_tenant_context(cursor, tenant_id, user_id)
        cursor.execute(
            _translate_sql(
                "INSERT INTO paper_translations "
                "(translation_uuid, tenant_id, user_id, paper_uuid, source_hash, source_text, "
                "source_language, target_language, translated_text, provider, model) "
                "SELECT ?, ?, ?, paper_uuid, ?, ?, ?, ?, ?, ?, ? FROM papers "
                "WHERE tenant_id=? AND user_id=? AND paper_id=? AND deleted_at IS NULL "
                "ON CONFLICT (tenant_id, user_id, paper_uuid, source_hash, target_language) "
                "DO UPDATE SET translated_text=EXCLUDED.translated_text, provider=EXCLUDED.provider, "
                "model=EXCLUDED.model, updated_at=CURRENT_TIMESTAMP"
            ),
            (
                translation_id, tenant_id, user_id, source_hash, source_text,
                source_language, target_language, translated_text, provider, model,
                tenant_id, user_id, paper_id,
            ),
        )
        conn.commit()


def migrate_annotations_json() -> int:
    return 0
