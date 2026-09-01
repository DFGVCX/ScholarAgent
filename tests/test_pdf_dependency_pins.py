from pathlib import Path
import unittest
import yaml


class PdfDependencyPinsTest(unittest.TestCase):
    def test_ingestion_images_install_docling_native_runtime_libraries(self) -> None:
        for dockerfile_name in ("backend", "worker"):
            dockerfile = Path(f"deploy/Dockerfile.{dockerfile_name}").read_text(encoding="utf-8")
            with self.subTest(dockerfile=dockerfile_name, setting="base_image"):
                self.assertIn("FROM python:3.12-slim-trixie", dockerfile)
            for package in ("libxcb1", "libgl1", "libglib2.0-0t64"):
                with self.subTest(dockerfile=dockerfile_name, package=package):
                    self.assertIn(package, dockerfile)

    def test_pdf_text_stack_is_pinned_to_verified_versions(self) -> None:
        requirements = Path("requirements.txt").read_text(encoding="utf-8")
        base_path = Path("requirements-base.txt")
        self.assertTrue(base_path.is_file())
        base_requirements = base_path.read_text(encoding="utf-8")

        self.assertIn("-r requirements-base.txt", requirements)
        self.assertIn("pypdf==6.11.0", base_requirements)
        self.assertIn("PyMuPDF==1.27.2.3", base_requirements)
        self.assertIn("docling==2.123.0", requirements)
        self.assertIn("--extra-index-url https://download.pytorch.org/whl/cpu", requirements)
        self.assertRegex(requirements, r"(?m)^torch==[^\s]+\+cpu$")

    def test_docling_model_cache_is_persistent_for_ingestion_services(self) -> None:
        compose = yaml.safe_load(Path("docker-compose.yml").read_text(encoding="utf-8"))

        for service_name in ("backend", "worker"):
            service = compose["services"][service_name]
            self.assertIn("HF_HOME=/app/storage/models/huggingface", service["environment"])
            self.assertIn("DOCLING_ARTIFACTS_PATH=/app/storage/models/docling", service["environment"])
            self.assertIn("scholar_storage:/app/storage", service["volumes"])

    def test_docling_models_can_be_prefetched_into_the_shared_volume(self) -> None:
        compose = yaml.safe_load(Path("docker-compose.yml").read_text(encoding="utf-8"))

        service = compose["services"]["docling_models"]
        self.assertEqual(service["profiles"], ["setup"])
        self.assertEqual(service["build"]["dockerfile"], "deploy/Dockerfile.backend")
        self.assertIn("scholar_storage:/app/storage", service["volumes"])
        self.assertIn("DOCLING_ARTIFACTS_PATH=/app/storage/models/docling", service["environment"])
        self.assertEqual(
            service["command"],
            [
                "python",
                "-m",
                "app.papers.docling_models",
                "prepare",
                "--output-dir",
                "/app/storage/models/docling",
            ],
        )


if __name__ == "__main__":
    unittest.main()
