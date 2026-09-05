from __future__ import annotations

import uvicorn

from app.asyncio_compat import configure_psycopg_event_loop_policy


def main() -> None:
    configure_psycopg_event_loop_policy()
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    main()
