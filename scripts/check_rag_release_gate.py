from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.evaluation.gate import DEFAULT_THRESHOLDS, evaluate_release_gate


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fail a release when production RAG metrics regress.")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--min-recall-10", type=float, default=DEFAULT_THRESHOLDS["recall@10"])
    parser.add_argument("--min-mrr", type=float, default=DEFAULT_THRESHOLDS["mrr"])
    parser.add_argument("--min-ndcg-3", type=float, default=DEFAULT_THRESHOLDS["ndcg@3"])
    parser.add_argument("--maximum-metric-drop", type=float, default=0.02)
    parser.add_argument("--allow-degraded", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    report = json.loads(args.report.read_text(encoding="utf-8"))
    baseline = (
        json.loads(args.baseline.read_text(encoding="utf-8"))
        if args.baseline
        else None
    )
    result = evaluate_release_gate(
        report,
        thresholds={
            "recall@10": args.min_recall_10,
            "mrr": args.min_mrr,
            "ndcg@3": args.min_ndcg_3,
        },
        baseline=baseline,
        maximum_metric_drop=max(0.0, args.maximum_metric_drop),
        allow_degraded=args.allow_degraded,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
