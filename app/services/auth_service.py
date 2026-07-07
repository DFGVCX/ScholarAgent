from __future__ import annotations

import hmac
import os
from dataclasses import asdict, dataclass
from typing import Any

from app.config import get_settings
from app.schemas import UserContext
from app.services import mysql_store


class AuthError(Exception):
    """Raised when local credentials or API keys are invalid."""


@dataclass(frozen=True)
class LocalAccount:
    username: str
    password: str
    tenant_id: str
    tenant_name: str
    user_id: str
    display_name: str
    api_key: str
    roles: tuple[str, ...]

    def public_profile(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("password", None)
        data["roles"] = list(self.roles)
        data["access_token"] = self.api_key
        data["token_type"] = "api-key"
        return data

    def to_context(self) -> UserContext:
        return UserContext(
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            api_key_label=f"{self.api_key[:4]}***",
        )


DEFAULT_ACCOUNTS: tuple[LocalAccount, ...] = (
    LocalAccount(
        username="demo",
        password="demo123",
        tenant_id="tenant_demo",
        tenant_name="Scholar Demo Lab",
        user_id="user_demo",
        display_name="Demo Researcher",
        api_key="demo-key",
        roles=("tenant_admin", "researcher"),
    ),
    LocalAccount(
        username="acme",
        password="acme123",
        tenant_id="tenant_acme",
        tenant_name="Acme AI Research",
        user_id="user_acme",
        display_name="Acme Analyst",
        api_key="acme-key",
        roles=("researcher",),
    ),
)


def _parse_api_key_mapping(raw: str) -> dict[str, UserContext]:
    mapping: dict[str, UserContext] = {}
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        parts = item.split(":")
        if len(parts) != 3:
            continue
        api_key, tenant_id, user_id = parts
        mapping[api_key] = UserContext(
            tenant_id=tenant_id,
            user_id=user_id,
            api_key_label=f"{api_key[:4]}***",
        )
    return mapping


class AuthService:
    """Local auth adapter that can be replaced by SSO/JWT without touching routes."""

    def __init__(self, accounts: tuple[LocalAccount, ...] = DEFAULT_ACCOUNTS) -> None:
        self._accounts = accounts

    def _local_accounts(self) -> tuple[LocalAccount, ...]:
        # Keep demos deterministic, while allowing a deployment to disable them.
        if os.getenv("SCHOLAR_DISABLE_DEMO_USERS", "").lower() in {"1", "true", "yes"}:
            return ()
        return self._accounts

    def login(self, username: str, password: str, tenant_id: str | None = None) -> dict[str, Any]:
        username = username.strip()
        tenant_id = tenant_id.strip() if tenant_id else None
        db_profile = self._login_from_mysql(username, password, tenant_id)
        if db_profile is not None:
            return db_profile
        for account in self._local_accounts():
            if tenant_id and account.tenant_id != tenant_id:
                continue
            if account.username != username:
                continue
            if not hmac.compare_digest(account.password, password):
                break
            return account.public_profile()
        raise AuthError("Invalid username, password, or tenant")

    def authenticate_api_key(self, api_key: str | None) -> UserContext:
        if not api_key:
            raise AuthError("API key is required")
        db_user = self._authenticate_api_key_from_mysql(api_key)
        if db_user is not None:
            return db_user
        for account in self._local_accounts():
            if hmac.compare_digest(account.api_key, api_key):
                return account.to_context()
        user = _parse_api_key_mapping(get_settings().api_keys).get(api_key)
        if user is None:
            raise AuthError("Invalid API key")
        return user

    def profile_for(self, user: UserContext) -> dict[str, Any]:
        db_profile = self._profile_from_mysql(user)
        if db_profile is not None:
            return db_profile
        for account in self._local_accounts():
            if account.tenant_id == user.tenant_id and account.user_id == user.user_id:
                profile = account.public_profile()
                profile.pop("access_token", None)
                profile.pop("token_type", None)
                return profile
        return {
            "tenant_id": user.tenant_id,
            "tenant_name": user.tenant_id,
            "user_id": user.user_id,
            "display_name": user.user_id,
            "roles": ["api_user"],
            "api_key": user.api_key_label,
        }

    def _login_from_mysql(
        self,
        username: str,
        password: str,
        tenant_id: str | None,
    ) -> dict[str, Any] | None:
        if not mysql_store.is_available():
            return None
        params: tuple[Any, ...]
        tenant_filter = ""
        if tenant_id:
            tenant_filter = "AND u.tenant_id = %s"
            params = (username, tenant_id)
        else:
            params = (username,)
        row = mysql_store.fetch_one(
            f"""
            SELECT u.*, t.name AS tenant_name
            FROM scholar_users u
            JOIN scholar_tenants t ON t.tenant_id = u.tenant_id
            WHERE u.username = %s
              {tenant_filter}
              AND u.status = 'active'
              AND t.status = 'active'
            LIMIT 1
            """,
            params,
        )
        if row is None:
            return None
        if not hmac.compare_digest(row["password_hash"], mysql_store.password_hash(password)):
            raise AuthError("Invalid username, password, or tenant")
        return self._row_to_profile(row, include_token=True)

    def _authenticate_api_key_from_mysql(self, api_key: str) -> UserContext | None:
        if not mysql_store.is_available():
            return None
        row = mysql_store.fetch_one(
            """
            SELECT user_id, tenant_id, api_key
            FROM scholar_users
            WHERE api_key = %s AND status = 'active'
            LIMIT 1
            """,
            (api_key,),
        )
        if row is None:
            return None
        return UserContext(
            tenant_id=row["tenant_id"],
            user_id=row["user_id"],
            api_key_label=f"{row['api_key'][:4]}***",
        )

    def _profile_from_mysql(self, user: UserContext) -> dict[str, Any] | None:
        if not mysql_store.is_available():
            return None
        row = mysql_store.fetch_one(
            """
            SELECT u.*, t.name AS tenant_name
            FROM scholar_users u
            JOIN scholar_tenants t ON t.tenant_id = u.tenant_id
            WHERE u.tenant_id = %s AND u.user_id = %s AND u.status = 'active'
            LIMIT 1
            """,
            (user.tenant_id, user.user_id),
        )
        if row is None:
            return None
        profile = self._row_to_profile(row, include_token=False)
        profile["api_key"] = user.api_key_label
        return profile

    def _row_to_profile(self, row: dict[str, Any], include_token: bool) -> dict[str, Any]:
        profile = {
            "username": row["username"],
            "tenant_id": row["tenant_id"],
            "tenant_name": row["tenant_name"],
            "user_id": row["user_id"],
            "display_name": row["display_name"],
            "api_key": row["api_key"],
            "roles": mysql_store.decode_json(row.get("roles_json"), []),
        }
        if include_token:
            profile["access_token"] = row["api_key"]
            profile["token_type"] = "api-key"
        return profile


auth_service = AuthService()
