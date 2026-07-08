from __future__ import annotations
from typing import Any
import chromadb
from chromadb.config import Settings as ChromaSettings
from app.config import get_settings


class ChromaStore:
    def __init__(self) -> None:
        settings = get_settings()
        self._path = str(settings.storage_dir / "chroma")
        self._client = chromadb.PersistentClient(
            path=self._path,
            settings=ChromaSettings(anonymized_telemetry=False))
        self._collection = self._client.get_or_create_collection(
            name="scholar_chunks",
            metadata={"hnsw:space": "cosine"})

    def index_paper(self, tenant_id: str, user_id: str, paper_id: str,
                    chunks: list[dict[str, Any]],
                    embeddings: list[list[float]]) -> int:
        if not chunks:
            return 0
        try:
            self._collection.delete(where={"paper_id": paper_id})
        except Exception:
            pass
        ids, docs, metas, embs = [], [], [], []
        for i, ch in enumerate(chunks):
            ids.append(f"{paper_id}:{i}")
            docs.append(ch.get("content", ""))
            metas.append({"tenant_id": tenant_id, "user_id": user_id,
                          "paper_id": paper_id, "chunk_index": i})
            if i < len(embeddings) and embeddings[i]:
                embs.append(embeddings[i])
        if embs and len(embs) == len(ids):
            self._collection.upsert(ids=ids, documents=docs, metadatas=metas, embeddings=embs)
        else:
            self._collection.upsert(ids=ids, documents=docs, metadatas=metas)
        return len(chunks)

    def _where(self, tenant_id: str, user_id: str) -> dict[str, Any]:
        return {"$and": [{"tenant_id": tenant_id}, {"user_id": user_id}]}

    def search(self, tenant_id: str, user_id: str, query: str,
               query_embedding: list[float] | None = None,
               limit: int = 10) -> dict[str, Any]:
        where = self._where(tenant_id, user_id)
        if query_embedding and len(query_embedding) > 0:
            results = self._collection.query(
                query_embeddings=[query_embedding], where=where,
                n_results=limit, include=["documents", "metadatas", "distances"])
        else:
            results = self._collection.query(
                query_texts=[query], where=where, n_results=limit,
                include=["documents", "metadatas", "distances"])
        items = []
        ids_list = results.get("ids", [[]])[0] or []
        docs_list = results.get("documents", [[]])[0] or []
        metas_list = results.get("metadatas", [[]])[0] or []
        dists_list = results.get("distances", [[]])[0] or []
        for i, cid in enumerate(ids_list):
            meta = metas_list[i] if i < len(metas_list) else {}
            dist = dists_list[i] if i < len(dists_list) else 1.0
            items.append({
                "chunk_id": cid,
                "paper_id": meta.get("paper_id", ""),
                "chunk_index": meta.get("chunk_index", 0),
                "content": docs_list[i] if i < len(docs_list) else "",
                "score": round(1.0 - min(dist, 1.0), 6)})
        return {"backend": "chromadb", "retrieval_mode": "hybrid", "items": items}

    def delete_paper(self, tenant_id: str, user_id: str, paper_id: str) -> None:
        try:
            self._collection.delete(where={"paper_id": paper_id})
        except Exception:
            pass

    def stats(self, tenant_id: str, user_id: str) -> dict[str, Any]:
        where = self._where(tenant_id, user_id)
        try:
            result = self._collection.get(where=where, include=["metadatas"])
            chunk_count = len(result.get("ids", []))
            paper_ids: set[str] = set()
            for meta in result.get("metadatas", []):
                pid = meta.get("paper_id", "")
                if pid:
                    paper_ids.add(pid)
            return {"chunk_count": chunk_count, "paper_count": len(paper_ids)}
        except Exception:
            return {"chunk_count": 0, "paper_count": 0}


chroma_store = ChromaStore()
