# ScholarAgent Frontend

`frontend/` is the web console boundary. The active deployed page is `frontend/dist/app.html`, mounted by the FastAPI backend at `/app.html` and served by Nginx in Docker.

The archived root-level demo was moved to `archive/legacy-root-index.html` and must not be treated as the active frontend.

## Current Structure

```text
frontend/
├── dist/                  # Current deployable static console
├── src/                   # Future typed Vite/React source layout
│   ├── api/
│   ├── hooks/
│   ├── pages/
│   ├── styles/
│   └── types/
├── index.html
├── package.json
└── vite.config.ts
```

Rules:

- API calls belong in `src/api/` for typed frontend work.
- Server state belongs in hooks such as `src/hooks/useTaskStream.ts`.
- Domain adapters live under page/domain folders such as `src/pages/survey-console/`.
- While `dist/app.html` remains the active zero-build console, keep request helpers and route state centralized inside that file.
- Do not add backend logic or runtime data under `frontend/`.

---

# Historical Note

The archived `archive/legacy-root-index.html` is the legacy zero-build demo. `frontend/dist/app.html`
is the active zero-build company console used by FastAPI/Docker/Nginx, while
`frontend/src/**` carries the Vite/React-style source layout expected for a
future typed frontend implementation.

## Structure

```text
frontend/src/
├── api/
├── hooks/
├── pages/survey-console/
├── components/
├── types/
└── styles/
```

The rules are adapted from ADP:

- API calls live in `api/`.
- Server state is accessed through hooks.
- View defaults and field compatibility live in adapters.
- Page components handle loading, empty, error, disabled, and permission states.
