from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from app.retrieval.bm25 import BM25CapacityExceeded, BM25IndexCache, search_tokens
from app.retrieval.models import RetrievalRequest
from app.retrieval.service import RetrievalService
from app.services.citation_evidence import bind_paragraphs, evidence_for_paper, validate_semantic_review
from mcp_server.scholar_mcp.paper_identity import deduplicate_papers
from tests.test_retrieval_service import _candidate, _Embedding


class BM25AcceptanceTests(unittest.IsolatedAsyncioTestCase):
    def test_real_bm25_scores_and_multilingual_tokens(self):
        cache = BM25IndexCache()
        documents = [replace(_candidate("a", "p1", 0), title="", content="point cloud repair repair"),
                     replace(_candidate("b", "p2", 0), title="", content="healthcare diagnosis")]
        index = cache.build(("t", "u", "f", "v1"), documents, 1.5, .75)
        hits = cache.search(index, "repair", 10)
        self.assertEqual([hit.chunk_id for hit in hits], ["a"])
        self.assertGreater(hits[0].score, 0)
        self.assertIn("\u70b9\u4e91", search_tokens("\u70b9\u4e91\u4fee\u590d"))

    def test_cache_version_scope_and_capacity(self):
        cache = BM25IndexCache(max_entries=2, max_documents=1)
        document = _candidate("a", "p1", 0)
        first = ("t", "u", "filter", "v1")
        newer = ("t", "u", "filter", "v2")
        cache.build(first, [document], 1.5, .75)
        self.assertIsNone(cache.get(("other-tenant", "u", "filter", "v1")))
        cache.build(newer, [document], 1.5, .75)
        self.assertIsNone(cache.get(first))
        with self.assertRaises(BM25CapacityExceeded):
            cache.build(newer, [document, document], 1.5, .75)

    async def test_preferences_recall_without_topic_drift(self):
        observed = []
        main = replace(_candidate("a", "p1", 1), title="point cloud", content="point cloud baseline")
        recent = replace(_candidate("b", "p2", 1), title="point cloud repair", content="point cloud recent method")
        unrelated = replace(_candidate("c", "p3", 1), title="healthcare", content="healthcare recent method")

        class Repository:
            async def bm25_candidates(self, request, **kwargs):
                observed.append(request.query)
                return [main] if request.query == "point cloud" else [unrelated, recent]

            async def vector_candidates(self, *args):
                return []

        response = await RetrievalService(Repository(), _Embedding()).search(
            RetrievalRequest("t", "u", "point cloud", limit=3, preference_query="recent method", preference_weight=.2)
        )
        self.assertEqual(observed, ["point cloud", "recent method"])
        self.assertEqual({hit.chunk_id for hit in response.local_hits}, {"a", "b"})
        self.assertEqual(response.ranking_policy["lexical_backend"], "bm25plus")
        self.assertEqual(response.query, "point cloud")

    def test_bounded_recency_changes_rank_not_evidence(self):
        old = replace(_candidate("a", "p1", 1), published_at="2000-01-01")
        new = replace(_candidate("b", "p2", 1), published_at=datetime.now(timezone.utc).date().isoformat())
        request = RetrievalRequest("t", "u", "evidence", recency_weight=.3)
        hits = RetrievalService._fuse([old, new], [], 2, request=request)
        self.assertEqual(hits[0].chunk_id, "b")
        self.assertEqual(hits[0].snippet, new.content)
        self.assertGreater(hits[0].temporal_score, hits[1].temporal_score)


class EvidenceAcceptanceTests(unittest.TestCase):
    paper = {"paper_id": "paper:p1", "title": "Point cloud repair", "abstract": "Point cloud repair uses local geometric structure."}

    def test_exact_offsets_and_abstract_scope(self):
        item = evidence_for_paper(self.paper, "repair")[0]
        self.assertEqual(self.paper[item["field"]][item["start"]:item["end"]], item["quote"])
        self.assertEqual(item["scope"], "abstract")

    def test_heading_cannot_hide_uncited_paragraph(self):
        result = bind_paragraphs("## Findings\nAn unsupported assertion.", [self.paper])
        self.assertFalse(result["evidence_located"])
        self.assertEqual(len(result["bindings"]), 1)

    def test_unknown_source_and_empty_source_fail(self):
        for papers, text in [([self.paper], "Claim [paper:invented]."),
                             ([{"paper_id": "paper:p1"}], "Claim [paper:p1].")]:
            self.assertFalse(bind_paragraphs(text, papers)["evidence_located"])

    def test_semantic_verdict_requires_real_quote(self):
        bindings = bind_paragraphs("Repair uses local structure [paper:p1].", [self.paper])["bindings"]
        payload = {"paragraphs": [{"paragraph_id": 1, "verdict": "supported", "paper_id": "paper:p1",
                                    "quote": "Point cloud repair uses local geometric structure.", "reason": "Grounded"}]}
        self.assertTrue(validate_semantic_review(payload, bindings)[0]["passed"])
        payload["paragraphs"][0]["quote"] = "A fabricated quote about perfect accuracy"
        self.assertFalse(validate_semantic_review(payload, bindings)[0]["passed"])
        payload["paragraphs"].append(payload["paragraphs"][0])
        with self.assertRaises(ValueError):
            validate_semantic_review(payload, bindings)

    def test_identity_deduplicates_sources_but_not_conflicting_dois(self):
        base = {"paper_id": "a", "title": "A study of point cloud reconstruction", "doi": "https://doi.org/10.1000/ABC", "source": "openalex"}
        second = {**base, "paper_id": "b", "doi": "10.1000/abc", "source": "crossref", "abstract": "Real abstract"}
        conflict = {**base, "paper_id": "c", "doi": "10.1000/other"}
        result = deduplicate_papers([base, second, conflict])
        self.assertEqual(len(result), 2)
        self.assertEqual(len(result[0]["source_records"]), 2)
        self.assertEqual(result[0]["abstract"], "Real abstract")
        self.assertEqual(len(deduplicate_papers([{**base, "tenant_id": "a"}, {**second, "tenant_id": "b"}])), 2)


class MemoryLedger:
    """Test double for durable node records, not a production fallback."""
    def __init__(self):
        self.rows = []
        self.invalidations = []

    @staticmethod
    def fingerprint(payload, dependencies):
        return hashlib.sha256(json.dumps([payload, dependencies], sort_keys=True).encode()).hexdigest()

    def latest_completed(self, **kwargs):
        for row in reversed(self.rows):
            if row.status == "completed" and all(getattr(row, key) == value for key, value in kwargs.items()):
                return deepcopy(row)

    def start(self, **kwargs):
        run_id = str(len(self.rows))
        self.rows.append(SimpleNamespace(**kwargs, input_fingerprint=kwargs["fingerprint"],
                                        run_id=run_id, status="running", output={}, quality={}))
        return run_id

    def complete(self, run_id, output, quality):
        self.rows[int(run_id)].output = deepcopy(output)
        self.rows[int(run_id)].quality = deepcopy(quality)
        self.rows[int(run_id)].status = "completed"

    def fail(self, run_id, error):
        self.rows[int(run_id)].status = "failed"

    def list_task_runs(self, state):
        return [dict(row.__dict__) for row in self.rows]

    def invalidate(self, plan, state, target):
        self.invalidations.append(target)
        targets = {target, "section_writing", "quality_review"} if target.startswith("section:") else plan.descendants(target)
        for row in self.rows:
            if row.node_id in targets and row.status == "completed":
                row.status = "invalidated"
        return sorted(targets)


class LifecycleAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config = patch("app.services.mysql_store.get_all_settings", return_value={})
        self.config.start()
        self.addCleanup(self.config.stop)

    async def test_real_stategraph_retries_only_failed_section(self):
        from agents.task_graph import DynamicTaskPlanner
        from agents.specialized.writing_lifecycle import RetrievalSkillAgent
        from skills.survey_generation.subgraph import build_lifecycle_graph
        ledger = MemoryLedger()
        calls = Counter()
        paper = EvidenceAcceptanceTests.paper

        async def generate(purpose, prompt, context):
            data = json.loads(prompt)
            calls[purpose] += 1
            if purpose == "outline":
                return SimpleNamespace(content="## Methods\n\n## Discussion")
            if purpose == "section":
                section = data["section"]["section_id"]
                calls[section] += 1
                citation = "[paper:invented]" if section == "section_1" and calls[section] == 1 else "[paper:p1]"
                return SimpleNamespace(content=("Point cloud repair uses local geometric structure. " * 3) + citation)
            if purpose == "evidence_review":
                return SimpleNamespace(content=json.dumps({"paragraphs": [{"paragraph_id": item["paragraph_id"],
                    "verdict": "supported", "paper_id": "paper:p1", "quote": paper["abstract"], "reason": "Supported"}
                    for item in data["paragraphs"]]}))
            raise AssertionError(purpose)

        plan = DynamicTaskPlanner().plan_writing("Point cloud repair", {})
        with patch("agents.specialized.writing_lifecycle.node_run_store", ledger), \
             patch("skills.survey_generation.subgraph.node_run_store", ledger), \
             patch.object(RetrievalSkillAgent, "execute", AsyncMock(return_value=({"papers": [paper], "chunks": []}, {"passed": True}))), \
             patch("agents.specialized.writing_lifecycle.model_factory.generate_text", side_effect=generate):
            final = await build_lifecycle_graph(plan).ainvoke({"task_id": "task", "tenant_id": "t", "user_id": "u",
                "topic": "Point cloud repair", "require_outline_confirmation": False})
        self.assertTrue(final["quality_decision"]["passed"])
        self.assertEqual(calls["section_1"], 2)
        self.assertEqual(calls["section_2"], 1)
        self.assertEqual(calls["outline"], 1)
        self.assertEqual(ledger.invalidations, ["section:section_1"])
        self.assertTrue(final["skill_result"]["node_snapshots"])

    async def test_outline_edit_reuses_unmodified_sections(self):
        from agents.specialized.writing_lifecycle import SectionWritingSkillAgent, _parse_outline
        from agents.task_graph import DynamicTaskPlanner
        ledger = MemoryLedger()
        paper = EvidenceAcceptanceTests.paper
        outline = _parse_outline("## A\n## B", [paper])
        state = {"tenant_id": "t", "user_id": "u", "task_id": "task", "topic": "repair", "papers": [paper], "outline": outline}
        node = DynamicTaskPlanner().plan_writing("repair", {}).node_for_capability("section_writing")
        generate = AsyncMock(return_value=SimpleNamespace(content=paper["abstract"] * 3 + " [paper:p1]"))
        with patch("agents.specialized.writing_lifecycle.node_run_store", ledger), \
             patch("agents.specialized.writing_lifecycle.model_factory.generate_text", generate):
            await SectionWritingSkillAgent().execute(state, node)
            updated = _parse_outline("## A\n## C", [paper], outline)
            self.assertEqual(updated[0]["section_id"], outline[0]["section_id"])
            await SectionWritingSkillAgent().execute({**state, "outline": updated}, node)
        self.assertEqual(generate.await_count, 3)

    async def test_invalid_outline_is_not_replaced_with_template(self):
        from agents.specialized.writing_lifecycle import OutlineSkillAgent
        from agents.task_graph import DynamicTaskPlanner
        node = DynamicTaskPlanner().plan_writing("repair", {}).node_for_capability("outline_generation")
        with patch("agents.specialized.writing_lifecycle.model_factory.generate_text", AsyncMock(return_value=SimpleNamespace(content="not an outline"))):
            with self.assertRaises(ValueError):
                await OutlineSkillAgent().execute({"topic": "repair", "papers": []}, node)

    async def test_independent_skills_are_discovered_and_tenant_scoped(self):
        from agents.skill_registry import skill_registry
        from agents.skill_execution import execute_registered_skill
        names = {item.name for item in skill_registry.list_skills()}
        self.assertTrue({"paper_retrieval", "citation_audit", "citation_formatting", "survey_generation"}.issubset(names))
        result = await execute_registered_skill("citation_audit", {"tenant_id": "t", "user_id": "u",
            "text": "Claim [paper:p1]", "papers": [EvidenceAcceptanceTests.paper]})
        self.assertEqual(result["tenant_id"], "t")
        with self.assertRaises(ValueError):
            await execute_registered_skill("citation_audit", {"tenant_id": "t", "user_id": "u",
                "text": "Claim", "papers": [{"tenant_id": "different"}]})

    async def test_same_provider_fallback_uses_independent_model(self):
        from app.config import get_settings
        from agents.factory import ModelFactory, ModelResponse
        settings = replace(get_settings(), primary_model_provider="qwen", secondary_model_provider="qwen",
                           secondary_model_name="backup-model", secondary_model_base_url="https://backup.example/v1",
                           secondary_model_api_key="test-key", model_response_cache_enabled=False)
        factory = ModelFactory()
        fallback = AsyncMock(return_value=ModelResponse("answer", "qwen", "backup-model"))
        with patch("agents.factory.get_settings", return_value=settings), \
             patch.object(factory, "_generate_with_provider", AsyncMock(side_effect=RuntimeError("offline"))), \
             patch.object(factory, "_generate_with_candidate", fallback):
            result = await factory.generate_text("conversation", "hello", {"tenant_id": "t", "user_id": "u"})
        self.assertEqual(result.model, "backup-model")
        self.assertEqual(fallback.call_args.args[0].base_url, "https://backup.example/v1")

    async def test_changing_model_does_not_reuse_old_response(self):
        from app.config import get_settings
        from agents.factory import ModelFactory, ModelResponse
        settings = replace(get_settings(), primary_model_provider="qwen", llm_model="one", model_response_cache_enabled=True)
        factory = ModelFactory()
        generate = AsyncMock(return_value=ModelResponse("first", "qwen", "one"))
        with patch("agents.factory.get_settings", return_value=settings) as config, \
             patch.object(factory, "_generate_with_provider", generate), \
             patch.object(factory, "_trace_model_call"):
            await factory.generate_text("outline", "same prompt", {"tenant_id": "t", "user_id": "u"})
            await factory.generate_text("outline", "same prompt", {"tenant_id": "t", "user_id": "u"})
            self.assertEqual(generate.await_count, 1)
            config.return_value = replace(settings, llm_model="two")
            await factory.generate_text("outline", "same prompt", {"tenant_id": "t", "user_id": "u"})
            self.assertEqual(generate.await_count, 2)

    async def test_search_candidate_can_be_saved_with_extra_ranking_fields(self):
        from mcp_server.scholar_mcp.tools import save_to_knowledge
        candidate = {**EvidenceAcceptanceTests.paper, "source": "openalex", "can_cite": False,
                     "acquisition_required": True, "score": .5, "tenant_id": "untrusted",
                     "source_records": [{"paper_id": "paper:p1", "source": "openalex"}]}
        save = AsyncMock(side_effect=lambda record: record.to_dict())
        with patch("mcp_server.scholar_mcp.tools.knowledge_store.save_paper", save):
            result = await save_to_knowledge("t", "u", candidate)
        self.assertEqual(result["paper"]["tenant_id"], "t")
        self.assertEqual(result["paper"]["metadata"]["source_records"], candidate["source_records"])

    def test_mcp_schema_preserves_typed_arguments(self):
        from mcp_server.scholar_mcp.registry import tool_registry
        schema = tool_registry.get_spec("search_papers").input_schema
        self.assertEqual(schema["properties"]["limit"]["type"], "integer")
        self.assertEqual(schema["properties"]["persist_results"]["type"], "boolean")
        schema = tool_registry.get_spec("execute_skill").input_schema
        self.assertEqual(schema["properties"]["inputs"]["type"], "object")

    def test_structured_evidence_is_not_silently_truncated(self):
        from agents.runtime.token_policy import TokenPolicy
        with self.assertRaisesRegex(ValueError, "split the structured evidence"):
            TokenPolicy().prepare("evidence_review", json.dumps({"paragraphs": ["x" * 40000]}), {})

    async def test_external_metadata_is_acquired_before_writing(self):
        from agents.specialized.writing_lifecycle import RetrievalSkillAgent
        from agents.task_graph import DynamicTaskPlanner
        candidate = {**EvidenceAcceptanceTests.paper, "can_cite": False, "source": "openalex"}
        calls = []

        async def call(name, arguments):
            calls.append((name, arguments))
            if name == "search_papers":
                return {"items": [candidate], "source_status": []}
            self.assertEqual(name, "acquire_paper_to_knowledge")
            return {"acquired": True, "paper": {**candidate, "full_text": candidate["abstract"]}}

        state = {"tenant_id": "t", "user_id": "u", "task_id": "task", "topic": "point cloud repair", "max_papers": 12}
        node = DynamicTaskPlanner().plan_writing(state["topic"], {}).nodes[0]
        with patch("agents.specialized.writing_lifecycle.ScholarMCPClient") as client:
            client.return_value.call_tool = call
            output, quality = await RetrievalSkillAgent().execute(state, node)
        self.assertEqual(calls[0][1]["limit"], 48)
        self.assertTrue(output["retrieval_summary"]["acquisition"][0]["acquired"])
        self.assertEqual(quality["paper_count"], 1)

    def test_trace_redaction_preserves_token_usage(self):
        from app.services.tracing import TraceRecorder
        sanitized = TraceRecorder._sanitize({"api_key": "secret", "access_token": "secret", "input_tokens": 10, "detail": "Bearer abc123"})
        self.assertEqual(sanitized["input_tokens"], 10)
        self.assertEqual(sanitized["api_key"], "[REDACTED]")
        self.assertNotIn("abc123", sanitized["detail"])


if __name__ == "__main__":
    unittest.main()
