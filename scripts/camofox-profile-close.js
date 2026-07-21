const key = process.env.CAMOFOX_HELPER_KEY || '';
const userId = process.env.CAMOFOX_PROFILE_USER_ID;
const sessionKey = 'spartan-operator-login';
const base = 'http://127.0.0.1:9377';

async function request(path, options = {}) {
  const headers = Object.assign({}, options.headers || {}, { Authorization: `Bearer ${key}` });
  if (options.body && !headers['Content-Type']) headers['Content-Type'] = 'application/json';
  const response = await fetch(`${base}${path}`, Object.assign({}, options, { headers }));
  const text = await response.text();
  if (!response.ok) throw new Error(`${options.method || 'GET'} failed with HTTP ${response.status}`);
  try { return text ? JSON.parse(text) : null; } catch { return text; }
}

(async () => {
  const listed = await request(`/tabs?userId=${encodeURIComponent(userId)}`);
  const operatorTabs = (listed?.tabs || []).filter(
    tab => tab?.listItemId === sessionKey && typeof tab?.tabId === 'string' && tab.tabId,
  );
  if (operatorTabs.length > 0) {
    const checkpoint = await request(
      `/internal/persistence/${encodeURIComponent(userId)}/checkpoint`,
      { method: 'POST' },
    );
    if (checkpoint?.persisted !== true) throw new Error('operator profile checkpoint failed');
  }
  for (const tab of operatorTabs) {
    await request(`/tabs/${encodeURIComponent(tab.tabId)}`, {
      method: 'DELETE',
      body: JSON.stringify({ userId }),
    });
  }
  console.log(JSON.stringify({ ok: true, closed: operatorTabs.length }));
})().catch(error => {
  console.error(error?.message || 'operator tab close failed');
  process.exit(1);
});
