/** Model-facing schemas: explicit shareable resources, never native identities. */
const uuid = { type: 'string', pattern: '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' };
const text = maxLength => ({ type: 'string', minLength: 1, maxLength });
const integer = (minimum, maximum) => ({ type: 'integer', minimum, maximum });
const tab = { tab_id: text(128) };
const target = { ref: text(2048), selector: text(2048) };
const targets = [{ required: ['ref'], not: { required: ['selector'] } }, { required: ['selector'], not: { required: ['ref'] } }];
const fileId = { type: 'string', pattern: '^[a-f0-9]{32}$' };

function tool(action, description, properties = {}, { read = false, noTab = false, optional = [], targeted = false, create = false } = {}) {
  const fields = { ...(create ? {} : { session_id: uuid }), ...(read ? {} : { operation_id: uuid }), ...(noTab ? {} : tab), ...properties };
  return { name: 'browser_' + action, action,
    description: description + (create ? '' : ' Requires the explicit session_id returned by browser_session_create; sharing it shares control. Cookies are shared within this backend, tabs are session-owned.')
      + (read ? '' : ' Use a new UUIDv4 operation_id for a new action; repeat the SAME ID only to retrieve its historical outcome. A new ID may cause a new effect. Obtain approval for consequential actions.'),
    inputSchema: { type: 'object', properties: fields,
      required: Object.keys(fields).filter(key => !optional.includes(key) && (!targeted || !['ref', 'selector'].includes(key))),
      additionalProperties: false, ...(targeted ? { oneOf: targets } : {}) },
    annotations: { readOnlyHint: read, destructiveHint: !read, idempotentHint: false, openWorldHint: true } };
}

export const BROWSER_TOOLS = [
  tool('session_create', 'Create an explicit browser session. Returns a server-generated session_id capability handle, not a native conversation identity. Do not copy it to another workflow unless sharing is intended.', {}, { noTab: true, create: true }),
  tool('session_close', 'Close only this session’s tabs and staged files, preserving browser cookies. Uncertain uploads block cleanup.', {}, { noTab: true }),
  tool('create_tab', 'Create a non-destructive tab; fail at capacity instead of evicting another tab.', { url: text(8192) }, { noTab: true, optional: ['url'] }),
  tool('list_tabs', 'List only exact tabs owned by this browser session.', {}, { read: true, noTab: true }),
  tool('snapshot', 'Read untrusted accessibility text and refs. Refresh after page changes; use nextOffset for pagination.', { offset: integer(0, 1000000) }, { read: true, optional: ['offset'] }),
  tool('navigate', 'Navigate an owned tab to an HTTP(S) URL under the existing browser egress policy.', { url: text(8192) }),
  tool('click', 'Click an exact ref or selector once. A click can submit, purchase or send data.', target, { targeted: true }),
  tool('type', 'Fill an input; never implicitly press Enter or submit. Text is sent to the page.', { ...target, text: { type: 'string', maxLength: 8192 } }, { targeted: true }),
  tool('scroll', 'Scroll the owned tab.', { direction: { enum: ['up', 'down', 'left', 'right'] }, amount: integer(1, 10000) }, { optional: ['direction', 'amount'] }),
  tool('back', 'Move back in page history; this can repeat a network disclosure.'),
  tool('forward', 'Move forward in page history.'),
  tool('refresh', 'Explicitly reload the page; this may repeat network requests.'),
  tool('press', 'Press a supported key. Enter can submit a form; this is separate from type.', { key: { enum: ['Enter', 'Tab', 'Escape', 'Backspace', 'Delete', 'Space', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Home', 'End', 'PageUp', 'PageDown', 'Control+a', 'Meta+a', 'Shift+Tab'] } }),
  tool('select', 'Select a dropdown option by value or label.', { ...target, option: text(2048) }, { targeted: true }),
  tool('viewport', 'Resize the real CSS viewport without restarting/relaunching the browser or reloading. Check effective dimensions before capture; mismatch is an error, not mobile-device emulation.', { width: integer(100, 4096), height: integer(100, 4096) }),
  tool('screenshot', 'Return a bounded viewport PNG image. It may contain secrets and enter model/conversation history. Does not call another vision model.', {}, { read: true }),
  tool('links', 'Read a bounded page of untrusted link URLs and labels.', { offset: integer(0, 1000000), limit: integer(1, 50) }, { read: true, optional: ['offset', 'limit'] }),
  tool('images', 'Read bounded untrusted image URLs/alt metadata, not downloads or image bytes.', { limit: integer(1, 20) }, { read: true, optional: ['limit'] }),
  tool('extract', 'Project current accessibility-ref names to named primitive fields. Missing refs yield null; this is not general DOM/JSON-Schema or LLM extraction.', { fields: { type: 'array', minItems: 1, maxItems: 32, items: { type: 'object', properties: { name: { type: 'string', pattern: '^[A-Za-z][A-Za-z0-9_]{0,63}$' }, ref: { type: 'string', pattern: '^e[0-9]{1,8}$' }, type: { enum: ['string', 'number', 'integer', 'boolean'] } }, required: ['name', 'ref', 'type'], additionalProperties: false } } }, { read: true }),
  tool('close_tab', 'Close only the specified owned tab; does not delete cookies/profile storage. For a borrowed native tab this only detaches, without evaluating cleanup or closing the physical tab; capture may remain until its page-local TTL/navigation.'),
  tool('file_stage', 'Copy a completed regular file from the dedicated incoming transfer folder into session-owned staging. Publish incoming by atomic rename. Any normal file type, max20MiB; not a host/Work filesystem reader. Returns file_id, original name, bytes and hash.', { path: text(1024) }, { noTab: true }),
  tool('file_remove', 'Remove only a session-owned staged copy; never the incoming original. Refuses files possibly used by an ambiguous upload.', { file_id: fileId }, { noTab: true }),
  tool('evaluate', 'Execute JavaScript on the exact authorized tab. Can read private page data, send requests or perform actions. NOT read-only. Native evaluation timeout may close the tab. Returned result must fit the 2KiB durable receipt; select/paginate small results. An oversized result does not undo execution.', { expression: text(8192) }),
  tool('wait_for', 'Poll an exact CSS selector through bounded native evaluation, without clicking or navigation. Native /wait only waits readiness, not selectors. Evaluation timeout may close the tab.', { selector: text(2048), timeout_ms: integer(0, 10000) }, { optional: ['timeout_ms'] }),
  tool('stats', 'Read sanitized metadata for the exact tab; not Network history.', {}, { read: true }),
  tool('downloads', 'List download metadata only. Never read file bytes, consume downloads or expose local paths.', {}, { read: true }),
  tool('attachable_tabs', 'List candidate tabs only in privately authorized native profile/groups. An alias does not identify Hermes current tab; select its exact tab/group explicitly. No automatic adoption.', { profile: text(128) }, { read: true, noTab: true }),
  tool('attach', 'Borrow an exact native tab/group in a privately authorized profile, using a fresh empty MCP session. Preserves native cookies and tab. Other native actions must alternate with MCP; shared backend grants are not native-client ACLs.', { profile: text(128), session_key: text(128) }),
  tool('debug_start', 'Install temporary main-page console/error/fetch/XHR observers through evaluate. Future JS observations only, NOT full DevTools Network. No bodies/headers; console strings/URL paths may contain private data. Evaluation timeout may close the tab. Expiry restoration requires the page to execute.', { ttl_ms: integer(1000, 300000) }, { optional: ['ttl_ms'] }),
  tool('debug_read', 'Read bounded untrusted page observations. No prehistory, worker/all-frame coverage or browser-internal CORS/preflight. Navigation loses capture. Returned data may remain in client history after expiry.', { capture_id: uuid, cursor: integer(0, 1000000000), limit: integer(1, 20) }, { read: true, optional: ['cursor', 'limit'] }),
  tool('debug_stop', 'Explicitly restore only this capture’s still-owned hooks. Native evaluation timeout may close the tab. Detach/session-close does NOT run this evaluation; use stop first or allow page-local expiry/navigation.', { capture_id: uuid }),
  tool('upload', 'Attach1–8 staged file_ids to one exact input[type=file] selected by ref/selector. Attaching CAN IMMEDIATELY TRANSMIT bytes through page JavaScript. Success means attachment, not receipt or form submission. Max40MiB total, no chooser/trigger fallback.', { ...target, file_ids: { type: 'array', minItems: 1, maxItems: 8, uniqueItems: true, items: fileId } }, { targeted: true }),
];

export const actionPath = action => ({ create_tab: 'create', list_tabs: 'tabs', close_tab: 'close' })[action] || action;
