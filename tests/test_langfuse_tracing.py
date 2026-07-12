from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.tracing import TraceRecorder


class FakeObservation:
    def __init__(self) -> None:
        self.ended = False

    def end(self) -> None:
        self.ended = True


class FakeLangfuse:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.observation = FakeObservation()

    def create_trace_id(self, *, seed: str) -> str:
        return f"trace-{seed}"

    def start_observation(self, **kwargs):
        self.calls.append(kwargs)
        return self.observation

    def flush(self) -> None:
        return None


class LangfuseTracingTest(unittest.TestCase):
    def test_event_is_redacted_and_mirrored_to_langfuse(self) -> None:
        fake = FakeLangfuse()
        recorder = TraceRecorder(Path("storage/test_trace_events.jsonl"), fake)
        with patch("app.services.tracing.mysql_store.is_available", return_value=False):
            recorder.record(
                "trace-1",
                "generate",
                "model_call",
                tenant_id="tenant_demo",
                model="qwen",
                metadata={"api_key": "secret", "result": "ok"},
            )
        self.assertEqual(fake.calls[0]["as_type"], "generation")
        self.assertEqual(fake.calls[0]["output"]["api_key"], "[REDACTED]")
        self.assertTrue(fake.observation.ended)


if __name__ == "__main__":
    unittest.main()
