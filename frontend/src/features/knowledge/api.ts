import type { ScholarApiClient } from '../../api/client';

export const knowledgeApi = (client: ScholarApiClient) => ({
  list: () => client.listKnowledge(),
  search: (query: string) => client.listKnowledge(query),
});
