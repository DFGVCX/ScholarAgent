from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings
from app.services import mysql_store
from app.services.auth_service import auth_service
from app.services.rag_service import rag_service
from mcp_server.scholar_mcp.models import PaperRecord
from mcp_server.scholar_mcp.store import knowledge_store


def _parse_mysql_url(url: str) -> dict[str, Any]:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    return {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 3306,
        "user": unquote(parsed.username or "root"),
        "password": unquote(parsed.password or ""),
        "database": parsed.path.lstrip("/") or "",
        "charset": query.get("charset", ["utf8mb4"])[0],
    }


def _database_identifier(value: str) -> str:
    if not value or not all(ch.isalnum() or ch == "_" for ch in value):
        raise ValueError(f"Unsafe MySQL database name: {value!r}")
    return f"`{value}`"


def _user_spec(conn: Any, user: str, host: str) -> str:
    return f"'{conn.escape_string(user)}'@'{conn.escape_string(host)}'"


def ensure_database_and_user() -> dict[str, Any]:
    try:
        import pymysql
    except ImportError as exc:
        raise RuntimeError("PyMySQL is required before bootstrapping MySQL") from exc

    target_url = os.getenv("SCHOLAR_MYSQL_URL", get_settings().mysql_url)
    admin_url = os.getenv(
        "SCHOLAR_MYSQL_ADMIN_URL",
        "mysql://root:root@localhost:3306/mysql?charset=utf8mb4",
    )
    target = _parse_mysql_url(target_url)
    admin = _parse_mysql_url(admin_url)
    database = target["database"] or mysql_store.configured_database_name()
    app_user = target["user"]
    app_password = target["password"]
    db_ident = _database_identifier(database)
    conn = pymysql.connect(
        host=admin["host"],
        port=admin["port"],
        user=admin["user"],
        password=admin["password"],
        database=admin["database"] or None,
        charset=admin["charset"],
        autocommit=True,
        connect_timeout=3,
        read_timeout=10,
        write_timeout=10,
    )
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS {db_ident} "
                "DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
            for host in ("localhost", "127.0.0.1"):
                spec = _user_spec(conn, app_user, host)
                cursor.execute(f"CREATE USER IF NOT EXISTS {spec} IDENTIFIED BY %s", (app_password,))
                cursor.execute(f"ALTER USER {spec} IDENTIFIED BY %s", (app_password,))
                cursor.execute(f"GRANT ALL PRIVILEGES ON {db_ident}.* TO {spec}")
            cursor.execute("FLUSH PRIVILEGES")
    finally:
        conn.close()
    os.environ["SCHOLAR_MYSQL_URL"] = target_url
    return {
        "database": database,
        "app_user": app_user,
        "hosts": ["localhost", "127.0.0.1"],
    }


async def _seed_rag() -> dict[str, Any]:
    paper = PaperRecord(
        paper_id="paper:manual:bootstrap-rag",
        tenant_id="tenant_demo",
        user_id="user_demo",
        source="manual",
        title="Tenant-Scoped RAG and Citation Audit Bootstrap Record",
        authors=["ScholarAgent Team"],
        abstract=(
            "This bootstrap record verifies that MySQL-backed knowledge storage, "
            "tenant isolation, RAG chunk indexing, and citation audit retrieval are available."
        ),
        full_text=(
            "ScholarAgent stores every paper, chunk, task, event, audit, reflection log, "
            "and trace with tenant_id and user_id. This record should be searchable through "
            "the RAG API after database bootstrap."
        ),
        published_at="2026-01-01",
        metadata={"seed": True, "bootstrap": "mysql"},
    )
    saved = await knowledge_store.save_paper(paper)
    stats = await rag_service.stats("tenant_demo", "user_demo")
    search = await rag_service.search("tenant_demo", "user_demo", "bootstrap", 5)
    return {"paper_id": saved["paper_id"], "stats": stats, "search_count": len(search["items"])}


def _table_counts() -> dict[str, int]:
    rows = mysql_store.fetch_all(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = %s AND table_name LIKE %s
        ORDER BY table_name
        """,
        (mysql_store.configured_database_name(), "scholar_%"),
    )
    return {"scholar_table_count": len(rows)}


async def main() -> int:
    try:
        bootstrap = ensure_database_and_user()
        schema = mysql_store.initialize_database(create_database=False)
        profile = auth_service.login("demo", "demo123", "tenant_demo")
        rag = await _seed_rag()
        result = {
            "status": "ok",
            "bootstrap": bootstrap,
            "schema": schema,
            "tables": _table_counts(),
            "login": {
                "tenant_id": profile["tenant_id"],
                "user_id": profile["user_id"],
                "api_key": profile["access_token"],
            },
            "rag": rag,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
