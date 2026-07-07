CREATE DATABASE IF NOT EXISTS scholar_agent
  DEFAULT CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE scholar_agent;

CREATE TABLE IF NOT EXISTS scholar_tenants (
  tenant_id VARCHAR(64) PRIMARY KEY,
  name VARCHAR(200) NOT NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'active',
  metadata_json JSON NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS scholar_users (
  user_id VARCHAR(64) PRIMARY KEY,
  tenant_id VARCHAR(64) NOT NULL,
  username VARCHAR(120) NOT NULL,
  password_hash CHAR(64) NOT NULL,
  display_name VARCHAR(200) NOT NULL,
  roles_json JSON NULL,
  api_key VARCHAR(160) NOT NULL UNIQUE,
  status VARCHAR(32) NOT NULL DEFAULT 'active',
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_scholar_users_tenant_username (tenant_id, username),
  KEY idx_scholar_users_api_key (api_key),
  CONSTRAINT fk_scholar_users_tenant
    FOREIGN KEY (tenant_id) REFERENCES scholar_tenants(tenant_id)
    ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS scholar_tasks (
  task_id CHAR(36) PRIMARY KEY,
  tenant_id VARCHAR(64) NOT NULL,
  user_id VARCHAR(64) NOT NULL,
  status VARCHAR(32) NOT NULL,
  phase VARCHAR(80) NOT NULL,
  percent INT NOT NULL DEFAULT 0,
  trace_id VARCHAR(80) NULL,
  request_json JSON NOT NULL,
  result_json JSON NULL,
  error TEXT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_scholar_tasks_user (tenant_id, user_id, updated_at),
  KEY idx_scholar_tasks_status (tenant_id, status),
  CONSTRAINT fk_scholar_tasks_user
    FOREIGN KEY (user_id) REFERENCES scholar_users(user_id)
    ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS scholar_knowledge_papers (
  paper_id VARCHAR(260) NOT NULL,
  tenant_id VARCHAR(64) NOT NULL,
  user_id VARCHAR(64) NOT NULL,
  source VARCHAR(40) NOT NULL,
  title VARCHAR(500) NOT NULL,
  authors_json JSON NULL,
  abstract TEXT NULL,
  full_text MEDIUMTEXT NULL,
  published_at VARCHAR(40) NULL,
  doi VARCHAR(200) NULL,
  arxiv_id VARCHAR(120) NULL,
  url VARCHAR(500) NULL,
  metadata_json JSON NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (tenant_id, user_id, paper_id),
  KEY idx_scholar_knowledge_user (tenant_id, user_id, updated_at),
  KEY idx_scholar_knowledge_source (tenant_id, user_id, source),
  FULLTEXT KEY ft_scholar_knowledge_title_abstract (title, abstract),
  CONSTRAINT fk_scholar_knowledge_user
    FOREIGN KEY (user_id) REFERENCES scholar_users(user_id)
    ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS scholar_rag_chunks (
  chunk_id CHAR(64) PRIMARY KEY,
  tenant_id VARCHAR(64) NOT NULL,
  user_id VARCHAR(64) NOT NULL,
  paper_id VARCHAR(260) NOT NULL,
  chunk_index INT NOT NULL,
  content_hash CHAR(64) NOT NULL,
  content TEXT NOT NULL,
  token_count INT NOT NULL DEFAULT 0,
  keywords_json JSON NULL,
  embedding_json JSON NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_scholar_rag_chunk (tenant_id, user_id, paper_id, chunk_index),
  KEY idx_scholar_rag_paper (tenant_id, user_id, paper_id),
  FULLTEXT KEY ft_scholar_rag_content (content),
  CONSTRAINT fk_scholar_rag_paper
    FOREIGN KEY (tenant_id, user_id, paper_id)
    REFERENCES scholar_knowledge_papers(tenant_id, user_id, paper_id)
    ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS scholar_task_events (
  event_id BIGINT AUTO_INCREMENT PRIMARY KEY,
  task_id CHAR(36) NOT NULL,
  tenant_id VARCHAR(64) NOT NULL,
  user_id VARCHAR(64) NOT NULL,
  event VARCHAR(80) NOT NULL,
  phase VARCHAR(80) NOT NULL,
  message TEXT NULL,
  percent INT NOT NULL DEFAULT 0,
  payload_json JSON NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_scholar_task_events_task (tenant_id, task_id, event_id),
  KEY idx_scholar_task_events_user (tenant_id, user_id, created_at),
  CONSTRAINT fk_scholar_task_events_task
    FOREIGN KEY (task_id) REFERENCES scholar_tasks(task_id)
    ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS scholar_citation_audits (
  audit_id BIGINT AUTO_INCREMENT PRIMARY KEY,
  task_id CHAR(36) NOT NULL,
  tenant_id VARCHAR(64) NOT NULL,
  user_id VARCHAR(64) NOT NULL,
  is_valid BOOLEAN NOT NULL DEFAULT FALSE,
  found_ids_json JSON NULL,
  hallucinated_ids_json JSON NULL,
  missing_ids_json JSON NULL,
  coverage DECIMAL(8, 6) NOT NULL DEFAULT 0,
  payload_json JSON NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_scholar_citation_audits_task (tenant_id, task_id, created_at),
  CONSTRAINT fk_scholar_citation_audits_task
    FOREIGN KEY (task_id) REFERENCES scholar_tasks(task_id)
    ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS scholar_reflection_logs (
  reflection_id BIGINT AUTO_INCREMENT PRIMARY KEY,
  task_id CHAR(36) NOT NULL,
  tenant_id VARCHAR(64) NOT NULL,
  user_id VARCHAR(64) NOT NULL,
  phase VARCHAR(80) NOT NULL,
  section_id VARCHAR(120) NULL,
  review_json JSON NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_scholar_reflection_logs_task (tenant_id, task_id, created_at),
  CONSTRAINT fk_scholar_reflection_logs_task
    FOREIGN KEY (task_id) REFERENCES scholar_tasks(task_id)
    ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS scholar_user_preferences (
  tenant_id VARCHAR(64) NOT NULL,
  user_id VARCHAR(64) NOT NULL,
  preference_key VARCHAR(120) NOT NULL,
  preference_json JSON NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (tenant_id, user_id, preference_key),
  CONSTRAINT fk_scholar_user_preferences_user
    FOREIGN KEY (user_id) REFERENCES scholar_users(user_id)
    ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS scholar_trace_events (
  trace_event_id BIGINT AUTO_INCREMENT PRIMARY KEY,
  trace_id VARCHAR(120) NOT NULL,
  task_id CHAR(36) NULL,
  tenant_id VARCHAR(64) NULL,
  user_id VARCHAR(64) NULL,
  span_name VARCHAR(160) NOT NULL,
  event_type VARCHAR(80) NOT NULL,
  provider VARCHAR(80) NULL,
  model VARCHAR(160) NULL,
  latency_ms INT NULL,
  metadata_json JSON NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_scholar_trace_events_trace (trace_id, trace_event_id),
  KEY idx_scholar_trace_events_task (tenant_id, task_id, trace_event_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO scholar_tenants (tenant_id, name, metadata_json)
VALUES
  ('tenant_demo', 'Scholar Demo Lab', JSON_OBJECT('plan', 'demo')),
  ('tenant_acme', 'Acme AI Research', JSON_OBJECT('plan', 'team'))
ON DUPLICATE KEY UPDATE
  name = VALUES(name),
  metadata_json = VALUES(metadata_json);

INSERT INTO scholar_users
  (user_id, tenant_id, username, password_hash, display_name, roles_json, api_key)
VALUES
  ('user_demo', 'tenant_demo', 'demo',
   'd3ad9315b7be5dd53b31a273b3b3aba5defe700808305aa16a3062b76658a791',
   'Demo Researcher', JSON_ARRAY('tenant_admin', 'researcher'), 'demo-key'),
  ('user_acme', 'tenant_acme', 'acme',
   '459aa9b36f4c740533a5fd26a10bbe674576210c10ac6e2befcd86f8d0405c99',
   'Acme Analyst', JSON_ARRAY('researcher'), 'acme-key')
ON DUPLICATE KEY UPDATE
  username = VALUES(username),
  password_hash = VALUES(password_hash),
  display_name = VALUES(display_name),
  roles_json = VALUES(roles_json),
  api_key = VALUES(api_key),
  status = 'active';
