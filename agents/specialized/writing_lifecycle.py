from __future__ import annotations

import json
import re
from typing import Any, Awaitable, Callable

from agents.factory import model_factory
from agents.task_graph import TaskGraphPlan, TaskNode
from app.services.node_run_store import node_run_store
from app.services.outline_approval import outline_approval_registry
from mcp_server.scholar_mcp.client import ScholarMCPClient
from skills.survey_generation.tools.citation import CitationGuard
from skills.survey_generation.tools.evaluator_tool import SurveyEvaluator
from skills.survey_generation.tools.processor import LiteratureProcessor
from skills.survey_generation.tools.synthesizer import OutlineSynthesizer


EventWriter = Callable[[dict[str, Any]], None]


def _context(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": state.get("task_id"),
        "tenant_id": state.get("tenant_id"),
        "user_id": state.get("user_id"),
        "trace_id": state.get("trace_id"),
        "topic": state.get("topic"),
    }


def _snapshot_ref(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        key: snapshot.get(key)
        for key in ("run_id", "version", "input_fingerprint")
        if snapshot.get(key) is not None
    }


def _parse_outline(markdown: str, papers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    titles: list[str] = []
    for raw in markdown.splitlines():
        match = re.match(r"^#{2,6}\s+(?:\d+[.)、]?\s*)?(.+)$", raw.strip())
        if match:
            titles.append(match.group(1).strip())
    if not titles:
        return []
    paper_ids = [str(paper["paper_id"]) for paper in papers]
    return [
        {
            "section_id": f"section_{index}",
            "title": title,
            "paper_ids": paper_ids[(index - 1) :: max(1, len(titles))][:5] or paper_ids[:3],
        }
        for index, title in enumerate(titles, start=1)
    ]


class RetrievalSkillAgent:
    capability = "literature_retrieval"

    async def execute(self, state: dict[str, Any], node: TaskNode) -> tuple[dict[str, Any], dict[str, Any]]:
        client = ScholarMCPClient()
        strategy = str(state.get("retrieval_strategy") or "online").lower()
        source = {"online": "external", "local": "local", "hybrid": "all"}.get(strategy, "external")
        limit = max(1, int(state.get("max_papers") or 12))
        seed: dict[str, Any] | None = None
        seed_error = ""
        input_value = str(state.get("input_value") or "").strip()
        if input_value:
            try:
                result = await client.call_tool(
                    "ingest_paper",
                    {
                        "tenant_id": state["tenant_id"],
                        "user_id": state["user_id"],
                        "task_id": state["task_id"],
                        "input_type": state.get("input_type", "url"),
                        "input_value": input_value,
                        "topic": state["topic"],
                    },
                )
                seed = result.get("paper")
            except Exception as exc:
                seed_error = str(exc)
        search = await client.call_tool(
            "search_papers",
            {
                "tenant_id": state["tenant_id"],
                "user_id": state["user_id"],
                "query": state["topic"],
                "source": source,
                "limit": limit,
            },
        )
        candidates = [*([seed] if seed else []), *(search.get("items") or [])]
        papers = list({paper["paper_id"]: paper for paper in candidates if paper}.values())[:limit]
        if not papers:
            detail = search.get("external_error") or seed_error or "no matching papers"
            raise RuntimeError(f"No literature was available for this writing task: {detail}")
        chunks = LiteratureProcessor().chunk_literature(papers)
        output = {"papers": papers, "chunks": chunks, "retrieval_external_error": search.get("external_error")}
        return output, {"passed": True, "paper_count": len(papers), "chunk_count": len(chunks)}


class OutlineSkillAgent:
    capability = "outline_generation"

    async def execute(self, state: dict[str, Any], node: TaskNode) -> tuple[dict[str, Any], dict[str, Any]]:
        papers = list(state.get("papers") or [])
        chunks = list(state.get("chunks") or [])
        response = await model_factory.generate_text(
            "outline",
            json.dumps(
                {
                    "goal": state["topic"],
                    "instruction": node.instruction,
                    "sources": [
                        {"paper_id": paper.get("paper_id"), "title": paper.get("title")}
                        for paper in papers[:20]
                    ],
                    "history": state.get("memory_context") or state.get("historical_preferences") or {},
                },
                ensure_ascii=False,
            ),
            _context(state),
        )
        outline_markdown = response.content.strip()
        outline = _parse_outline(outline_markdown, papers)
        if not outline:
            synthesizer = OutlineSynthesizer()
            outline = synthesizer.synthesize(str(state["topic"]), chunks)
            outline_markdown = synthesizer.to_markdown(outline, str(state["topic"]))
        payload = {"outline": outline, "outline_markdown": outline_markdown}
        return payload, {"passed": bool(outline), "section_count": len(outline)}


class SectionWritingSkillAgent:
    capability = "section_writing"

    async def execute(self, state: dict[str, Any], node: TaskNode) -> tuple[dict[str, Any], dict[str, Any]]:
        papers = list(state.get("papers") or [])
        outline = list(state.get("outline") or [])
        snapshots = dict(state.get("node_snapshots") or {})
        sections: list[dict[str, Any]] = []
        reviews: list[dict[str, Any]] = []
        guard = CitationGuard()
        evaluator = SurveyEvaluator()
        for section in outline:
            section_id = str(section.get("section_id") or f"section_{len(sections) + 1}")
            record_id = f"section:{section_id}"
            citation_ids = list(section.get("paper_ids") or []) or [papers[0]["paper_id"]]
            payload = {
                "topic": state["topic"],
                "section": section,
                "instruction": node.instruction,
                "source_ids": citation_ids,
                "history": state.get("memory_context") or {},
            }
            if str(state.get("retry_target") or "") == record_id:
                payload["retry_feedback"] = (state.get("retry_history") or [{}])[-1]
            dependencies = {
                "outline": _snapshot_ref(snapshots.get(state.get("outline_node_id", "outline"), {})),
                "sources": citation_ids,
            }
            fingerprint = node_run_store.fingerprint(payload, dependencies)
            cached = node_run_store.latest_completed(
                tenant_id=state["tenant_id"], user_id=state["user_id"], task_id=state["task_id"],
                node_id=record_id, fingerprint=fingerprint,
            )
            if cached:
                section_output = cached.output
            else:
                run_id = node_run_store.start(
                    tenant_id=state["tenant_id"], user_id=state["user_id"], task_id=state["task_id"],
                    node_id=record_id, capability="section_writing", version=node.version,
                    fingerprint=fingerprint, payload=payload, dependencies=dependencies,
                )
                try:
                    response = await model_factory.generate_text(
                        "section",
                        json.dumps(payload, ensure_ascii=False),
                        _context(state) | {
                            "section_title": section.get("title"),
                            "citation_id": citation_ids[0],
                        },
                    )
                    generated = {
                        "section_id": section_id,
                        "title": section.get("title", section_id),
                        "content": response.content,
                    }
                    audit = guard.verify_citations(response.content, papers)
                    review = evaluator.evaluate_section(generated, audit)
                    section_output = {"section": generated, "review": review, "citation_audit": audit}
                    node_run_store.complete(run_id, section_output, review)
                except Exception as exc:
                    node_run_store.fail(run_id, str(exc))
                    raise
            sections.append(dict(section_output["section"]))
            reviews.append({"section_id": section_id, **dict(section_output.get("review") or {})})
        quality = {"passed": all(item.get("passed") for item in reviews), "section_reviews": reviews}
        return {"sections": sections, "section_reviews": reviews}, quality


class QualitySkillAgent:
    capability = "quality_review"

    async def execute(self, state: dict[str, Any], node: TaskNode) -> tuple[dict[str, Any], dict[str, Any]]:
        papers = list(state.get("papers") or [])
        outline = list(state.get("outline") or [])
        sections = list(state.get("sections") or [])
        reviews = list(state.get("section_reviews") or [])
        combined = "\n\n".join(str(section.get("content") or "") for section in sections)
        citation_audit = CitationGuard().verify_citations(combined, papers)
        retry_target = ""
        findings: list[str] = []
        if not papers:
            retry_target = "retrieval"
            findings.append("No evidence pool is available")
        elif not outline:
            retry_target = "outline"
            findings.append("The outline is empty")
        else:
            failed = next((item for item in reviews if not item.get("passed")), None)
            if failed:
                retry_target = f"section:{failed['section_id']}"
                findings.extend(str(item) for item in failed.get("findings") or ["Section quality failed"])
            elif not citation_audit.get("is_valid"):
                bad_id = next(iter(citation_audit.get("hallucinated_ids") or []), "")
                owner = next(
                    (section for section in sections if bad_id and bad_id in str(section.get("content") or "")),
                    sections[0] if sections else {},
                )
                retry_target = f"section:{owner.get('section_id', 'section_1')}"
                findings.append("A section contains an unsupported source ID")
        decision = {
            "passed": not retry_target,
            "retry_target": retry_target,
            "findings": findings,
            "citation_audit": citation_audit,
        }
        retry_count = int(state.get("quality_retry_count") or 0) + (0 if decision["passed"] else 1)
        retry_history = list(state.get("retry_history") or [])
        if retry_target:
            retry_history.append({
                "attempt": retry_count,
                "retry_target": retry_target,
                "findings": findings,
            })
        return {
            "quality_decision": decision,
            "citation_audit": citation_audit,
            "retry_target": retry_target,
            "quality_retry_count": retry_count,
            "retry_history": retry_history,
        }, decision


CAPABILITY_EXECUTORS = {
    "literature_retrieval": RetrievalSkillAgent(),
    "outline_generation": OutlineSkillAgent(),
    "section_writing": SectionWritingSkillAgent(),
    "quality_review": QualitySkillAgent(),
}


class LifecycleNodeRunner:
    def __init__(self, plan: TaskGraphPlan, writer: EventWriter) -> None:
        self.plan = plan
        self.writer = writer

    async def run(self, node: TaskNode, state: dict[str, Any]) -> dict[str, Any]:
        snapshots = dict(state.get("node_snapshots") or {})
        dependencies = {
            dependency: _snapshot_ref(snapshots.get(dependency, {}))
            for dependency in node.depends_on
        }
        payload = self._node_input(node, state)
        fingerprint = node_run_store.fingerprint(payload, dependencies)
        cached = node_run_store.latest_completed(
            tenant_id=state["tenant_id"], user_id=state["user_id"], task_id=state["task_id"],
            node_id=node.node_id, fingerprint=fingerprint,
        )
        if cached:
            cached_output = dict(cached.output)
            if node.capability == "outline_generation":
                cached_output = await self._publish_outline(state, cached_output, reused=True)
            snapshots[node.node_id] = {
                "run_id": cached.run_id, "version": cached.version,
                "input_fingerprint": cached.input_fingerprint, "status": "reused",
            }
            self.writer({
                "event": "progress", "phase": node.node_id,
                "message": f"Reused cached output for {node.capability}", "percent": self._percent(node),
                "payload": {
                    "node_id": node.node_id,
                    "capability": node.capability,
                    "agent_name": node.agent_name,
                    "version": node.version,
                    "status": "reused",
                    "cache_reused": True,
                    "quality": cached.quality,
                },
            })
            patch = cached_output | {"node_snapshots": snapshots}
            if node.capability == "outline_generation":
                patch["outline_node_id"] = node.node_id
            return patch
        run_id = node_run_store.start(
            tenant_id=state["tenant_id"], user_id=state["user_id"], task_id=state["task_id"],
            node_id=node.node_id, capability=node.capability, version=node.version,
            fingerprint=fingerprint, payload=payload, dependencies=dependencies,
        )
        self.writer({
            "event": "progress", "phase": node.node_id,
            "message": f"Running {node.agent_name}", "percent": self._percent(node),
            "payload": {
                "node_id": node.node_id,
                "capability": node.capability,
                "agent_name": node.agent_name,
                "version": node.version,
                "status": "running",
            },
        })
        try:
            output, quality = await CAPABILITY_EXECUTORS[node.capability].execute(state, node)
            if node.capability == "outline_generation":
                output = await self._publish_outline(state, output, reused=False)
            node_run_store.complete(run_id, output, quality)
        except Exception as exc:
            node_run_store.fail(run_id, str(exc))
            raise
        snapshots[node.node_id] = {
            "run_id": run_id, "version": node.version,
            "input_fingerprint": fingerprint, "status": "completed", "quality": quality,
        }
        self.writer({
            "event": "progress",
            "phase": node.node_id,
            "message": f"Completed {node.agent_name}",
            "percent": self._percent(node) + 8,
            "payload": {
                "node_id": node.node_id,
                "capability": node.capability,
                "agent_name": node.agent_name,
                "version": node.version,
                "status": "completed",
                "quality": quality,
                "retry_target": quality.get("retry_target", ""),
            },
        })
        patch = dict(output) | {"node_snapshots": snapshots}
        if node.capability == "outline_generation":
            patch["outline_node_id"] = node.node_id
        return patch

    async def _publish_outline(
        self, state: dict[str, Any], output: dict[str, Any], *, reused: bool
    ) -> dict[str, Any]:
        payload = dict(output)
        requires_confirmation = bool(state.get("require_outline_confirmation"))
        if requires_confirmation:
            outline_approval_registry.open(str(state["task_id"]), payload)
        self.writer({
            "event": "outline_required",
            "phase": "outline",
            "message": "Outline is ready for confirmation" if requires_confirmation else "Outline generated",
            "percent": 42,
            "payload": payload | {
                "requires_confirmation": requires_confirmation,
                "cache_reused": reused,
            },
        })
        if not requires_confirmation:
            return payload
        decision = await outline_approval_registry.wait(str(state["task_id"]))
        if not decision.approved:
            raise RuntimeError("Outline confirmation was rejected")
        if decision.outline_markdown.strip():
            markdown = decision.outline_markdown.strip()
            payload["outline_markdown"] = markdown
            payload["outline"] = _parse_outline(markdown, list(state.get("papers") or [])) or payload["outline"]
        return payload

    def _node_input(self, node: TaskNode, state: dict[str, Any]) -> dict[str, Any]:
        common = {
            "goal": state.get("topic"), "instruction": node.instruction,
            "version": node.version,
        }
        retry_target = str(state.get("retry_target") or "")
        by_capability = {
            "literature_retrieval": {
                "strategy": state.get("retrieval_strategy"), "constraints": state.get("retrieval_constraints"),
                "max_papers": state.get("max_papers"), "input_value": state.get("input_value"),
            },
            "outline_generation": {"papers": state.get("papers"), "memory": state.get("memory_context")},
            "section_writing": {"outline": state.get("outline"), "papers": state.get("papers")},
            "quality_review": {"sections": state.get("sections"), "papers": state.get("papers")},
        }
        payload = common | by_capability[node.capability]
        target_capability = {
            "retrieval": "literature_retrieval",
            "outline": "outline_generation",
            "section_writing": "section_writing",
            "quality": "quality_review",
        }.get(retry_target, "section_writing" if retry_target.startswith("section:") else "")
        if target_capability == node.capability:
            payload["retry_target"] = retry_target
            payload["retry_feedback"] = (state.get("retry_history") or [{}])[-1]
        return payload

    def _percent(self, node: TaskNode) -> int:
        order = [item.node_id for item in self.plan.nodes]
        return 12 + (order.index(node.node_id) + 1) * 18
