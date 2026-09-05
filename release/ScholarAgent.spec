# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules, copy_metadata


ROOT = Path(SPECPATH).parent

datas = [(str(ROOT / "frontend" / "dist"), "frontend/dist")]
binaries = []
hiddenimports = []

for package in ("app", "agents", "skills", "mcp_server", "browser_worker", "desktop"):
    hiddenimports += collect_submodules(package)

for package in ("playwright",):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

hiddenimports += collect_submodules(
    "chromadb",
    filter=lambda name: not name.startswith(
        ("chromadb.test", "chromadb.server", "chromadb.cli")
    ),
)
datas += collect_data_files("chromadb", excludes=["test/**", "tests/**"])

for distribution in (
    "chromadb",
    "fastapi",
    "langgraph",
    "langgraph-checkpoint",
    "langgraph-checkpoint-sqlite",
    "mcp",
    "playwright",
    "pydantic",
    "uvicorn",
):
    try:
        datas += copy_metadata(distribution, recursive=True)
    except Exception:
        pass

datas += collect_data_files("cajCvtPdf", include_py_files=True)

a = Analysis(
    [str(ROOT / "desktop" / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "pytest",
        "unittest.mock",
        "chromadb.test",
        "chromadb.server",
        "chromadb.cli",
        "onnxruntime.quantization",
        "onnxruntime.tools",
        "onnxruntime.transformers",
    ],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ScholarAgent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="ScholarAgent",
)
