const fs = require('node:fs');

const helper = `// SPARTAN_SINGLE_ATTEMPT_UPLOAD_V1
async function spartanUploadOnce(tabState, paths, ref, selector) {
  const invalid = () => Object.assign(new Error('Select one exact file input'), { statusCode: 422 });
  if ((typeof ref === 'string' && !!ref) === (typeof selector === 'string' && !!selector)) throw invalid();
  let locator;
  if (ref) {
    const info = tabState.refs.get(ref);
    if (!info || typeof info.name !== 'string' || !Number.isInteger(info.nth) || info.nth < 0) throw invalid();
    let scope = tabState.page;
    if (info.frameName || info.frameUrl) {
      const frames = tabState.page.frames().filter(frame =>
        (!info.frameName || frame.name() === info.frameName) &&
        (!info.frameUrl || frame.url() === info.frameUrl));
      if (frames.length !== 1) throw invalid();
      scope = frames[0];
    }
    locator = scope.getByRole(info.role, { name: info.name, exact: true }).nth(info.nth);
  } else {
    const error = selectorValidationError(selector);
    if (error) throw invalid();
    locator = tabState.page.locator(selector);
  }
  if (await locator.count() !== 1 || !await locator.evaluate(element =>
    element.tagName === 'INPUT' && element.type === 'file')) throw invalid();
  await locator.setInputFiles(paths, { timeout: UPLOAD_INPUT_TIMEOUT_MS });
  tabState.refs = new Map();
  return { ok: true, attached: paths, via: 'single_attempt_input', refsAvailable: false };
}

`;
const branch = `      if (req.body.singleAttempt === true) {
        return spartanUploadOnce(tabState, paths, ref, selector);
      }
`;

function replaceOnce(source, old, next) {
  if (source.includes(next)) return source;
  if (source.split(old).length !== 2) throw new Error('Camofox managed-upload anchor changed');
  return source.replace(old, next);
}

function patchManagedUpload(source) {
  const anchor = "app.post('/tabs/:tabId/upload', async (req, res) => {";
  const end = "app.post('/tabs/:tabId/type', async (req, res) => {";
  const start = source.indexOf(anchor); const stop = source.indexOf(end, start);
  if (start < 0 || stop < 0) throw new Error('Camofox managed-upload route missing');
  let route = source.slice(start, stop);
  const input = '      const directInput = tabState.page.locator(\'input[type="file"]\').first();';
  route = replaceOnce(route, input, branch + input);
  route = replaceOnce(route,
    "    log('info', 'uploaded', { reqId: req.reqId, tabId, attached: paths, via: result.via });",
    "    log('info', 'uploaded', req.body.singleAttempt === true ? { reqId: req.reqId, tabId, count: paths.length, via: result.via } : { reqId: req.reqId, tabId, attached: paths, via: result.via });");
  route = replaceOnce(route,
    "    pluginEvents.emit('tab:upload', { userId, tabId, paths });",
    "    pluginEvents.emit('tab:upload', req.body.singleAttempt === true ? { userId, tabId, count: paths.length } : { userId, tabId, paths });");
  route = replaceOnce(route,
    "    log('error', 'upload failed', { reqId: req.reqId, tabId, error: err.message });",
    "    log('error', 'upload failed', req.body.singleAttempt === true ? { reqId: req.reqId, tabId, code: 'managed_upload_failed' } : { reqId: req.reqId, tabId, error: err.message });");
  source = source.slice(0, start) + route + source.slice(stop);
  source = replaceOnce(source, anchor, helper + anchor);
  source = replaceOnce(source, '    singleAttemptBrowserClick: true,\n',
    '    singleAttemptBrowserClick: true,\n    singleAttemptBrowserUpload: true,\n');
  if (source.split(helper).length !== 2 || source.split(branch).length !== 2) throw new Error('Incomplete managed-upload patch');
  return source;
}

module.exports = { patchManagedUpload };
if (require.main === module) {
  const source = fs.readFileSync('server.js', 'utf8');
  fs.writeFileSync('server.js', patchManagedUpload(source));
}
