from __future__ import annotations

from mcp_server.scholar_mcp.models import SafetyLevel, ToolSpec


class SafetyDecision:
    def __init__(self, allowed: bool, reason: str = "", require_confirmation: bool = False) -> None:
        self.allowed = allowed
        self.reason = reason
        self.require_confirmation = require_confirmation

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "require_confirmation": self.require_confirmation,
        }


def evaluate_tool_safety(spec: ToolSpec, arguments: dict) -> SafetyDecision:
    if spec.requires_user_id:
        if not arguments.get("tenant_id") or not arguments.get("user_id"):
            return SafetyDecision(False, "tenant_id and user_id are required")
    if spec.safety_level == SafetyLevel.HIGH and not arguments.get("confirmation_token"):
        return SafetyDecision(
            False,
            f"Tool {spec.name} requires human confirmation",
            require_confirmation=True,
        )
    return SafetyDecision(True)

