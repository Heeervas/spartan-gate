export const SERVICE_ERRORS = new Set(['stale_workflow', 'stale_tab', 'remote_outcome_unknown', 'workflow_busy', 'unsupported_backend']);

export class ServiceError extends Error {
  constructor(code) { super(`Managed tools rejected the request (${code}).`); this.code = code; }
}
