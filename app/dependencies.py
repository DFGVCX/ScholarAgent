from __future__ import annotations

from app.schemas import UserContext
from app.services.auth_service import AuthError, auth_service


def authenticate_api_key(api_key: str | None) -> UserContext:
    return auth_service.authenticate_api_key(api_key)
