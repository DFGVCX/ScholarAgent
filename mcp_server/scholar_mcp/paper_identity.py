from __future__ import annotations

import re
from typing import Any, Iterable
from urllib.parse import unquote


def normalized_doi(value: Any) -> str:
    doi = unquote(str(value or "")).strip().lower()
    return re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", doi).rstrip(" .")


def identity_keys(paper: dict[str, Any]) -> set[str]:
    keys = set()
    doi = normalized_doi(paper.get("doi"))
    if doi:
        keys.add(f"doi:{doi}")
    arxiv = str(paper.get("arxiv_id") or "").split("/abs/")[-1].split("/pdf/")[-1]
    if arxiv:
        keys.add("arxiv:" + re.sub(r"v\d+$", "", arxiv))
    title = re.sub(r"[^\w]", "", str(paper.get("title") or "").casefold())
    if len(title) >= 20:
        year = str(paper.get("published_at") or paper.get("year") or "")[:4]
        keys.add(f"title:{title}:{year}")
    if paper.get("paper_id"):
        keys.add(f"id:{paper['paper_id']}")
    return keys


def deduplicate_papers(papers: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    for raw in papers:
        item = dict(raw)
        keys = identity_keys(item)
        matches = [group for group in groups if identity_keys(group).intersection(keys)
                   and all(group.get(field) == item.get(field) for field in ("tenant_id", "user_id"))
                   and not (normalized_doi(group.get("doi")) and normalized_doi(item.get("doi"))
                            and normalized_doi(group["doi"]) != normalized_doi(item["doi"]))]
        previous = matches[0] if matches else None
        provenance = {"paper_id": item.get("paper_id"), "source": item.get("source"), "url": item.get("url")}
        item["source_records"] = [*item.get("source_records", []), provenance]
        if previous is None:
            groups.append(item)
            continue
        for merged in [item, *matches[1:]]:
            for source in merged["source_records"]:
                if source not in previous["source_records"]:
                    previous["source_records"].append(source)
            for name in ("doi", "arxiv_id", "abstract", "full_text", "url", "authors"):
                if not previous.get(name) and merged.get(name):
                    previous[name] = merged[name]
            previous["metadata"] = {**(merged.get("metadata") or {}), **(previous.get("metadata") or {})}
        for redundant in matches[1:]:
            groups.remove(redundant)
    return groups
