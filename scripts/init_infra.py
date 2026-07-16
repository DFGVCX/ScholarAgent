from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings
from app.services import mysql_store
from app.services.auth_service import auth_service
from app.services.rag_service import rag_service
from mcp_server.scholar_mcp.models import PaperRecord
from mcp_server.scholar_mcp.store import knowledge_store


def _safe_postgres_url() -> str:
    parsed = urlparse(get_settings().database_url)
    user = parsed.username or "scholar"
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 5432
    database = parsed.path.lstrip("/") or mysql_store.configured_database_name()
    return f"postgresql://{user}:***@{host}:{port}/{database}"


def _redis_status() -> dict[str, object]:
    try:
        import redis

        client = redis.Redis.from_url(
            get_settings().redis_url,
            socket_connect_timeout=0.4,
            socket_timeout=0.4,
            decode_responses=True,
        )
        pong = client.ping()
        client.set("scholar:init:ping", "ok", ex=60)
        return {"url": get_settings().redis_url, "available": bool(pong)}
    except Exception as exc:
        return {"url": get_settings().redis_url, "available": False, "error": str(exc)}


async def _seed_rag() -> dict[str, object]:
    paper = PaperRecord(
        paper_id="paper:manual:rag-demo",
        tenant_id="tenant_demo",
        user_id="user_demo",
        source="manual",
        title="RAG Knowledge Base for Source-Grounded Survey Generation",
        authors=["ScholarAgent Team"],
        abstract=(
            "This seed paper describes a tenant-scoped retrieval augmented generation "
            "knowledge base. It stores normalized papers, searchable chunks, and keywords "
            "for citation-grounded academic writing."
        ),
        full_text=(
            "ScholarAgent RAG indexes paper titles, abstracts, and full text into chunks. "
            "Each chunk is isolated by tenant_id and user_id, so retrieval cannot leak "
            "knowledge across tenants. The retrieval result is used by review generation "
            "and citation audit workflows."
        ),
        published_at="2026-01-01",
        metadata={"seed": True, "module": "rag"},
    )
    saved = await knowledge_store.save_paper(paper)
    stats = await rag_service.stats("tenant_demo", "user_demo")
    search = await rag_service.search("tenant_demo", "user_demo", "citation audit retrieval", 5)
    return {"paper": saved, "stats": stats, "search": search}


async def main() -> int:
    try:
        postgres_result = mysql_store.initialize_database()
        mysql_store.seed_demo_data()
    except Exception as exc:
        print(
            json.dumps(
                {
                    "postgresql": {
                        "url": _safe_postgres_url(),
                        "available": False,
                        "error": str(exc),
                    },
                    "hint": (
                        "Set SCHOLAR_DATABASE_URL and run `python -m alembic upgrade head` first"
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    login_profile = auth_service.login("demo", "demo123", "tenant_demo")
    rag_result = await _seed_rag()
    result = {
        "postgresql": {
            "url": _safe_postgres_url(),
            "available": True,
            **postgres_result,
        },
        "redis": _redis_status(),
        "login": {
            "username": login_profile["username"],
            "tenant_id": login_profile["tenant_id"],
            "user_id": login_profile["user_id"],
            "access_token": login_profile["access_token"],
        },
        "rag": rag_result,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
