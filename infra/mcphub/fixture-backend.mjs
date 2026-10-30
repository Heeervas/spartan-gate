// Inert, deliberately tiny HTTP MCP peer for isolated_acceptance.py only.
// No upstream fetch, accounts or production deployment; independent execution ledger.
import http from 'node:http';
const executions = [];
http.createServer(async (req, res) => {
  res.setHeader('Content-Type', 'application/json');
  if (req.url === '/executions') { res.end(JSON.stringify(executions)); return; }
  if (req.url !== '/mcp') { res.end('proxy-fixture-ok'); return; }
  if (req.method !== 'POST') { res.writeHead(405).end(); return; }
  let raw = '';
  for await (const chunk of req) raw += chunk;
  const message = JSON.parse(raw);
  if (!('id' in message)) { res.writeHead(202).end(); return; }
  let result;
  if (message.method === 'initialize') {
    result = { protocolVersion: message.params.protocolVersion, capabilities: { tools: {} },
      serverInfo: { name: 'inert-fixture', version: '1' } };
  } else if (message.method === 'tools/list') {
    result = { tools: ['allowed', 'unlisted'].map(name => ({ name, description: 'Inert test only',
      inputSchema: { type: 'object', properties: {} } })) };
  } else if (message.method === 'tools/call') {
    executions.push(message.params.name);
    result = { content: [{ type: 'text', text: 'inert-' + message.params.name }] };
  } else { res.end(JSON.stringify({ jsonrpc: '2.0', id: message.id, error: { code: -32601, message: 'Unknown method' } })); return; }
  res.end(JSON.stringify({ jsonrpc: '2.0', id: message.id, result }));
}).listen(8080, '0.0.0.0');
