from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest


class DoclingModelInspectionTest(unittest.TestCase):
    def test_model_without_expected_directories_is_not_ready(self) -> None:
        from app.papers.docling_models import inspect_artifacts

        with TemporaryDirectory() as directory:
            report = inspect_artifacts(
                Path(directory),
                required={"layout": ()},
            )

        self.assertFalse(report["ready"])
        self.assertEqual(report["missing"], ["layout:<no-directories>"])

    def test_runtime_report_rejects_cuda_torch_build(self) -> None:
        from app.papers.docling_models import inspect_runtime

        cpu = inspect_runtime(
            torch_module=SimpleNamespace(__version__="2.10.0+cpu", version=SimpleNamespace(cuda=None)),
            docling_version="2.123.0",
        )
        cuda = inspect_runtime(
            torch_module=SimpleNamespace(__version__="2.10.0", version=SimpleNamespace(cuda="13.0")),
            docling_version="2.123.0",
        )

        self.assertTrue(cpu["cpu_only"])
        self.assertFalse(cuda["cpu_only"])
        self.assertEqual(cuda["cuda_version"], "13.0")

    def test_missing_or_empty_model_directories_are_not_ready(self) -> None:
        from app.papers.docling_models import inspect_artifacts

        required = {
            "layout": (Path("layout-model"),),
            "tableformer": (Path("table-model"),),
            "code_formula": (Path("formula-model"),),
        }
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "layout-model").mkdir()
            (root / "table-model").mkdir()
            (root / "table-model" / "config.json").write_text("{}", encoding="utf-8")

            report = inspect_artifacts(root, required=required)

        self.assertFalse(report["ready"])
        self.assertEqual(
            report["missing"],
            ["layout:layout-model", "code_formula:formula-model"],
        )
        self.assertEqual(report["models"]["tableformer"]["file_count"], 1)

    def test_all_required_model_files_produce_ready_report(self) -> None:
        from app.papers.docling_models import inspect_artifacts

        required = {
            "layout": (Path("layout-model"),),
            "tableformer": (Path("table-model"),),
            "code_formula": (Path("formula-model"),),
        }
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in ("layout-model", "table-model", "formula-model"):
                target = root / relative
                target.mkdir()
                (target / "weights.bin").write_bytes(b"model")

            report = inspect_artifacts(root, required=required)

        self.assertTrue(report["ready"])
        self.assertEqual(report["missing"], [])
        self.assertEqual(set(report["models"]), {"layout", "tableformer", "code_formula"})


if __name__ == "__main__":
    unittest.main()
