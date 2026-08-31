from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
import re
from typing import Any


_GENERATED_PNG_RE = re.compile(r"^page_\d{3}_[A-Za-z0-9_.-]+\.png$", re.IGNORECASE)


def _safe_name(value: object) -> str:
    name = str(value or "").strip()
    return name if name and Path(name).name == name and name not in {".", ".."} else ""


def inventory_from_pages(pages: Sequence[object]) -> list[dict[str, Any]]:
    inventory: list[dict[str, Any]] = []
    seen: set[str] = set()
    for page in pages:
        page_number = int(getattr(page, "page_number", 1) or 1)
        for block in tuple(getattr(page, "blocks", ()) or ()):
            metadata = dict(getattr(block, "metadata", {}) or {})
            name = _safe_name(metadata.get("asset_name"))
            if not name or name in seen:
                continue
            seen.add(name)
            inventory.append(
                {
                    "name": name,
                    "type": str(getattr(block, "block_type", "asset") or "asset"),
                    "page_number": page_number,
                    "block_id": str(metadata.get("block_id") or ""),
                    "label": str(metadata.get("label") or ""),
                    "quality_status": str(metadata.get("quality_status") or ""),
                }
            )
    return inventory


def inventory_from_manifest(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Normalize both current and pre-inventory parse manifests."""
    inventory: list[dict[str, Any]] = []
    seen: set[str] = set()

    for raw in manifest.get("asset_inventory", []) or []:
        if not isinstance(raw, Mapping):
            continue
        name = _safe_name(raw.get("name"))
        if not name or name in seen:
            continue
        seen.add(name)
        item = dict(raw)
        item["name"] = name
        inventory.append(item)

    for raw in manifest.get("visual_blocks", []) or []:
        if not isinstance(raw, Mapping):
            continue
        metadata = dict(raw.get("metadata") or {})
        name = _safe_name(metadata.get("asset_name"))
        if not name or name in seen:
            continue
        seen.add(name)
        inventory.append(
            {
                "name": name,
                "type": str(raw.get("block_type") or "asset"),
                "page_number": int(raw.get("page_number") or metadata.get("page_number") or 1),
                "label": str(metadata.get("label") or ""),
                "quality_status": str(metadata.get("quality_status") or ""),
            }
        )

    for raw in manifest.get("equations", []) or []:
        if not isinstance(raw, Mapping):
            continue
        name = _safe_name(raw.get("asset_name"))
        if not name or name in seen:
            continue
        seen.add(name)
        inventory.append(
            {
                "name": name,
                "type": "equation",
                "page_number": int(raw.get("page_number") or 1),
                "label": str(raw.get("label") or ""),
                "quality_status": str(raw.get("quality_status") or ""),
            }
        )
    return inventory


def unreferenced_generated_assets(
    existing_names: Iterable[str],
    manifests: Iterable[Mapping[str, Any]],
) -> list[str]:
    """Identify only generated direct-child PNGs; this function never deletes files."""
    referenced = {
        item["name"]
        for manifest in manifests
        for item in inventory_from_manifest(manifest)
    }
    return sorted(
        name
        for raw_name in existing_names
        if (name := _safe_name(raw_name))
        and _GENERATED_PNG_RE.fullmatch(name)
        and name not in referenced
    )
