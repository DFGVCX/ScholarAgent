from __future__ import annotations

import unittest

from agents.graph import app


class LangGraphStateGraphTest(unittest.TestCase):
    def test_global_workflow_is_a_compiled_graph_with_explicit_nodes(self) -> None:
        graph = app.get_graph()
        self.assertTrue(
            {"route_task", "execute_skill", "global_review", "finalize"}
            .issubset(graph.nodes)
        )
        self.assertIn("InMemorySaver", type(app.checkpointer).__name__)


if __name__ == "__main__":
    unittest.main()
