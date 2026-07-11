from __future__ import annotations

import unittest
from uuid import uuid4

from app.schemas import UserContext
from app.services.memory_service import UserMemoryService


class UserMemoryServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        marker = uuid4().hex
        self.user = UserContext(tenant_id=f"tenant_{marker}", user_id=f"user_{marker}")
        self.other = UserContext(tenant_id=f"tenant_other_{marker}", user_id=f"user_other_{marker}")
        self.service = UserMemoryService()

    def test_explicit_preference_is_extracted_and_recalled_across_conversations(self):
        saved = self.service.extract_from_messages(
            self.user,
            "conversation_a",
            [{
                "message_id": "message_1",
                "role": "user",
                "content": "请记住我偏好近三年的异常检测论文，并使用 IEEE 引用格式。",
            }],
        )
        self.assertTrue(saved)

        recalled = self.service.recall(self.user, "帮我检索异常检测论文并整理引用")
        self.assertTrue(any("近三年" in item.content for item in recalled))
        self.assertTrue(any("IEEE" in item.content for item in recalled))

    def test_memory_is_tenant_and_user_scoped(self):
        self.service.remember(
            self.user,
            memory_type="profile",
            content="我的研究方向是工业异常检测",
            importance=0.95,
        )
        self.assertTrue(self.service.recall(self.user, "工业异常检测"))
        self.assertEqual(self.service.recall(self.other, "工业异常检测"), [])

    def test_forget_removes_memory_from_recall(self):
        record = self.service.remember(
            self.user,
            memory_type="instruction",
            content="后续优先使用中文回答",
            importance=0.95,
        )
        self.assertIsNotNone(record)
        self.assertTrue(self.service.forget(self.user, record.memory_id))
        self.assertFalse(any(item.memory_id == record.memory_id for item in self.service.recall(self.user, "中文回答")))


if __name__ == "__main__":
    unittest.main()
