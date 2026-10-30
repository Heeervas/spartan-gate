export const SERVICE_ERRORS = new Set(['stale_workflow', 'stale_tab', 'remote_outcome_unknown', 'workflow_busy', 'unsupported_backend', 'egress_denied', 'capacity', 'reader_queue_full', 'reader_overloaded', 'deadline', 'cancelled', 'remote_http_error', 'response_too_large', 'extraction_failed', 'needs_render', 'upstream_error', 'unavailable']);

export class ServiceError extends Error {
  constructor(code, upstreamStatus) {
    const remote = code === 'remote_http_error' && Number.isInteger(upstreamStatus) && upstreamStatus >= 300 && upstreamStatus <= 599 ? upstreamStatus : undefined;
    super(`Managed tools rejected the request (${code}${remote ? `; upstream HTTP ${remote}` : ''}).`);
    this.code = code;
    this.upstreamStatus = remote;
  }
}
