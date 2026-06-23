import { afterAll, afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Hono } from 'hono';
import { createHash } from 'node:crypto';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { ClawRouteConfig, TaskTier } from '../src/types.js';
import { resetModelRegistry } from '../src/models.js';
import {
    closeDb,
    initDb,
    logRouting,
    setCodexActivationCheckpoint,
    upsertCodexUsageSnapshots,
} from '../src/logger.js';

const ADMIN_TOKEN = 'admin-secret';
const UNAUTHORIZED = {
    error: {
        message: 'Unauthorized. Provide Bearer token in Authorization header or token query param.',
        type: 'authentication_error',
        code: 'unauthorized',
    },
};

const mockFetch = vi.fn();
global.fetch = mockFetch;
const tempDirs: string[] = [];

function hashAccountKey(accountId: string): string {
    return createHash('sha256').update(accountId).digest('hex').slice(0, 16);
}

function createTestConfig(authToken = ADMIN_TOKEN): ClawRouteConfig {
    return {
        enabled: true,
        dryRun: false,
        baselineModel: 'openai/gpt-5.2',
        providerProfile: null,
        proxyPort: 18799,
        proxyHost: '127.0.0.1',
        authToken,
        classification: { conservativeMode: true, minConfidence: 0.7, toolAwareRouting: true },
        escalation: { enabled: true, maxRetries: 2, retryDelayMs: 10, onlyRetryBeforeStreaming: true, onlyRetryWithoutToolCalls: true, alwaysFallbackToOriginal: true },
        models: {
            [TaskTier.HEARTBEAT]: { primary: 'google/gemini-2.5-flash-lite', fallback: 'deepseek/deepseek-chat' },
            [TaskTier.SIMPLE]: { primary: 'deepseek/deepseek-chat', fallback: 'google/gemini-2.5-flash' },
            [TaskTier.MODERATE]: { primary: 'google/gemini-2.5-flash', fallback: 'openai/gpt-5-mini' },
            [TaskTier.COMPLEX]: { primary: 'anthropic/claude-sonnet-4-6', fallback: 'openai/gpt-5.2' },
            [TaskTier.FRONTIER_SONNET]: { primary: 'anthropic/claude-sonnet-4-6', fallback: 'openai/gpt-5' },
            [TaskTier.FRONTIER_OPUS]: { primary: 'anthropic/claude-opus-4-6', fallback: 'openai/o3' },
        },
        logging: { dbPath: ':memory:', logContent: false, logSystemPrompts: false, debugMode: false, retentionDays: 30 },
        dashboard: { enabled: true },
        overrides: { globalForceModel: null, sessions: {} },
        apiKeys: { anthropic: 'test-key', openai: 'test-key', codex: 'test-key', google: 'test-key', deepseek: 'test-key', openrouter: '', ollama: '', 'x-ai': '', stepfun: '' },
        alerts: {},
    } as ClawRouteConfig;
}

function authHeaders(token = ADMIN_TOKEN): HeadersInit {
    return { Authorization: `Bearer ${token}` };
}

function makeTempDir(): string {
    const dir = mkdtempSync(join(tmpdir(), 'codex-usage-api-'));
    tempDirs.push(dir);
    return dir;
}

function writeAuth(dir: string, name: string, accessToken: string, accountId: string): string {
    const path = join(dir, name);
    writeFileSync(path, JSON.stringify({ tokens: { access_token: accessToken, account_id: accountId } }));
    return path;
}

function authHeader(init?: RequestInit): string {
    return String((init?.headers as Record<string, string> | undefined)?.Authorization ?? '');
}

function usageResponse(accountId: string, usedPercent: number): Response {
    return new Response(JSON.stringify({
        account_id: accountId,
        primary_window: { limit_window_seconds: 18_000, used_percent: usedPercent, resets_at: 1_800_018_000 },
    }), { status: 200, headers: { 'Content-Type': 'application/json' } });
}

function resetCreditsResponse(availableCount = 0): Response {
    return new Response(JSON.stringify({
        available_count: availableCount,
        credits: availableCount > 0
            ? [{
                id: 'raw-reset-credit-id',
                status: 'available',
                reset_type: 'codex',
                title: 'Referral reset',
                grant_date: '2026-06-11T10:00:00.000Z',
                expiry_date: '2026-07-11T10:00:00.000Z',
            }]
            : [],
    }), { status: 200, headers: { 'Content-Type': 'application/json' } });
}

function isResetCreditsUrl(url: unknown): boolean {
    return String(url).includes('/backend-api/wham/rate-limit-reset-credits');
}

function isUsageUrl(url: unknown): boolean {
    return String(url).includes('/backend-api/wham/usage');
}

function expiredJwt(): string {
    const header = Buffer.from(JSON.stringify({ alg: 'none', typ: 'JWT' })).toString('base64url');
    const payload = Buffer.from(JSON.stringify({ exp: 1 })).toString('base64url');
    return `${header}.${payload}.signature`;
}

function futureJwt(): string {
    const header = Buffer.from(JSON.stringify({ alg: 'none', typ: 'JWT' })).toString('base64url');
    const payload = Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })).toString('base64url');
    return `${header}.${payload}.signature`;
}

async function createTestApp(authToken = ADMIN_TOKEN): Promise<Hono> {
    resetModelRegistry();
    const { resetRotationState } = await import('../src/codex-transport.js');
    resetRotationState();
    const { createApp } = await import('../src/server.js');
    const config = createTestConfig(authToken);
    await initDb(config);
    return createApp(config);
}

beforeEach(() => {
    mockFetch.mockReset();
    vi.stubEnv('OPENAI_CODEX_AUTH_PATHS', '');
    vi.stubEnv('OPENAI_CODEX_AUTH_PATH', '');
    vi.stubEnv('OPENAI_CODEX_TOKEN', '');
    vi.stubEnv('CODEX_HOME', '');
});

afterEach(() => {
    closeDb();
    vi.useRealTimers();
    vi.unstubAllEnvs();
    while (tempDirs.length > 0) rmSync(tempDirs.pop()!, { recursive: true, force: true });
});

afterAll(() => {
    vi.restoreAllMocks();
});

describe('GET /api/codex/usage', () => {
    it('keeps the route behind bearer auth and returns partial sanitized JSON when one slot times out after authorization', async () => {
        const dir = makeTempDir();
        const firstPath = writeAuth(dir, 'first.json', 'token-first', 'acct-live');
        const secondPath = writeAuth(dir, 'second.json', 'token-second', 'acct-timeout');
        vi.stubEnv('OPENAI_CODEX_AUTH_PATHS', `${firstPath},${secondPath}`);
        mockFetch.mockImplementation(async (url: string, init?: RequestInit) => {
            if (authHeader(init) === 'Bearer token-first' && isUsageUrl(url)) return usageResponse('acct-live', 17);
            if (authHeader(init) === 'Bearer token-first' && isResetCreditsUrl(url)) return resetCreditsResponse(1);
            if (authHeader(init) === 'Bearer token-second' && isUsageUrl(url)) throw new Error('slot 1 timed out');
            throw new Error(`Unexpected Authorization header: ${authHeader(init)}`);
        });
        const app = await createTestApp();

        const missing = await app.request('/api/codex/usage');
        expect(missing.status).toBe(401);
        expect(await missing.json()).toEqual(UNAUTHORIZED);

        const response = await app.request('/api/codex/usage', { headers: authHeaders() });
        expect(response.status).toBe(200);
        const body = await response.json();
        expect(body).toMatchObject({
            partial: true,
            accounts: [expect.objectContaining({
                accountKey: hashAccountKey('acct-live'),
                slotIndex: 0,
                resetCredits: expect.objectContaining({ availableCount: 1 }),
            })],
            slotErrors: [expect.objectContaining({ slotIndex: 1, message: expect.stringMatching(/timed out/i) })],
            cacheUsage: expect.objectContaining({ periodHours: 24 }),
            cacheUsageRecent: expect.objectContaining({ periodHours: 1 / 6 }),
        });
        expect(JSON.stringify(body)).not.toContain('raw-reset-credit-id');
        expect(mockFetch.mock.calls.some(([url]) => String(url).includes('/consume'))).toBe(false);
    });

    it('returns sanitized dashboard JSON without auth when CLAWROUTE_TOKEN is unset', async () => {
        const dir = makeTempDir();
        const authPath = writeAuth(dir, 'auth.json', 'token-open', 'acct-open');
        vi.stubEnv('OPENAI_CODEX_AUTH_PATHS', authPath);
        mockFetch.mockImplementation(async (url: string) => isResetCreditsUrl(url)
            ? resetCreditsResponse()
            : usageResponse('acct-open', 29));
        const app = await createTestApp('');

        const response = await app.request('/api/codex/usage');
        expect(response.status).toBe(200);
        const body = await response.json();
        expect(body).toMatchObject({
            partial: false,
            accounts: [expect.objectContaining({ accountKey: hashAccountKey('acct-open'), slotIndex: 0 })],
        });
    });

    it('keeps an expired non-primary slot dormant in balancer mode on', async () => {
        vi.useFakeTimers();
        vi.setSystemTime(new Date('2026-06-08T10:00:00.000Z'));
        const dir = makeTempDir();
        const firstPath = writeAuth(dir, 'first.json', 'token-first', 'acct-main');
        const secondPath = writeAuth(dir, 'second.json', 'token-second', 'acct-dormant');
        vi.stubEnv('OPENAI_CODEX_AUTH_PATHS', `${firstPath},${secondPath}`);
        vi.stubEnv('CODEX_BALANCE_LOADER_MODE', 'on');
        vi.stubEnv('CODEX_EARLY_ACTIVATION_ENABLED', 'false');
        mockFetch.mockImplementation(async (url: string, init?: RequestInit) => {
            if (authHeader(init) === 'Bearer token-first' && isUsageUrl(url)) return usageResponse('acct-main', 17);
            if (authHeader(init) === 'Bearer token-first' && isResetCreditsUrl(url)) return resetCreditsResponse();
            if (authHeader(init) === 'Bearer token-second' && isUsageUrl(url)) return usageResponse('acct-dormant', 22);
            if (authHeader(init) === 'Bearer token-second' && isResetCreditsUrl(url)) return resetCreditsResponse();
            throw new Error(`Unexpected Authorization header: ${authHeader(init)}`);
        });
        const app = await createTestApp();
        setCodexActivationCheckpoint({
            slotIndex: 1,
            accountKey: hashAccountKey('acct-dormant'),
            expectedWeeklyResetAt: '2026-06-07T12:00:00.000Z',
            lastUsageCheckAt: '2026-05-31T12:00:00.000Z',
            updatedAt: '2026-05-31T12:00:00.000Z',
        });

        const response = await app.request('/api/codex/usage', { headers: authHeaders() });

        expect(response.status).toBe(200);
        expect(mockFetch).toHaveBeenCalledTimes(2);
        expect(authHeader(mockFetch.mock.calls[0]?.[1])).toBe('Bearer token-first');
        expect(mockFetch.mock.calls.map(([url]) => String(url))).toEqual([
            expect.stringContaining('/backend-api/wham/usage'),
            expect.stringContaining('/backend-api/wham/rate-limit-reset-credits'),
        ]);
    });

    it('forces a manual usage check for a dormant slot', async () => {
        const dir = makeTempDir();
        const firstPath = writeAuth(dir, 'first.json', 'token-first', 'acct-main');
        const secondPath = writeAuth(dir, 'second.json', 'token-second', 'acct-manual');
        vi.stubEnv('OPENAI_CODEX_AUTH_PATHS', `${firstPath},${secondPath}`);
        vi.stubEnv('CODEX_BALANCE_LOADER_MODE', 'on');
        mockFetch.mockImplementation(async (url: string, init?: RequestInit) => {
            if (authHeader(init) === 'Bearer token-second' && isUsageUrl(url)) return usageResponse('acct-manual', 31);
            if (authHeader(init) === 'Bearer token-second' && isResetCreditsUrl(url)) return resetCreditsResponse();
            throw new Error(`Unexpected Authorization header: ${authHeader(init)}`);
        });
        const app = await createTestApp();

        const response = await app.request('/api/codex/usage/slots/1', {
            method: 'POST',
            headers: authHeaders(),
        });

        expect(response.status).toBe(200);
        expect(mockFetch).toHaveBeenCalledTimes(2);
        expect(authHeader(mockFetch.mock.calls[0]?.[1])).toBe('Bearer token-second');
    });

    it('checks usage with existing credentials without invoking OAuth refresh', async () => {
        const dir = makeTempDir();
        const authPath = join(dir, 'auth.json');
        writeFileSync(authPath, JSON.stringify({
            tokens: {
                access_token: futureJwt(),
                refresh_token: 'refresh-must-not-be-used',
                account_id: 'acct-read-only-usage',
            },
        }));
        vi.stubEnv('OPENAI_CODEX_AUTH_PATHS', authPath);
        mockFetch.mockImplementation(async (url: string, init?: RequestInit) => {
            if (isUsageUrl(url)) return usageResponse('acct-read-only-usage', 2);
            if (isResetCreditsUrl(url)) {
                expect((init?.headers as Record<string, string>)['OpenAI-Beta']).toBe('codex-1');
                expect((init?.headers as Record<string, string>)['originator']).toBe('Codex Desktop');
                expect((init?.headers as Record<string, string>)['ChatGPT-Account-ID']).toBe('acct-read-only-usage');
                return resetCreditsResponse(1);
            }
            throw new Error(`Unexpected URL: ${url}`);
        });
        const app = await createTestApp();

        const response = await app.request('/api/codex/usage/slots/0', {
            method: 'POST',
            headers: authHeaders(),
        });

        expect(response.status).toBe(200);
        expect(mockFetch).toHaveBeenCalledTimes(2);
        expect(await response.json()).toMatchObject({
            accounts: [expect.objectContaining({
                slotIndex: 0,
                fiveHour: expect.objectContaining({ usedPercent: 2 }),
                resetCredits: expect.objectContaining({ availableCount: 1 }),
            })],
        });
    });

    it('reloads slot identity and usage credentials when an auth file is replaced', async () => {
        const dir = makeTempDir();
        const authPath = writeAuth(dir, 'auth.json', 'token-old', 'acct-old');
        vi.stubEnv('OPENAI_CODEX_AUTH_PATHS', authPath);
        vi.stubEnv('CODEX_BALANCE_LOADER_MODE', 'on');
        mockFetch.mockImplementation(async (url: string, init?: RequestInit) => {
            if (authHeader(init) === 'Bearer token-old' && isUsageUrl(url)) return usageResponse('acct-old', 44);
            if (authHeader(init) === 'Bearer token-old' && isResetCreditsUrl(url)) return resetCreditsResponse();
            if (authHeader(init) === 'Bearer token-new' && isUsageUrl(url)) return usageResponse('acct-new', 11);
            if (authHeader(init) === 'Bearer token-new' && isResetCreditsUrl(url)) return resetCreditsResponse();
            throw new Error(`Unexpected Authorization header: ${authHeader(init)}`);
        });
        const app = await createTestApp();

        const firstUsage = await app.request('/api/codex/usage/slots/0', {
            method: 'POST',
            headers: authHeaders(),
        });
        expect(firstUsage.status).toBe(200);
        expect(await firstUsage.json()).toMatchObject({
            accounts: [expect.objectContaining({
                accountKey: hashAccountKey('acct-old'),
                fiveHour: expect.objectContaining({ usedPercent: 44 }),
            })],
        });

        writeFileSync(authPath, JSON.stringify({
            tokens: { access_token: 'token-new', account_id: 'acct-new' },
        }));

        const balancer = await app.request('/api/codex/balancer', { headers: authHeaders() });
        expect(balancer.status).toBe(200);
        expect(await balancer.json()).toMatchObject({
            slots: [expect.objectContaining({
                slotIndex: 0,
                accountKey: hashAccountKey('acct-new'),
            })],
        });

        const secondUsage = await app.request('/api/codex/usage/slots/0', {
            method: 'POST',
            headers: authHeaders(),
        });
        expect(secondUsage.status).toBe(200);
        expect(await secondUsage.json()).toMatchObject({
            accounts: [expect.objectContaining({
                accountKey: hashAccountKey('acct-new'),
                fiveHour: expect.objectContaining({ usedPercent: 11 }),
            })],
        });
        expect(mockFetch.mock.calls.map(([, init]) => authHeader(init)).filter(Boolean)).toEqual([
            'Bearer token-old',
            'Bearer token-old',
            'Bearer token-new',
            'Bearer token-new',
        ]);
    });

    it('does not call usage upstream with an expired manual-check token', async () => {
        const dir = makeTempDir();
        const authPath = join(dir, 'expired.json');
        writeFileSync(authPath, JSON.stringify({
            tokens: {
                access_token: expiredJwt(),
                refresh_token: 'refresh-must-not-be-used',
                account_id: 'acct-expired-usage',
            },
        }));
        vi.stubEnv('OPENAI_CODEX_AUTH_PATHS', authPath);
        const app = await createTestApp();

        const response = await app.request('/api/codex/usage/slots/0', {
            method: 'POST',
            headers: authHeaders(),
        });

        expect(response.status).toBe(502);
        expect(mockFetch).not.toHaveBeenCalled();
        expect(await response.json()).toMatchObject({
            slotErrors: [expect.objectContaining({
                slotIndex: 0,
                message: 'Codex auth access token is expired',
            })],
            error: { message: 'Codex auth access token is expired' },
        });
    });

    it('returns a clear error when a manual usage check cannot refresh the slot', async () => {
        const dir = makeTempDir();
        const authPath = writeAuth(dir, 'auth.json', 'token-rejected', 'acct-rejected');
        vi.stubEnv('OPENAI_CODEX_AUTH_PATHS', authPath);
        mockFetch.mockResolvedValueOnce(new Response('unauthorized', { status: 401 }));
        const app = await createTestApp();

        const response = await app.request('/api/codex/usage/slots/0', {
            method: 'POST',
            headers: authHeaders(),
        });

        expect(response.status).toBe(502);
        expect(await response.json()).toMatchObject({
            slotErrors: [expect.objectContaining({
                slotIndex: 0,
                message: 'Codex usage HTTP 401',
            })],
            error: { message: 'Codex usage HTTP 401' },
        });
    });

    it('updates the expected weekly reset from the slot API', async () => {
        const dir = makeTempDir();
        const authPath = writeAuth(dir, 'auth.json', 'token-main', 'acct-main');
        vi.stubEnv('OPENAI_CODEX_AUTH_PATHS', authPath);
        const app = await createTestApp();

        const response = await app.request('/api/codex/balancer/slots/0', {
            method: 'PATCH',
            headers: {
                ...authHeaders(),
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ expectedWeeklyResetAt: '2026-06-12T08:45:00.000Z' }),
        });

        expect(response.status).toBe(200);
        expect(await response.json()).toMatchObject({
            slots: [{
                slotIndex: 0,
                expectedWeeklyResetAt: '2026-06-12T08:45:00.000Z',
            }],
        });
        expect(mockFetch).not.toHaveBeenCalled();
    });

    it('updates the cold migration threshold from balancer settings API', async () => {
        const app = await createTestApp();

        const response = await app.request('/api/codex/balancer/settings', {
            method: 'PATCH',
            headers: {
                ...authHeaders(),
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ coldMigrationFiveHourThresholdPercent: 6.5 }),
        });

        expect(response.status).toBe(200);
        expect(await response.json()).toMatchObject({
            settings: { coldMigrationFiveHourThresholdPercent: 6.5 },
            settingSources: { coldMigrationFiveHourThresholdPercent: 'persisted' },
        });
    });
});

describe('GET /api/codex/analysis', () => {
    it('requires bearer auth, validates periods, and returns analysis data', async () => {
        const app = await createTestApp();
        logRouting({
            timestamp: '2026-06-11T08:00:00.000Z',
            original_model: 'clawroute/auto',
            routed_model: 'codex/gpt-5.5',
            actual_model: 'codex/gpt-5.5',
            tier: TaskTier.COMPLEX,
            classification_reason: 'test',
            confidence: 0.9,
            input_tokens: 50,
            cached_input_tokens: 25,
            output_tokens: 5,
            original_cost_usd: 0,
            actual_cost_usd: 0,
            savings_usd: 0,
            escalated: false,
            escalation_chain: '[]',
            response_time_ms: 100,
            had_tool_calls: false,
            is_dry_run: false,
            is_override: false,
            session_id: null,
            error: null,
            prompt_preview: null,
            context_info: null,
            request_api_kind: 'responses',
            requested_reasoning_effort: 'medium',
            selected_codex_slot_index: 0,
            selected_codex_account_key: 'acct-analysis',
        });
        upsertCodexUsageSnapshots([{
            accountKey: 'acct-analysis',
            slotIndex: 0,
            window: 'weekly',
            usedPercent: 12,
            resetAt: '2026-06-18T08:00:00.000Z',
            windowMinutes: 10080,
            updatedAt: '2026-06-11T08:01:00.000Z',
        }]);

        const missing = await app.request('/api/codex/analysis?period=7d');
        expect(missing.status).toBe(401);
        expect(await missing.json()).toEqual(UNAUTHORIZED);

        const invalid = await app.request('/api/codex/analysis?period=bad', { headers: authHeaders() });
        expect(invalid.status).toBe(400);
        expect(await invalid.json()).toMatchObject({ error: { code: 'invalid_period' } });

        const response = await app.request(
            '/api/codex/analysis?period=custom&start=2026-06-11T00:00:00.000Z&end=2026-06-12T00:00:00.000Z',
            { headers: authHeaders() },
        );
        expect(response.status).toBe(200);
        expect(await response.json()).toMatchObject({
            period: { key: 'custom' },
            summary: {
                requests: 1,
                inputTokens: 50,
                cachedInputTokens: 25,
                outputTokens: 5,
            },
            apiKindMix: [{ key: 'responses', requests: 1, requestSharePercent: 100 }],
            reasoningEffortMix: [{ key: 'medium', requests: 1, requestSharePercent: 100 }],
            apiPricing: {
                source: 'https://developers.openai.com/api/docs/pricing',
                currency: 'USD',
            },
            flags: {
                hasQuotaHistory: true,
                hasSelectedCodexAttribution: true,
                hasReasoningEffort: true,
            },
        });
    });
});

describe('GET /dashboard-codex', () => {
    it('serves a public HTML shell with codex usage UI markers for the next slice', async () => {
        const app = await createTestApp();

        const response = await app.request('/dashboard-codex');
        expect(response.status).toBe(200);
        expect(response.headers.get('content-type')).toMatch(/text\/html/i);
        const html = await response.text();
        expect(html).toContain('/api/codex/usage');
        expect(html).toContain('/api/codex/balancer');
        expect(html).toContain('/api/codex/cold-migrations/');
        expect(html).toMatch(/Balancer mode/i);
        expect(html).toMatch(/Early activation/i);
        expect(html).toContain('cold-migration-threshold');
        expect(html).toMatch(/Enabled for routing/i);
        expect(html).toMatch(/Activate now/i);
        expect(html).toMatch(/Check usage/i);
        expect(html).toMatch(/Expected weekly reset/i);
        expect(html).toContain('/api/codex/usage/slots/');
        expect(html).toMatch(/sessionStorage/i);
        expect(html).toMatch(/401|Authorization/i);
        expect(html).toMatch(/5h|5-hour/i);
        expect(html).toMatch(/weekly/i);
        expect(html).toMatch(/Banked Resets/i);
        expect(html).toContain('renderResetCreditStrip');
        expect(html).toContain('resetCreditErrors');
        expect(html).toMatch(/account-rows|accounts-list|accounts-body/i);
        expect(html).toMatch(/stale|partial/i);
        expect(html).toMatch(/last sync|last updated/i);
        expect(html).toMatch(/href=["'][^"']*\/dashboard["']/i);
        expect(html).toMatch(/href=["'][^"']*\/dashboard-archive["']/i);
        expect(html).toContain('has-error');
        expect(html).toContain('renderSlotErrors');
        expect(html).toContain('cacheUsageRecent');
        expect(html).toContain('Path ${slot.path}');
        expect(html).toContain('Telemetry pending');
        expect(html).toContain('Last usage check');
        expect(html).not.toContain("${escapeHtml(slot.scheduledDay)} • Persisted telemetry");
        expect(html).toMatch(/authCard\.classList\.contains\('hidden'\)\s*\|\|\s*sessionStorage\.getItem\(TOKEN_KEY\)/);
    });
});

describe('GET /dashboard-codex-analysis', () => {
    it('serves the analysis dashboard shell with API and auth markers', async () => {
        const app = await createTestApp();

        const response = await app.request('/dashboard-codex-analysis');
        expect(response.status).toBe(200);
        expect(response.headers.get('content-type')).toMatch(/text\/html/i);
        const html = await response.text();
        expect(html).toContain('/api/codex/analysis');
        expect(html).toContain('sessionStorage');
        expect(html).toContain('Codex Usage Report');
        expect(html).toContain('slot-cards');
        expect(html).toContain('Daily quota trend');
        expect(html).toContain('Weekly quota trend');
        expect(html).toContain('Detailed per-slot table');
        expect(html).toContain('Raw tables');
        expect(html).toMatch(/Codex Analysis/i);
        expect(html).toMatch(/href=["'][^"']*\/dashboard-codex["']/i);
        expect(html).toMatch(/href=["'][^"']*\/dashboard-archive["']/i);
    });
});

describe('dashboard routes', () => {
    it.each(['/dashboard', '/dashboard2'])('%s serves Dashboard 2 with navigation and request inspection', async (path) => {
        const app = await createTestApp();

        const response = await app.request(path);
        expect(response.status).toBe(200);
        expect(response.headers.get('content-type')).toMatch(/text\/html/i);
        const html = await response.text();
        expect(html).toMatch(/href=["'][^"']*\/dashboard-codex["']/i);
        expect(html).toMatch(/href=["'][^"']*\/dashboard-codex-analysis["']/i);
        expect(html).toMatch(/href=["'][^"']*\/dashboard-archive["']/i);
        expect(html).toContain('promptPreview');
        expect(html).toMatch(/Inspect request|Inspect/i);
        expect(html).toContain('slice(0, 50)');
        expect(html).toContain('/api/routing/live');
        expect(html).toContain('loadTurnDetails');
        expect(html).toContain('session-group');
        expect(html).toContain('turn-group');
        expect(html).toContain('requestTrace');
        expect(html).toContain('estimateQuota');
        expect(html).toContain('quotaEstimatePills');
        expect(html).toContain("pill('weekly', weekly)");
        expect(html).toContain('class="summary-quota">${quotaEstimate}</span>');
        expect(html).toContain('burst-sensitive estimate');
    });

    it('/dashboard-archive retains the legacy dashboard', async () => {
        const app = await createTestApp();

        const response = await app.request('/dashboard-archive');
        expect(response.status).toBe(200);
        expect(response.headers.get('content-type')).toMatch(/text\/html/i);
        expect(await response.text()).toMatch(/ClawRoute Dashboard/i);
    });
});

describe('live routing hierarchy API', () => {
    it('requires auth, returns session/turn metrics, and validates turn ids', async () => {
        const app = await createTestApp();
        logRouting({
            timestamp: '2026-06-19T08:00:00.000Z',
            original_model: 'clawroute/auto',
            routed_model: 'codex/gpt-5.5',
            actual_model: 'codex/gpt-5.5',
            tier: TaskTier.MODERATE,
            classification_reason: 'test', confidence: 0.9,
            input_tokens: 100, cached_input_tokens: 75, output_tokens: 10,
            original_cost_usd: 0, actual_cost_usd: 0, savings_usd: 0,
            escalated: false, escalation_chain: '[]', response_time_ms: 120,
            had_tool_calls: false, is_dry_run: false, is_override: false,
            session_id: '0123456789abcdef', turn_id: 'fedcba9876543210', request_id: 'request-1',
            error: null, prompt_preview: 'Inspect hierarchy',
            context_info: JSON.stringify({
                request_trace: {
                    version: 1, sessionSource: 'prompt_cache_key', requestFingerprint: 'fingerprint',
                    messageFingerprints: ['fingerprint'], parentRequestId: null, phase: 'user_input',
                    delta: {
                        comparison: 'baseline', addedMessageCount: 1, removedMessageCount: 0,
                        addedChars: 10, roleCounts: { user: 1 }, toolCallCount: 0, toolResultCount: 0,
                        items: [], omittedItems: 0,
                        toolSchemas: { status: 'baseline', count: 0, chars: 0 },
                    },
                },
            }),
        });

        expect((await app.request('/api/routing/live')).status).toBe(401);
        const live = await app.request('/api/routing/live', { headers: authHeaders() });
        expect(live.status).toBe(200);
        expect(await live.json()).toMatchObject({
            retentionDays: 30,
            quotaCalibration: {
                source: 'calibrated_total_tokens',
                periodDays: 7,
                fiveHourBurstSensitive: true,
            },
            sessions: [{
                id: '0123456789abcdef',
                turnCount: 1,
                metrics: { requests: 1, inputTokens: 100, cachedInputTokens: 75, outputTokens: 10 },
                turns: [{ id: 'fedcba9876543210', promptPreview: 'Inspect hierarchy' }],
            }],
        });

        const invalid = await app.request('/api/routing/turns/not-valid', { headers: authHeaders() });
        expect(invalid.status).toBe(400);
        const detail = await app.request('/api/routing/turns/fedcba9876543210', { headers: authHeaders() });
        expect(detail.status).toBe(200);
        expect(await detail.json()).toMatchObject({
            turnId: 'fedcba9876543210',
            requests: [{ requestId: 'request-1', requestTrace: { phase: 'user_input' } }],
        });
    });
});
