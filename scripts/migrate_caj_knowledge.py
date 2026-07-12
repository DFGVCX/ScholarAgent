from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.institutional_access.service import (
    InstitutionalAccessError,
    _detect_document,
    _extract_pdf_text,
    _knowledge_source_url,
    _persist_pdf_only,
)
from mcp_server.scholar_mcp.models import PaperRecord
from mcp_server.scholar_mcp.store import knowledge_store


async def migrate(tenant_id: str, user_id: str) -> tuple[int, int]:
    papers = await knowledge_store.search(tenant_id, user_id, "", 500)
    migrated = 0
    failed = 0
    for item in papers:
        metadata = dict(item.get("metadata") or {})
        source_path = Path(
            str(metadata.get("file_path") or item.get("file_path") or "")
        ).resolve()
        if source_path.suffix.lower() != ".caj" or not source_path.is_file():
            continue
        try:
            raw = source_path.read_bytes()
            file_type = _detect_document(raw, "application/octet-stream", source_path.as_uri())
            destination, pdf_raw, digest, _ = await asyncio.to_thread(
                _persist_pdf_only,
                raw,
                file_type,
                source_path.parent,
                str(item.get("title") or source_path.stem),
                _knowledge_source_url(str(item.get("url") or "")),
            )
            full_text = await asyncio.to_thread(_extract_pdf_text, destination)
            metadata.pop("original_file_path", None)
            metadata.update(
                {
                    "file_name": destination.name,
                    "file_path": str(destination),
                    "file_url": f"/knowledge/files/{item['paper_id']}",
                    "content_type": "application/pdf",
                    "content_length": len(pdf_raw),
                    "file_sha256": digest,
                    "document_format": "pdf",
                    "converted_from_caj": True,
                    "parsed": bool(full_text),
                }
            )
            record = PaperRecord(
                paper_id=str(item["paper_id"]),
                tenant_id=tenant_id,
                user_id=user_id,
                source=str(item.get("source") or "cnki"),
                title=str(item.get("title") or source_path.stem),
                authors=list(item.get("authors") or []),
                abstract=full_text[:900] or str(item.get("abstract") or ""),
                full_text=full_text,
                published_at=item.get("published_at"),
                doi=item.get("doi"),
                arxiv_id=item.get("arxiv_id"),
                url=_knowledge_source_url(str(item.get("url") or "")) or None,
                file_path=str(destination),
                in_knowledge_base=bool(item.get("in_knowledge_base", True)),
                metadata=metadata,
            )
            await knowledge_store.save_paper(record)
            source_path.unlink(missing_ok=True)
            migrated += 1
            print(f"MIGRATED {record.paper_id} -> {destination}")
        except (InstitutionalAccessError, OSError) as exc:
            failed += 1
            print(f"FAILED {item.get('paper_id')}: {exc}")
    return migrated, failed


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert legacy knowledge-base CAJ files to PDF")
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--user-id", required=True)
    args = parser.parse_args()
    migrated, failed = asyncio.run(migrate(args.tenant_id, args.user_id))
    print(f"SUMMARY migrated={migrated} failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
