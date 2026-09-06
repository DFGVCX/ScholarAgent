from __future__ import annotations

import argparse
from importlib.metadata import version
import json
from pathlib import Path
from typing import Any, Mapping


RequiredArtifacts = Mapping[str, tuple[Path, ...]]

# Folder names produced by ``docling.utils.model_downloader.download_models``
# for the project-pinned Docling 2.123.0. Keeping the readiness check static is
# intentional: upload workers can reject an incomplete volume without importing
# Docling, Transformers, or Torch just to discover these names.
_PINNED_REQUIRED_ARTIFACTS: dict[str, tuple[Path, ...]] = {
    "layout": (
        Path("docling-project--docling-layout-heron"),
        Path("docling-project--docling-layout-heron-onnx"),
    ),
    "tableformer": (Path("docling-project--docling-models"),),
    "code_formula": (Path("docling-project--CodeFormulaV2"),),
}


def required_artifact_directories() -> dict[str, tuple[Path, ...]]:
    """Return the model layout for the pinned Docling release without loading it."""
    return {name: tuple(paths) for name, paths in _PINNED_REQUIRED_ARTIFACTS.items()}


def inspect_artifacts(
    root: Path,
    *,
    required: RequiredArtifacts | None = None,
) -> dict[str, Any]:
    required = required or required_artifact_directories()
    missing: list[str] = []
    models: dict[str, dict[str, Any]] = {}

    for model_name, directories in required.items():
        file_count = 0
        relative_directories: list[str] = []
        if not directories:
            missing.append(f"{model_name}:<no-directories>")
        for relative in directories:
            relative = Path(relative)
            relative_directories.append(relative.as_posix())
            target = root / relative
            count = sum(1 for path in target.rglob("*") if path.is_file()) if target.is_dir() else 0
            file_count += count
            if count == 0:
                missing.append(f"{model_name}:{relative.as_posix()}")
        models[model_name] = {
            "directories": relative_directories,
            "file_count": file_count,
        }

    return {
        "ready": not missing,
        "artifacts_path": str(root),
        "models": models,
        "missing": missing,
    }


def inspect_runtime(
    *,
    torch_module: Any | None = None,
    docling_version: str | None = None,
) -> dict[str, Any]:
    if torch_module is None:
        import torch as torch_module

    cuda_version = getattr(getattr(torch_module, "version", None), "cuda", None)
    return {
        "docling_version": docling_version or version("docling"),
        "torch_version": str(torch_module.__version__),
        "cuda_version": cuda_version,
        "cpu_only": cuda_version is None,
    }


def prepare_models(root: Path) -> dict[str, Any]:
    from docling.utils.model_downloader import download_models

    download_models(
        output_dir=root,
        progress=True,
        with_layout=True,
        with_tableformer=True,
        with_tableformer_v2=False,
        with_code_formula=True,
        with_picture_classifier=False,
        with_rapidocr=False,
        with_easyocr=False,
        with_nemotron_ocr=False,
    )
    artifacts = inspect_artifacts(root)
    runtime = inspect_runtime()
    return {
        **artifacts,
        "ready": bool(artifacts["ready"] and runtime["cpu_only"]),
        "runtime": runtime,
    }


def check_models(root: Path) -> dict[str, Any]:
    artifacts = inspect_artifacts(root)
    runtime = inspect_runtime()
    return {
        **artifacts,
        "ready": bool(artifacts["ready"] and runtime["cpu_only"]),
        "runtime": runtime,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare and verify ScholarAgent Docling models")
    parser.add_argument("action", choices=("prepare", "check"))
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)

    try:
        report = prepare_models(args.output_dir) if args.action == "prepare" else check_models(args.output_dir)
    except Exception as exc:
        report = {
            "ready": False,
            "error_type": exc.__class__.__name__,
            "error": str(exc),
        }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report.get("ready") else 1


if __name__ == "__main__":
    raise SystemExit(main())
