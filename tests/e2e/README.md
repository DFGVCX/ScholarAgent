# ScholarAgent E2E Test Plan

This directory follows the copied ADP `e2e-test-infrastructure-setup` skill.

The intended production E2E flow is:

1. Open `http://localhost`.
2. Enter `demo-key`.
3. Create an arXiv survey task.
4. Observe SSE progress events.
5. Verify outline appears before final result.
6. Verify final markdown and citation audit PASS.
7. Repeat with DOI and PDF fallback input.

The Playwright template can be copied from:

```text
.agents/skills/e2e-test-infrastructure-setup/template/
```

It is not installed by default to avoid adding Node dependencies to the Python
MVP path.

