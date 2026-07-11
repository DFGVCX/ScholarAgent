from __future__ import annotations

import hashlib
from typing import Any
from uuid import uuid4

from agents.factory import model_factory
from app.services import mysql_store


class TranslationService:
    async def translate(
        self, *, tenant_id: str, user_id: str, paper_id: str, text: str,
        source_language: str, target_language: str, context: str = "",
    ) -> dict[str, Any]:
        normalized = " ".join(text.split()).strip()
        if not normalized:
            raise ValueError("待翻译文本不能为空")
        source_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        cached = mysql_store.get_translation(
            tenant_id, user_id, paper_id, source_hash, target_language
        )
        if cached:
            return {**cached, "cached": True, "source_hash": source_hash}

        prompt = (
            f"将下面的学术文本从 {source_language or '自动识别'} 翻译为 {target_language}。\n"
            "要求：准确保留术语、数字、公式、缩写和引用编号；表达自然；"
            "只输出译文，不添加解释、标题或 Markdown 围栏。\n"
            f"上下文（仅用于消歧）：{context[:1200]}\n\n待翻译文本：\n{normalized}"
        )
        response = await model_factory.generate_text(
            "academic_translation",
            prompt,
            {"tenant_id": tenant_id, "user_id": user_id, "paper_id": paper_id},
        )
        translated = response.content.strip()
        if not translated:
            raise RuntimeError("模型未返回译文")
        translation_id = f"trans_{uuid4().hex}"
        mysql_store.save_translation(
            translation_id=translation_id,
            tenant_id=tenant_id,
            user_id=user_id,
            paper_id=paper_id,
            source_hash=source_hash,
            source_text=normalized,
            source_language=source_language or "auto",
            target_language=target_language,
            translated_text=translated,
            provider=response.provider,
            model=response.model,
        )
        return {
            "translation_id": translation_id,
            "source_text": normalized,
            "source_language": source_language or "auto",
            "target_language": target_language,
            "translated_text": translated,
            "provider": response.provider,
            "model": response.model,
            "source_hash": source_hash,
            "cached": False,
        }


translation_service = TranslationService()
