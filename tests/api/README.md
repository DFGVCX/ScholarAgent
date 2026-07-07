# ScholarAgent API Test Plan

FastAPI route tests should be enabled after installing `requirements.txt`.

Minimum API checks:

1. `GET /health` returns ok.
2. `POST /tasks/survey` requires `X-API-Key`.
3. Valid `demo-key` creates a task and returns `task_id`.
4. `GET /tasks/{task_id}/stream?api_key=demo-key` emits SSE events.
5. `GET /tasks/{task_id}/result` enforces tenant/user ownership.

