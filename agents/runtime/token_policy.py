from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any


@dataclass(frozen=True)
class ModelCallBudget:
    max_input_tokens: int
    max_output_tokens: int
    cache_ttl_seconds: int = 0


class TokenPolicy:
    """Apply purpose-aware hard ceilings before provider requests are created."""

    _budgets = {
        "intent_planning": ModelCallBudget(1800, 420, 300),
        "tool_planning": ModelCallBudget(1800, 420, 300),
        "conversation": ModelCallBudget(5600, 1000),
        "orchestrator_synthesis": ModelCallBudget(7000, 1200),
        "delegated_agent": ModelCallBudget(4200, 800),
        "outline": ModelCallBudget(5200, 900, 1800),
        "section": ModelCallBudget(6000, 1200, 3600),
        "critic": ModelCallBudget(3600, 600, 1800),
        "translation": ModelCallBudget(4200, 1400),
        "connection_probe": ModelCallBudget(1200, 200, 60),
    }
    _default = ModelCallBudget(5000, 1000)

    @staticmethod
    def estimate_tokens(value: str) -> int:
        return max(1, len(value) // 3)

    def budget_for(self, purpose: str) -> ModelCallBudget:
        return self._budgets.get(purpose, self._default)

    def prepare(
        self,
        purpose: str,
        prompt: str,
        context: dict[str, Any],
    ) -> tuple[str, dict[str, Any], ModelCallBudget, int]:
        budget = self.budget_for(purpose)
        compact_context = self._compact(context, depth=0)
        context_budget = max(240, budget.max_input_tokens // 2)
        if self.estimate_tokens(str(compact_context)) > context_budget:
            compact_context = {
                "compressed_context": self._fit_text(
                    json.dumps(compact_context, ensure_ascii=False, default=str),
                    context_budget,
                )
            }
        context_cost = self.estimate_tokens(str(compact_context))
        prompt_budget = max(240, budget.max_input_tokens - context_cost)
        compact_prompt = self._fit_text(prompt, prompt_budget)
        estimated = self.estimate_tokens(compact_prompt) + self.estimate_tokens(str(compact_context))
        return compact_prompt, compact_context, budget, estimated

    @classmethod
    def _fit_text(cls, value: str, token_budget: int) -> str:
        char_budget = max(300, token_budget * 3)
        if len(value) <= char_budget:
            return value
        head = max(120, char_budget // 3)
        tail = max(120, char_budget - head - 42)
        return value[:head] + "\n\n[中间上下文已按预算压缩]\n\n" + value[-tail:]

    @classmethod
    def _compact(cls, value: Any, *, depth: int) -> Any:
        if depth >= 4:
            return str(value)[:240]
        if isinstance(value, dict):
            result: dict[str, Any] = {}
            for key, item in list(value.items())[:36]:
                if item in (None, "", [], {}):
                    continue
                result[str(key)] = cls._compact(item, depth=depth + 1)
            return result
        if isinstance(value, (list, tuple)):
            return [cls._compact(item, depth=depth + 1) for item in list(value)[-12:]]
        if isinstance(value, str):
            return value if len(value) <= 1800 else value[:600] + " … " + value[-1000:]
        return value


token_policy = TokenPolicy()
