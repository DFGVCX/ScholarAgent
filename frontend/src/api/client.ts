import type {
  AuthProfile,
  KnowledgePaper,
  RagChunk,
  RagStats,
  RuntimeConfigResponse,
  SurveyTaskRequest,
  TaskAuditResponse,
  TaskCreateResponse,
  TaskRecord,
} from '../types/task';

export class ScholarApiClient {
  constructor(private readonly apiKey = '', private readonly baseUrl = '') {}

  private headers(withJson = false): HeadersInit {
    return {
      ...(withJson ? { 'Content-Type': 'application/json' } : {}),
      ...(this.apiKey ? { 'X-API-Key': this.apiKey } : {}),
    };
  }

  async login(username: string, password: string, tenantId?: string): Promise<AuthProfile> {
    const response = await fetch(`${this.baseUrl}/auth/login`, {
      method: 'POST',
      headers: this.headers(true),
      body: JSON.stringify({ username, password, tenant_id: tenantId }),
    });
    if (!response.ok) throw new Error(await response.text());
    return response.json();
  }

  async me(): Promise<AuthProfile> {
    const response = await fetch(`${this.baseUrl}/auth/me`, {
      headers: this.headers(),
    });
    if (!response.ok) throw new Error(await response.text());
    return response.json();
  }

  async createSurveyTask(payload: SurveyTaskRequest): Promise<TaskCreateResponse> {
    const response = await fetch(`${this.baseUrl}/tasks/survey`, {
      method: 'POST',
      headers: this.headers(true),
      body: JSON.stringify(payload),
    });
    if (!response.ok) throw new Error(await response.text());
    return response.json();
  }

  async listTasks(): Promise<{ items: TaskRecord[] }> {
    const response = await fetch(`${this.baseUrl}/tasks`, {
      headers: this.headers(),
    });
    if (!response.ok) throw new Error(await response.text());
    return response.json();
  }

  async getTaskResult(taskId: string): Promise<TaskRecord> {
    const response = await fetch(`${this.baseUrl}/tasks/${taskId}/result`, {
      headers: this.headers(),
    });
    if (!response.ok) throw new Error(await response.text());
    return response.json();
  }

  async getTaskAudit(taskId: string): Promise<TaskAuditResponse> {
    const response = await fetch(`${this.baseUrl}/tasks/${taskId}/audit`, {
      headers: this.headers(),
    });
    if (!response.ok) throw new Error(await response.text());
    return response.json();
  }

  async approveOutline(taskId: string, comment = ''): Promise<{ status: string; task_id: string }> {
    const response = await fetch(`${this.baseUrl}/tasks/${taskId}/outline/approve`, {
      method: 'POST',
      headers: this.headers(true),
      body: JSON.stringify({ comment }),
    });
    if (!response.ok) throw new Error(await response.text());
    return response.json();
  }

  async listKnowledge(query = '', source = 'local'): Promise<{ items: KnowledgePaper[] }> {
    const params = new URLSearchParams({ query, source, limit: '80' });
    const response = await fetch(`${this.baseUrl}/knowledge?${params}`, {
      headers: this.headers(),
    });
    if (!response.ok) throw new Error(await response.text());
    return response.json();
  }

  async saveKnowledge(paper: Partial<KnowledgePaper> & Pick<KnowledgePaper, 'title' | 'source'>): Promise<{ item: KnowledgePaper; rag: RagStats }> {
    const response = await fetch(`${this.baseUrl}/knowledge`, {
      method: 'POST',
      headers: this.headers(true),
      body: JSON.stringify(paper),
    });
    if (!response.ok) throw new Error(await response.text());
    return response.json();
  }

  async deleteKnowledge(paperId: string, confirmationToken = ''): Promise<unknown> {
    const params = confirmationToken ? `?confirmation_token=${encodeURIComponent(confirmationToken)}` : '';
    const response = await fetch(`${this.baseUrl}/knowledge/${encodeURIComponent(paperId)}${params}`, {
      method: 'DELETE',
      headers: this.headers(),
    });
    if (!response.ok) throw new Error(await response.text());
    return response.json();
  }

  async searchRag(query = '', limit = 10): Promise<{ backend: string; items: RagChunk[] }> {
    const params = new URLSearchParams({ query, limit: String(limit) });
    const response = await fetch(`${this.baseUrl}/knowledge/rag/search?${params}`, {
      headers: this.headers(),
    });
    if (!response.ok) throw new Error(await response.text());
    return response.json();
  }

  async getRagStats(): Promise<RagStats> {
    const response = await fetch(`${this.baseUrl}/knowledge/rag/stats`, {
      headers: this.headers(),
    });
    if (!response.ok) throw new Error(await response.text());
    return response.json();
  }

  async getRuntimeConfig(): Promise<{ config: RuntimeConfigResponse; profile: AuthProfile }> {
    const response = await fetch(`${this.baseUrl}/settings/runtime`, {
      headers: this.headers(),
    });
    if (!response.ok) throw new Error(await response.text());
    return response.json();
  }

  async updateRuntimeConfig(values: Record<string, string>): Promise<{ status: string; config: RuntimeConfigResponse }> {
    const response = await fetch(`${this.baseUrl}/settings/runtime`, {
      method: 'PUT',
      headers: this.headers(true),
      body: JSON.stringify({ values }),
    });
    if (!response.ok) throw new Error(await response.text());
    return response.json();
  }

  async bootstrapMysql(payload: { admin_url: string; mysql_url?: string; seed_rag?: boolean }): Promise<unknown> {
    const response = await fetch(`${this.baseUrl}/settings/mysql/bootstrap`, {
      method: 'POST',
      headers: this.headers(true),
      body: JSON.stringify(payload),
    });
    if (!response.ok) throw new Error(await response.text());
    return response.json();
  }

  async getAnnotations(paperId: string): Promise<{ paper_id: string; strokes: unknown[]; notes: string }> {
    const response = await fetch(`${this.baseUrl}/knowledge/files/${encodeURIComponent(paperId)}/annotations`, {
      headers: this.headers(),
    });
    if (!response.ok) throw new Error(await response.text());
    return response.json();
  }

  async saveAnnotations(paperId: string, strokes: unknown[], notes: string): Promise<{ saved: boolean; paper_id: string }> {
    const response = await fetch(`${this.baseUrl}/knowledge/files/${encodeURIComponent(paperId)}/annotations`, {
      method: 'POST',
      headers: this.headers(true),
      body: JSON.stringify({ strokes, notes }),
    });
    if (!response.ok) throw new Error(await response.text());
    return response.json();
  }

  async getPdfInfo(paperId: string): Promise<{ paper_id: string; pages: number; file_size: number; file_name: string }> {
    const response = await fetch(`${this.baseUrl}/knowledge/files/${encodeURIComponent(paperId)}/pdf-info`, {
      headers: this.headers(),
    });
    if (!response.ok) throw new Error(await response.text());
    return response.json();
  }
}
