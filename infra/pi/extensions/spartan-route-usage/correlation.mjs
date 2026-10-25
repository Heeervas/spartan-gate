// Telemetry labels are independent of usage registration and replay authority.
export function correlationHeaders(headers, ctx) {
  const marker = Object.keys(headers).find(key => key.toLowerCase() === 'x-spartan-pi-client');
  if (!marker || headers[marker] !== '1') return;
  for (const key of Object.keys(headers)) {
    if (key.toLowerCase() === 'x-spartan-correlation') delete headers[key];
  }
  const conversation = ctx.sessionManager.getSessionId();
  if (typeof conversation !== 'string' || conversation.length < 1 || conversation.length > 128 ||
    /[^A-Za-z0-9._:-]/.test(conversation)) return;
  headers['X-Spartan-Correlation'] = JSON.stringify({ version: 1, client: 'pi', conversation, purpose: 'other' });
}
