from __future__ import annotations

import re
from typing import Any, AsyncIterator

from agents.factory import model_factory
from app.services.outline_approval import outline_approval_registry
from mcp_server.scholar_mcp.client import ScholarMCPClient
from skills.survey_generation.tools.citation import CitationGuard
from skills.survey_generation.tools.evaluator_tool import SurveyEvaluator
from skills.survey_generation.tools.formatter import CitationFormatter
from skills.survey_generation.tools.processor import LiteratureProcessor
from skills.survey_generation.tools.refiner import LCERefiner
from skills.survey_generation.tools.synthesizer import OutlineSynthesizer


def _progress(phase: str, message: str, percent: int, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "event": "progress",
        "phase": phase,
        "message": message,
        "percent": percent,
        "payload": payload or {},
    }


def _outline_from_markdown(markdown: str, original: list[dict[str, Any]]) -> list[dict[str, Any]]:
    titles: list[str] = []
    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        heading = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading:
            if heading.group(1) == "#":
                continue
            candidate = heading.group(2).strip()
        else:
            candidate = line
        numbered = re.match(r"^(?:\d+[\.\、\)]\s+)(.+)$", candidate)
        if not numbered and not heading:
            continue
        title = (numbered.group(1) if numbered else candidate).strip()
        if title and not title.lower().startswith("survey outline"):
            titles.append(title)
    if not titles:
        return original
    edited: list[dict[str, Any]] = []
    fallback = original[-1] if original else {"paper_ids": []}
    for index, title in enumerate(titles):
        base = original[index] if index < len(original) else fallback
        edited.append(
            {
                "section_id": base.get("section_id") or f"custom_{index + 1}",
                "title": title,
                "paper_ids": list(base.get("paper_ids") or []),
            }
        )
    return edited


async def run_survey_pipeline(initial_state: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
    tenant_id = initial_state["tenant_id"]
    user_id = initial_state["user_id"]
    topic = initial_state["topic"]
    task_id = initial_state["task_id"]
    input_type = initial_state.get("input_type", "arxiv")
    input_value = str(initial_state.get("input_value") or "").strip()
    retrieval_strategy = str(initial_state.get("retrieval_strategy") or "online").lower()
    retrieval_constraints = str(initial_state.get("retrieval_constraints") or "").strip()
    search_source = {"online": "external", "local": "local", "hybrid": "all"}.get(
        retrieval_strategy,
        "external",
    )
    citation_style = initial_state.get("citation_style", "IEEE")
    max_papers = int(initial_state.get("max_papers", 12))
    require_outline_confirmation = bool(initial_state.get("require_outline_confirmation", False))
    delegation_results = list(initial_state.get("delegation_results") or [])
    collaboration_plan = "\n\n".join(
        f"[{item.get('agent_name', 'subagent')}] {item.get('content', '')}"
        for item in delegation_results
        if item.get("status") == "succeeded" and item.get("content")
    )[:10000]
    trace_context = {
        "task_id": task_id,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "trace_id": initial_state.get("trace_id"),
        "execution_mode": "delegation" if delegation_results else "skill",
        "collaboration_plan": collaboration_plan,
        "retrieval_strategy": retrieval_strategy,
        "retrieval_constraints": retrieval_constraints,
    }

    client = ScholarMCPClient()
    primary_paper: dict[str, Any] | None = None
    ingest_error: str | None = None
    yield _progress("ingest_sources", "Preparing retrieval scope and optional seed paper", 8)
    if input_value:
        try:
            ingest = await client.call_tool(
                "ingest_paper",
                {
                    "tenant_id": tenant_id,
                    "user_id": user_id,
                    "task_id": task_id,
                    "input_type": input_type,
                    "input_value": input_value,
                    "topic": topic,
                },
            )
            primary_paper = ingest["paper"]
        except Exception as exc:
            ingest_error = str(exc)
            yield _progress(
                "ingest_sources",
                f"Seed paper lookup failed; continuing with the selected retrieval scope: {ingest_error}",
                10,
                {"external_error": ingest_error},
            )

    strategy_labels = {"online": "online sources", "local": "tenant knowledge base", "hybrid": "online sources and tenant knowledge base"}
    yield _progress("search_papers", f"Searching {strategy_labels.get(retrieval_strategy, 'online sources')}", 16)
    search = await client.call_tool(
        "search_papers",
        {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "query": topic,
            "source": search_source,
            "limit": max_papers,
        },
    )
    if search.get("external_error"):
        yield _progress(
            "search_papers",
            f"External source unavailable: {search['external_error']}",
            18,
            {"external_error": search["external_error"]},
        )
    if primary_paper is not None:
        primary_paper["can_cite"] = bool(primary_paper.get("full_text"))
    local_evidence = [
        paper for paper in search.get("local_hits", []) if paper.get("can_cite") is True
    ]
    acquisition_errors: list[str] = []
    if retrieval_strategy in {"online", "hybrid"}:
        slots = max(0, max_papers - len(local_evidence))
        for candidate in (search.get("external_candidates") or [])[:slots]:
            try:
                acquired = await client.call_tool(
                    "acquire_paper_to_knowledge",
                    {
                        "tenant_id": tenant_id,
                        "user_id": user_id,
                        "paper": candidate,
                    },
                )
                acquired_paper = dict(acquired.get("paper") or {})
                if acquired_paper.get("full_text"):
                    acquired_paper["can_cite"] = True
                    local_evidence.append(acquired_paper)
            except Exception as exc:
                acquisition_errors.append(
                    f"{candidate.get('paper_id') or candidate.get('title') or 'external candidate'}: {exc}"
                )
    papers = [
        *([primary_paper] if primary_paper and primary_paper.get("can_cite") else []),
        *local_evidence,
    ]
    deduped = {paper["paper_id"]: paper for paper in papers}
    papers = list(deduped.values())[:max_papers]
    if not papers:
        external_count = len(search.get("external_candidates") or [])
        source_error = (
            search.get("external_error")
            or ingest_error
            or (" | ".join(acquisition_errors) if acquisition_errors else "")
            or "no citeable local evidence returned"
        )
        if retrieval_strategy == "local":
            raise RuntimeError("本地知识库没有检索到可用于写作的文献，请调整研究主题或附加约束。")
        if retrieval_strategy == "online":
            raise RuntimeError(
                f"在线检索返回了 {external_count} 篇候选，但候选尚未下载、解析和入库，"
                "不能直接作为写作引用。请先获取全文后重试。"
                f"原始错误：{source_error}"
            )
        raise RuntimeError(
            "在线论文源与本地知识库均未返回可用于写作的文献。"
            f"请稍后重试或先向知识库添加论文。原始错误：{source_error}"
        )

    processor = LiteratureProcessor()
    chunks = processor.chunk_literature(papers)
    yield _progress(
        "chunk_literature",
        f"Chunked {len(papers)} papers into {len(chunks)} token-aware batch(es)",
        25,
        {"paper_count": len(papers), "chunk_count": len(chunks)},
    )

    synthesizer = OutlineSynthesizer()
    outline = synthesizer.synthesize(topic, chunks)
    outline_markdown = synthesizer.to_markdown(outline, topic)
    outline_payload = {
        "outline": outline,
        "outline_markdown": outline_markdown,
        "agent_collaboration": delegation_results,
    }
    if require_outline_confirmation:
        outline_approval_registry.open(task_id, outline_payload)
    yield {
        "event": "outline_required",
        "phase": "synthesize_outline",
        "message": "Outline synthesized; waiting for confirmation" if require_outline_confirmation else "Outline synthesized",
        "percent": 35,
        "payload": outline_payload | {"requires_confirmation": require_outline_confirmation},
    }
    if require_outline_confirmation:
        decision = await outline_approval_registry.wait(task_id)
        if not decision.approved:
            raise RuntimeError("Outline confirmation was not approved")
        if decision.outline_markdown.strip():
            outline_markdown = decision.outline_markdown.strip()
            outline = _outline_from_markdown(outline_markdown, outline)
        yield _progress(
            "outline_confirmed",
            "Outline confirmed; continuing section writing",
            38,
            {
                "comment": decision.comment,
                "outline_markdown": outline_markdown,
                "edited_outline": bool(decision.outline_markdown.strip()),
            },
        )

    sections: list[dict[str, Any]] = []
    guard = CitationGuard()
    evaluator = SurveyEvaluator()
    reflection_logs: list[dict[str, Any]] = []

    yield _progress("write_trial_section", "Writing trial section with source IDs", 45)
    for idx, section in enumerate(outline):
        citation_id = section["paper_ids"][0] if section["paper_ids"] else papers[0]["paper_id"]
        response = await model_factory.generate_text(
            "section",
            section["title"],
            trace_context | {"topic": topic, "section_title": section["title"], "citation_id": citation_id},
        )
        generated = {"section_id": section["section_id"], "title": section["title"], "content": response.content}
        audit = guard.verify_citations(response.content, papers)
        review = evaluator.evaluate_section(generated, audit)
        if not review["passed"]:
            reflection_logs.append({"section_id": section["section_id"], "review": review})
            rewrite = await model_factory.generate_text(
                "section",
                section["title"],
                trace_context | {"topic": topic, "section_title": section["title"], "citation_id": citation_id},
            )
            generated["content"] = rewrite.content
        sections.append(generated)
        if idx == 0:
            yield _progress(
                "review_trial_section",
                "Trial section reviewed",
                55,
                {"audit": audit, "review": review},
            )

    combined = "\n\n".join(section["content"] for section in sections)
    citation_audit = guard.verify_citations(combined, papers)
    if not citation_audit["is_valid"]:
        reflection_logs.append({"phase": "citation_guard", "audit": citation_audit})
        raise RuntimeError("Citation hallucination detected after rewrite")
    yield _progress(
        "citation_guard",
        "Citation audit passed with zero hallucinated source IDs",
        72,
        {"citation_audit": citation_audit},
    )

    cited_ids = set(citation_audit["found_ids"])
    cited_papers = [paper for paper in papers if paper["paper_id"] in cited_ids]
    formatter = CitationFormatter()
    references = formatter.batch_process(cited_papers, citation_style)
    yield _progress("citation_format", f"Formatted references with {citation_style}", 82)

    markdown = LCERefiner().merge_sections(topic, sections, references)
    result = {
        "task_id": task_id,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "topic": topic,
        "markdown": markdown,
        "outline": outline,
        "outline_markdown": outline_markdown,
        "papers": papers,
        "sections": sections,
        "references": references,
        "formatter_status": formatter.status(),
        "citation_audit": citation_audit,
        "reflection_logs": reflection_logs,
        "agent_execution": {
            "mode": "delegation" if delegation_results else "skill",
            "parent_run_id": initial_state.get("agent_parent_run_id"),
            "children": delegation_results,
        },
    }
    yield _progress("lce_refine", "Merged sections into final report", 92)
    yield {"event": "skill_result", "phase": "survey_generation", "message": "Skill result ready", "percent": 94, "payload": result}


async def run_survey_workflow(initial_state: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
    """Public Skill entrypoint backed by the compiled LangGraph subgraph."""
    from skills.survey_generation.subgraph import survey_subgraph

    async for event in survey_subgraph.astream(dict(initial_state), stream_mode="custom"):
        yield event
