from __future__ import annotations

import inspect
from typing import Any, Awaitable, Callable

from mcp_server.scholar_mcp.models import SafetyLevel, ToolSpec

ToolCallable = Callable[..., Awaitable[dict[str, Any]]]


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolCallable] = {}
        self._specs: dict[str, ToolSpec] = {}

    def register(
        self,
        *,
        name: str,
        description: str,
        category: str,
        safety_level: SafetyLevel = SafetyLevel.LOW,
        requires_user_id: bool = True,
    ) -> Callable[[ToolCallable], ToolCallable]:
        def decorator(func: ToolCallable) -> ToolCallable:
            self._tools[name] = func
            self._specs[name] = ToolSpec(
                name=name,
                description=description,
                category=category,
                safety_level=safety_level,
                input_schema=self._build_schema(func),
                requires_user_id=requires_user_id,
            )
            return func

        return decorator

    def _build_schema(self, func: ToolCallable) -> dict[str, Any]:
        signature = inspect.signature(func)
        properties: dict[str, Any] = {}
        required: list[str] = []
        for name, parameter in signature.parameters.items():
            if name == "self":
                continue
            properties[name] = {"type": "string"}
            if parameter.default is inspect._empty:
                required.append(name)
        return {"type": "object", "properties": properties, "required": required}

    def list_specs(self) -> list[dict[str, Any]]:
        return [
            {
                "name": spec.name,
                "description": spec.description,
                "category": spec.category,
                "safety_level": spec.safety_level.value,
            }
            for spec in self._specs.values()
        ]

    def get_spec(self, name: str) -> ToolSpec:
        return self._specs[name]

    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return await self._tools[name](**arguments)


tool_registry = ToolRegistry()


def scholar_tool(
    *,
    name: str,
    description: str,
    category: str,
    safety_level: SafetyLevel = SafetyLevel.LOW,
    requires_user_id: bool = True,
) -> Callable[[ToolCallable], ToolCallable]:
    return tool_registry.register(
        name=name,
        description=description,
        category=category,
        safety_level=safety_level,
        requires_user_id=requires_user_id,
    )

