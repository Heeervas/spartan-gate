// Inert Camofox protocol peer for browser_profiles_acceptance.py. No browser.
import http from 'node:http';
const executions = [];
http.createServer(async (req, res) => {
  res.setHeader('Content-Type', 'application/json');
  if (req.url === '/executions') { res.end(JSON.stringify(executions)); return; }
  if (req.headers.authorization !== 'Bearer synthetic-browser-key') { res.writeHead(401); res.end('{}'); return; }
  executions.push({ method: req.method, path: req.url });
  if (req.url === '/health') { res.end('{"ok":true,"browserGeneration":1}'); return; }
  if (req.url === '/spartan/capabilities') {
    res.end('{"nonDestructiveTabCreate":true,"singleAttemptBrowserClick":true,"singleAttemptBrowserUpload":true}'); return;
  }
  if (req.method === 'DELETE' && req.url.startsWith('/sessions/')) { res.end('{"ok":true}'); return; }
  res.writeHead(404); res.end('{}');
}).listen(8082, '0.0.0.0');
