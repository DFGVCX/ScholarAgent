import type { ScholarApiClient } from '../../api/client';

export const conversationApi = (client: ScholarApiClient) => ({
  list: () => client.listConversations(),
  create: (title = '新会话') => client.createConversation(title),
  get: (conversationId: string) => client.getConversation(conversationId),
  send: (conversationId: string, content: string, skillId = 'general_assistant') =>
    client.sendMessage(conversationId, content, skillId),
});
