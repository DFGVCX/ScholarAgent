from __future__ import annotations

import unittest

from skills.survey_generation.subgraph import survey_subgraph


class SurveySubgraphTests(unittest.TestCase):
    def test_subgraph_has_quality_loop(self) -> None:
        graph = survey_subgraph.get_graph()
        self.assertTrue(
            {"run_pipeline", "quality_gate", "targeted_retry", "finish"}
            .issubset(graph.nodes)
        )
        edges = {(edge.source, edge.target) for edge in graph.edges}
        self.assertIn(("targeted_retry", "run_pipeline"), edges)


if __name__ == "__main__":
    unittest.main()
