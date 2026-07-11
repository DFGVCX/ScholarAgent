from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.schemas import UserContext
from app.services import mysql_store


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decode(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    result = dict(row)
    if "authenticated_domains_json" in result:
        result["authenticated_domains"] = mysql_store.decode_json(
            result.pop("authenticated_domains_json"), []
        )
    result["enabled"] = bool(result.get("enabled", True))
    return result


class InstitutionalAccessStore:
    def initialize(self) -> None:
        mysql_store.initialize_database()

    def save_profile(
        self,
        user: UserContext,
        *,
        institution_name: str,
        access_type: str,
        login_url: str,
        proxy_prefix: str = "",
        profile_id: str = "",
    ) -> dict[str, Any]:
        self.initialize()
        profile_id = profile_id or f"inst_profile_{uuid4().hex}"
        mysql_store.execute(
            """INSERT INTO scholar_institution_profiles
            (profile_id, tenant_id, user_id, institution_name, access_type, login_url,
             proxy_prefix, enabled, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT(profile_id) DO UPDATE SET institution_name=excluded.institution_name,
            access_type=excluded.access_type, login_url=excluded.login_url,
            proxy_prefix=excluded.proxy_prefix, enabled=1, updated_at=excluded.updated_at""",
            (
                profile_id,
                user.tenant_id,
                user.user_id,
                institution_name,
                access_type,
                login_url,
                proxy_prefix,
                _now(),
                _now(),
            ),
        )
        return self.get_profile(user, profile_id) or {}

    def list_profiles(self, user: UserContext) -> list[dict[str, Any]]:
        self.initialize()
        return [
            _decode(row) or {}
            for row in mysql_store.fetch_all(
                "SELECT * FROM scholar_institution_profiles WHERE tenant_id=? AND user_id=? "
                "AND enabled=1 ORDER BY updated_at DESC",
                (user.tenant_id, user.user_id),
            )
        ]

    def get_profile(self, user: UserContext, profile_id: str) -> dict[str, Any] | None:
        self.initialize()
        return _decode(
            mysql_store.fetch_one(
                "SELECT * FROM scholar_institution_profiles WHERE profile_id=? "
                "AND tenant_id=? AND user_id=?",
                (profile_id, user.tenant_id, user.user_id),
            )
        )

    def create_session(self, user: UserContext, profile_id: str) -> dict[str, Any]:
        self.initialize()
        session_id = f"inst_session_{uuid4().hex}"
        mysql_store.execute(
            """INSERT INTO scholar_institution_sessions
            (session_id, profile_id, tenant_id, user_id, status,
             authenticated_domains_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'awaiting_user_login', '[]', ?, ?)""",
            (session_id, profile_id, user.tenant_id, user.user_id, _now(), _now()),
        )
        return self.get_session(user, session_id) or {}

    def get_session(self, user: UserContext, session_id: str) -> dict[str, Any] | None:
        self.initialize()
        return _decode(
            mysql_store.fetch_one(
                "SELECT * FROM scholar_institution_sessions WHERE session_id=? "
                "AND tenant_id=? AND user_id=?",
                (session_id, user.tenant_id, user.user_id),
            )
        )

    def latest_session(self, user: UserContext) -> dict[str, Any] | None:
        self.initialize()
        return _decode(
            mysql_store.fetch_one(
                "SELECT * FROM scholar_institution_sessions WHERE tenant_id=? AND user_id=? "
                "ORDER BY updated_at DESC LIMIT 1",
                (user.tenant_id, user.user_id),
            )
        )

    def update_session(self, user: UserContext, session_id: str, **values: Any) -> dict[str, Any] | None:
        allowed = {"status", "authenticated_domains_json", "verified_at", "expires_at", "revoked_at", "last_error"}
        updates = {key: value for key, value in values.items() if key in allowed}
        if "authenticated_domains" in values:
            updates["authenticated_domains_json"] = mysql_store.encode_json(values["authenticated_domains"])
        if not updates:
            return self.get_session(user, session_id)
        assignments = ", ".join(f"{key}=?" for key in updates)
        mysql_store.execute(
            f"UPDATE scholar_institution_sessions SET {assignments}, updated_at=? "
            "WHERE session_id=? AND tenant_id=? AND user_id=?",
            (*updates.values(), _now(), session_id, user.tenant_id, user.user_id),
        )
        return self.get_session(user, session_id)

    def create_download(
        self,
        user: UserContext,
        *,
        session_id: str,
        source: str,
        source_url: str,
        title: str,
        doi: str,
        conversation_id: str = "",
    ) -> dict[str, Any]:
        self.initialize()
        download_id = f"inst_download_{uuid4().hex}"
        mysql_store.execute(
            """INSERT INTO scholar_institution_downloads
            (download_id, session_id, tenant_id, user_id, conversation_id, source,
             source_url, title, doi, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'awaiting_confirmation', ?)""",
            (
                download_id, session_id, user.tenant_id, user.user_id, conversation_id,
                source, source_url, title, doi, _now(),
            ),
        )
        return self.get_download(user, download_id) or {}

    def get_download(self, user: UserContext, download_id: str) -> dict[str, Any] | None:
        self.initialize()
        return _decode(
            mysql_store.fetch_one(
                "SELECT * FROM scholar_institution_downloads WHERE download_id=? "
                "AND tenant_id=? AND user_id=?",
                (download_id, user.tenant_id, user.user_id),
            )
        )

    def update_download(self, user: UserContext, download_id: str, **values: Any) -> dict[str, Any] | None:
        allowed = {
            "status", "file_type", "file_path", "file_sha256", "file_size", "paper_id",
            "failure_code", "failure_message", "completed_at",
        }
        updates = {key: value for key, value in values.items() if key in allowed}
        if not updates:
            return self.get_download(user, download_id)
        assignments = ", ".join(f"{key}=?" for key in updates)
        mysql_store.execute(
            f"UPDATE scholar_institution_downloads SET {assignments} "
            "WHERE download_id=? AND tenant_id=? AND user_id=?",
            (*updates.values(), download_id, user.tenant_id, user.user_id),
        )
        return self.get_download(user, download_id)


institutional_access_store = InstitutionalAccessStore()
