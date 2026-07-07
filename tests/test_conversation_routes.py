from __future__ import annotations

import unittest
from uuid import uuid4

from app.routes.conversations import (
    ConversationCreateDTO,
    ConversationMessageDTO,
    append_message,
    archive_conversation,
    create_conversation,
    get_conversation,
    list_conversation_skills,
    list_conversations,
)


class ConversationRoutesTest(unittest.IsolatedAsyncioTestCase):
    async def test_conversation_messages_are_tenant_scoped_and_persistent(self):
        marker = f"conversation marker {uuid4()}"
        title = f"Skill intake test {uuid4()}"
        conversation_id = None
        skills = await list_conversation_skills(x_api_key="demo-key")
        self.assertTrue(any(item["skill_id"] == "survey_review" for item in skills["items"]))

        try:
            created = await create_conversation(
                ConversationCreateDTO(
                    title=title,
                    skill_id="survey_review",
                    initial_message=marker,
                ),
                x_api_key="demo-key",
            )
            conversation_id = created["item"]["conversation_id"]
            self.assertEqual(len(created["item"]["messages"]), 2)

            appended = await append_message(
                conversation_id,
                ConversationMessageDTO(content="补充：需要先确认大纲", skill_id="survey_review"),
                x_api_key="demo-key",
            )
            self.assertEqual(len(appended["items"]), 2)

            loaded = await get_conversation(conversation_id, x_api_key="demo-key")
            contents = [message["content"] for message in loaded["item"]["messages"]]
            self.assertIn(marker, contents)
            self.assertTrue(any("写作专项" in content for content in contents))

            demo_list = await list_conversations(x_api_key="demo-key")
            self.assertTrue(any(item["conversation_id"] == conversation_id for item in demo_list["items"]))

            acme_list = await list_conversations(x_api_key="acme-key")
            self.assertFalse(any(item["conversation_id"] == conversation_id for item in acme_list["items"]))
        finally:
            if conversation_id:
                await archive_conversation(conversation_id, x_api_key="demo-key")


if __name__ == "__main__":
    unittest.main()
