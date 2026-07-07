export interface TaskStreamEvent {
  event: string;
  phase: string;
  message: string;
  percent: number;
  payload: Record<string, unknown>;
}

export const createTaskEventSource = (
  taskId: string,
  apiKey: string,
  onEvent: (event: TaskStreamEvent) => void,
): EventSource => {
  const source = new EventSource(`/tasks/${taskId}/stream?api_key=${encodeURIComponent(apiKey)}`);
  const handler = (event: MessageEvent<string>) => onEvent(JSON.parse(event.data));
  source.onmessage = handler;
  ['queued', 'progress', 'outline_required', 'completed', 'failed'].forEach((name) => {
    source.addEventListener(name, handler);
  });
  return source;
};
