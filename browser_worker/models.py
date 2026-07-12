from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class BrowserSession:
    session_id: str
    tenant_id: str
    user_id: str
    login_url: str
    context: Any
    page: Any
    profile_dir: Path
    download_dir: Path
    status: str = "awaiting_user_login"
    search_results: list[dict[str, Any]] = field(default_factory=list)
    operation_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    created_at: float = field(default_factory=time.time)
    last_activity_at: float = field(default_factory=time.time)
    last_error: str = ""
