import type { CitationAudit } from '../../types/task';

export interface AuditViewModel {
  label: string;
  tone: 'pass' | 'fail' | 'idle';
}

export const toAuditViewModel = (audit?: CitationAudit): AuditViewModel => {
  if (!audit) return { label: '-', tone: 'idle' };
  return audit.is_valid
    ? { label: `PASS / coverage ${Math.round(audit.coverage * 100)}%`, tone: 'pass' }
    : { label: `FAIL / ${audit.hallucinated_ids.length} hallucinated`, tone: 'fail' };
};

