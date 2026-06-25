import { createHash } from 'node:crypto';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { TaskTier, ClawRouteConfig } from '../src/types.js';
import { buildToolSchemaFingerprint } from '../src/request-trace.js';
import {
    approveCodexCacheBreaker,
    clearCodexCacheBreaker,
    getCodexCacheBreakerBlock,
    hashPromptCacheKey,
    recordCodexCacheBreakerOutcome,
    resetCodexCacheBreakerState,
} from '../src/codex-cache-breaker.js';
import { makeCodexRequest, resetRotationState } from '../src/codex-transport.js';
import { createApp } from '../src/server.js';

const tempDirs: string[] = [];
const ADMIN_TOKEN = 'admin-secret';
const promptCacheKey = 'hermes:cache-breaker-test';
const promptCacheKeyHash = hashPromptCacheKey(promptCacheKey)!;
const accountKey = createHash('sha256').update('acct-cache-breaker').digest('hex').slice(0, 16);
const toolSchemaFingerprint = buildToolSchemaFingerprint([]);

function makeTempDir(): string {
    const dir = mkdtempSync(join(tmpdir(), 'codex-cache-breaker-'));
    tempDirs.push(dir);
    return dir;
}

function writeAuth(dir: string, name: string, accessToken: string, accountId: string): string {
    const path = join(dir, name);
    writeFileSync(path, JSON.stringify({ tokens: { access_token: accessToken, account_id: accountId } }));
    return path;
}

function recordMiss(overrides: Partial<Parameters<typeof recordCodexCacheBreakerOutcome>[0]> = {}) {
    return recordCodexCacheBreakerOutcome({
        promptCacheKeyHash,
        actualModel: 'codex/gpt-5.5',
        accountKey,
        slotIndex: 0,
        toolSchemaFingerprint,
        requestId: 'request-test',
        turnId: 'turn-test',
        inputTokens: 26_000,
        cachedInputTokens: 0,
        outputTokens: 10,
        phase: 'tool_results',
        comparison: 'prefix',
        ...overrides,
    });
}

function blockKey(overrides: Partial<Parameters<typeof getCodexCacheBreakerBlock>[0]> = {}) {
    return {
        promptCacheKeyHash,
        actualModel: 'codex/gpt-5.5',
        accountKey,
        slotIndex: 0,
        toolSchemaFingerprint,
        ...overrides,
    };
}

function createTestConfig(): ClawRouteConfig {
    return {
        enabled: true,
        dryRun: false,
        baselineModel: 'openai/gpt-5.2',
        providerProfile: null,
        proxyPort: 18799,
        proxyHost: '127.0.0.1',
        authToken: ADMIN_TOKEN,
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
    };
}

function authHeaders(): HeadersInit {
    return { Authorization: `Bearer ${ADMIN_TOKEN}` };
}

beforeEach(() => {
    resetRotationState();
    resetCodexCacheBreakerState();
    vi.stubEnv('CODEX_CACHE_BREAKER_ENABLED', 'true');
    vi.stubEnv('CODEX_CACHE_BREAKER_MIN_INPUT_TOKENS', '20000');
    vi.stubEnv('CODEX_CACHE_BREAKER_LOW_CACHE_RATIO', '0.20');
    vi.stubEnv('CODEX_CACHE_BREAKER_CONSECUTIVE_MISSES', '2');
    vi.stubEnv('CODEX_CACHE_BREAKER_WINDOW_MISSES', '3');
    vi.stubEnv('CODEX_CACHE_BREAKER_WINDOW_REQUESTS', '5');
    vi.stubEnv('CODEX_BALANCE_LOADER_MODE', 'off');
    vi.stubEnv('OPENAI_CODEX_TOKEN', '');
    vi.stubEnv('OPENAI_CODEX_AUTH_PATH', '');
    vi.stubEnv('OPENAI_CODEX_AUTH_PATHS', '');
    vi.stubEnv('CODEX_HOME', '');
});

afterEach(() => {
    resetRotationState();
    resetCodexCacheBreakerState();
    vi.unstubAllEnvs();
    vi.restoreAllMocks();
    while (tempDirs.length > 0) rmSync(tempDirs.pop()!, { recursive: true, force: true });
});

describe('Codex cache-miss breaker', () => {
    it('does not count first cold baseline requests as cache failures', () => {
        recordMiss({ comparison: 'baseline', phase: 'user_input' });
        expect(getCodexCacheBreakerBlock(blockKey())).toBeNull();
    });

    it('records one low-cache prefix miss without blocking the next request yet', () => {
        recordMiss({ comparison: 'baseline', phase: 'user_input' });
        recordMiss();
        expect(getCodexCacheBreakerBlock(blockKey())).toBeNull();
    });

    it('blocks after two consecutive expected-hit cache misses', () => {
        expect(recordMiss()).toBeNull();
        const block = recordMiss();
        expect(block).toMatchObject({
            policy: 'codex_cache_miss_breaker',
            consecutiveMisses: 2,
            key: blockKey(),
        });
        expect(getCodexCacheBreakerBlock(blockKey())?.id).toBe(block?.id);
    });

    it('does not let misses bleed across model or tool-schema boundaries', () => {
        recordMiss({ actualModel: 'codex/gpt-5.4-mini' });
        recordMiss({ actualModel: 'codex/gpt-5.4-mini' });
        expect(getCodexCacheBreakerBlock(blockKey())).toBeNull();
        expect(getCodexCacheBreakerBlock(blockKey({ actualModel: 'codex/gpt-5.4-mini' }))).not.toBeNull();
    });

    it('allows approved breakers until their approval expires', () => {
        recordMiss();
        const block = recordMiss();
        expect(block).not.toBeNull();

        const approved = approveCodexCacheBreaker(block!.id, 15);
        expect(approved?.approvalExpiresAt).toEqual(expect.any(String));
        expect(getCodexCacheBreakerBlock(blockKey())).toBeNull();

        const cleared = clearCodexCacheBreaker(block!.id);
        expect(cleared?.id).toBe(block!.id);
    });

    it('returns a fail-closed 403 before a matching Codex request can spend quota', async () => {
        const dir = makeTempDir();
        const authPath = writeAuth(dir, 'auth.json', 'token-cache-breaker', 'acct-cache-breaker');
        vi.stubEnv('OPENAI_CODEX_AUTH_PATHS', authPath);
        recordMiss({ actualModel: 'codex/gpt-5.4-mini' });
        recordMiss({ actualModel: 'codex/gpt-5.4-mini' });

        const fetchMock = vi.fn();
        vi.stubGlobal('fetch', fetchMock);

        const response = await makeCodexRequest(
            {
                messages: [{ role: 'user', content: 'continue without spending quota' }],
                prompt_cache_key: promptCacheKey,
                stream: false,
                tools: [],
            },
            'codex/gpt-5.4-mini',
            null,
        );
        const body = await response.json() as { error: Record<string, unknown> };

        expect(response.status).toBe(403);
        expect(response.headers.get('X-ClawRoute-Policy-Block')).toBe('codex_cache_miss_breaker');
        expect(body.error.code).toBe('codex_cache_miss_breaker_blocked');
        expect(fetchMock).not.toHaveBeenCalled();
    });

    it('exposes authenticated cache-breaker state, approval, and clear endpoints', async () => {
        const block = (recordMiss(), recordMiss());
        const app = createApp(createTestConfig());

        const unauthorized = await app.request('/api/codex/cache-breaker');
        expect(unauthorized.status).toBe(401);

        const state = await app.request('/api/codex/cache-breaker', { headers: authHeaders() });
        expect(state.status).toBe(200);
        expect(await state.json()).toMatchObject({
            breakers: [expect.objectContaining({ id: block!.id, status: 'blocked' })],
        });

        const approved = await app.request(`/api/codex/cache-breaker/${block!.id}/approve`, {
            method: 'POST',
            headers: { ...authHeaders(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ ttlMinutes: 10 }),
        });
        expect(approved.status).toBe(200);
        expect(await approved.json()).toMatchObject({
            breaker: expect.objectContaining({ id: block!.id, approvalExpiresAt: expect.any(String) }),
        });

        const cleared = await app.request(`/api/codex/cache-breaker/${block!.id}/clear`, {
            method: 'POST',
            headers: authHeaders(),
        });
        expect(cleared.status).toBe(200);
        expect(await cleared.json()).toMatchObject({ state: { breakers: [] } });
    });
});
