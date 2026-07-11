from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from agents.factory import ModelResponse
from app.services.translation_service import TranslationService


class TranslationServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_translate_calls_model_and_persists_result(self):
        service = TranslationService()
        response = ModelResponse(content="这是可靠的学术译文。", provider="qwen", model="qwen-flash")
        with (
            patch("app.services.translation_service.mysql_store.get_translation", return_value=None),
            patch("app.services.translation_service.mysql_store.save_translation") as save,
            patch("app.services.translation_service.model_factory.generate_text", new=AsyncMock(return_value=response)) as generate,
        ):
            result = await service.translate(
                tenant_id="tenant_a", user_id="user_a", paper_id="paper:a",
                text="  Reliable academic translation.  ", source_language="auto", target_language="中文",
            )

        self.assertFalse(result["cached"])
        self.assertEqual(result["translated_text"], "这是可靠的学术译文。")
        self.assertEqual(result["model"], "qwen-flash")
        generate.assert_awaited_once()
        save.assert_called_once()
        self.assertEqual(save.call_args.kwargs["tenant_id"], "tenant_a")
        self.assertEqual(save.call_args.kwargs["user_id"], "user_a")

    async def test_translate_uses_tenant_scoped_cache(self):
        service = TranslationService()
        cached = {
            "translation_id": "trans_cached",
            "source_text": "Cached source",
            "source_language": "auto",
            "target_language": "中文",
            "translated_text": "缓存译文",
            "provider": "qwen",
            "model": "qwen-flash",
        }
        with (
            patch("app.services.translation_service.mysql_store.get_translation", return_value=cached) as get,
            patch("app.services.translation_service.model_factory.generate_text", new=AsyncMock()) as generate,
        ):
            result = await service.translate(
                tenant_id="tenant_a", user_id="user_a", paper_id="paper:a",
                text="Cached source", source_language="auto", target_language="中文",
            )

        self.assertTrue(result["cached"])
        self.assertEqual(result["translated_text"], "缓存译文")
        self.assertEqual(get.call_args.args[:3], ("tenant_a", "user_a", "paper:a"))
        generate.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
