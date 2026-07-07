export type InputType = 'pdf' | 'arxiv' | 'doi';
export type CitationStyle = 'IEEE' | 'APA' | 'GB/T 7714';

export interface SurveyTaskRequest {
  topic: string;
  input_type: InputType;
  input_value: string;
  citation_style: CitationStyle;
  max_papers: number;
  require_outline_confirmation: boolean;
}

export interface TaskCreateResponse {
  task_id: string;
  status: string;
  stream_url: string;
  trace_id?: string;
}

export interface CitationAudit {
  is_valid: boolean;
  found_ids: string[];
  hallucinated_ids: string[];
  missing_reference_ids: string[];
  coverage: number;
  suggestions?: Record<string, string[]>;
}

export interface AuthProfile {
  username?: string;
  tenant_id: string;
  tenant_name: string;
  user_id: string;
  display_name: string;
  api_key?: string;
  access_token?: string;
  token_type?: 'api-key';
  roles: string[];
}

export interface TaskRecord {
  task_id: string;
  tenant_id: string;
  user_id: string;
  status: string;
  phase: string;
  request: SurveyTaskRequest;
  percent: number;
  trace_id?: string;
  error?: string;
  result?: SurveyTaskResult;
}

export interface SurveyTaskResult {
  task_id: string;
  topic: string;
  markdown: string;
  papers: KnowledgePaper[];
  references: string[];
  citation_audit: CitationAudit;
  reflection_logs: unknown[];
}

export interface TaskAuditResponse {
  task_id: string;
  status: string;
  topic: string;
  citation_audit: CitationAudit;
  references: string[];
  papers: KnowledgePaper[];
  reflection_logs: unknown[];
  global_review?: unknown;
}

export interface KnowledgePaper {
  paper_id: string;
  tenant_id?: string;
  user_id?: string;
  source: string;
  title: string;
  authors: string[];
  abstract?: string;
  full_text?: string;
  published_at?: string;
  doi?: string;
  arxiv_id?: string;
  url?: string;
  metadata?: Record<string, unknown>;
}

export interface RagChunk {
  chunk_id: string;
  paper_id: string;
  chunk_index: number;
  title?: string;
  source?: string;
  content: string;
  token_count: number;
  score: number;
  keywords?: string[];
  embedding?: number[];
}

export interface RagStats {
  backend: 'mysql' | 'json';
  chunk_count: number;
  paper_count: number;
}

export interface RuntimeConfigItem {
  key: string;
  value: string;
  effective_value: string;
  configured: boolean;
  secret: boolean;
  options: string[];
}

export interface RuntimeConfigResponse {
  path: string;
  items: RuntimeConfigItem[];
}
