from __future__ import annotations

from typing import Any, AsyncIterator


async def run_survey_workflow(initial_state: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
    """Run the planner-authored LangGraph writing lifecycle and expose SSE events."""
    from skills.survey_generation.subgraph import survey_subgraph

    async for event in survey_subgraph.astream(dict(initial_state), stream_mode="custom"):
        yield event
