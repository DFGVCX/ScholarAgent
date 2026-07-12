import type { ScholarApiClient } from '../../api/client';
import type { SurveyTaskRequest } from '../../types/task';

export const writingApi = (client: ScholarApiClient) => ({
  create: (input: SurveyTaskRequest) => client.createSurveyTask(input),
  result: (taskId: string) => client.getTaskResult(taskId),
  approveOutline: (taskId: string, outlineMarkdown: string, comment = '') =>
    client.approveOutline(taskId, comment, outlineMarkdown),
});
