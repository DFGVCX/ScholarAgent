from __future__ import annotations

import asyncio
import sys
import warnings


def configure_psycopg_event_loop_policy() -> None:
    """Select an event loop supported by psycopg async connections on Windows."""
    if sys.platform != "win32":
        return
    selector_policy_type = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
    if selector_policy_type is None:
        return
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        current_policy = asyncio.get_event_loop_policy()
        if not isinstance(current_policy, selector_policy_type):
            asyncio.set_event_loop_policy(selector_policy_type())
