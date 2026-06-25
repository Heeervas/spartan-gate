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
    getCodexCacheBreakerSnapshot,
    hashPromptCacheKey,
    recordCodexCacheBreakerOutcome,
    resetCodexCacheBreakerState,
} from '../src/codex-cache-breaker.js';
import { makeCodexRequest, resetRotationState } from '../src/codex-transport.js';
import { closeDb, getRecentDecisions, initDb } from '../src/logger.js';
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

function recordHealthy(overrides: Partial<Parameters<typeof recordCodexCacheBreakerOutcome>[0]> = {}) {
    return recordCodexCacheBreakerOutcome({
        promptCacheKeyHash,
        actualModel: 'codex/gpt-5.5',
        accountKey,
        slotIndex: 0,
        toolSchemaFingerprint,
        requestId: 'request-healthy',
        turnId: 'turn-test',
        inputTokens: 80_000,
        cachedInputTokens: 25_600,
        outputTokens: 10,
        phase: 'user_input',
        comparison: 'baseline',
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
    vi.stubEnv('CODEX_CACHE_BREAKER_BLOCKING_ENABLED', 'true');
    vi.stubEnv('CODEX_CACHE_BREAKER_MIN_INPUT_TOKENS', '20000');
    vi.stubEnv('CODEX_CACHE_BREAKER_LOW_CACHE_RATIO', '0.20');
    vi.stubEnv('CODEX_CACHE_BREAKER_CONSECUTIVE_MISSES', '2');
    vi.stubEnv('CODEX_CACHE_BREAKER_UNCACHED_BUDGET_TOKENS', '300000');
    vi.stubEnv('CODEX_CACHE_BREAKER_WINDOW_MISSES', '3');
    vi.stubEnv('CODEX_CACHE_BREAKER_WINDOW_REQUESTS', '5');
    vi.stubEnv('CODEX_BALANCE_LOADER_MODE', 'off');
    vi.stubEnv('OPENAI_CODEX_TOKEN', '');
    vi.stubEnv('OPENAI_CODEX_AUTH_PATH', '');
    vi.stubEnv('OPENAI_CODEX_AUTH_PATHS', '');
    vi.stubEnv('CODEX_HOME', '');
});

afterEach(() => {
    closeDb();
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

    it('records a large low-cache stream error even on a baseline user turn without immediate blocking', () => {
        const block = recordMiss({
            comparison: 'baseline',
            phase: 'user_input',
            inputTokens: 83_027,
            cachedInputTokens: 0,
            streamError: 'Stream error',
        });

        expect(block).toBeNull();
        expect(getCodexCacheBreakerSnapshot().breakers[0]).toMatchObject({
            status: 'watching',
            consecutiveMisses: 1,
            uncachedTokensSinceRecovery: 83_027,
            key: blockKey(),
            recent: [
                expect.objectContaining({
                    comparison: 'baseline',
                    phase: 'user_input',
                    streamError: 'Stream error',
                    failureKind: 'stream_error',
                }),
            ],
        });
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
            blockReason: 'consecutive_misses',
            consecutiveMisses: 2,
            key: blockKey(),
        });
        expect(getCodexCacheBreakerBlock(blockKey())?.id).toBe(block?.id);
    });

    it('defaults active blocking off while still tracking low-cache misses', () => {
        vi.stubEnv('CODEX_CACHE_BREAKER_BLOCKING_ENABLED', 'false');

        expect(recordMiss()).toBeNull();
        expect(recordMiss()).toBeNull();

        expect(getCodexCacheBreakerBlock(blockKey())).toBeNull();
        expect(getCodexCacheBreakerSnapshot().breakers[0]).toMatchObject({
            status: 'watching',
            consecutiveMisses: 2,
            uncachedTokensSinceRecovery: 52_000,
        });
    });

    it('treats healthy baseline user turns as recovery between prefix misses', () => {
        expect(recordMiss({ turnId: 'turn-a', inputTokens: 67_657 })).toBeNull();
        expect(recordHealthy({ turnId: 'turn-b', inputTokens: 74_682, cachedInputTokens: 25_600 })).toBeNull();
        expect(recordMiss({ turnId: 'turn-b', inputTokens: 78_536 })).toBeNull();
        expect(recordHealthy({ turnId: 'turn-c', inputTokens: 82_006, cachedInputTokens: 25_600 })).toBeNull();
        expect(recordMiss({ turnId: 'turn-c', inputTokens: 87_140 })).toBeNull();

        expect(getCodexCacheBreakerBlock(blockKey())).toBeNull();
    });

    it('resets consecutive misses and uncached budget on any same-key healthy request', () => {
        expect(recordMiss({ inputTokens: 120_000 })).toBeNull();
        expect(recordHealthy({ comparison: 'baseline', inputTokens: 90_000, cachedInputTokens: 30_000 })).toBeNull();
        expect(recordMiss({ inputTokens: 120_000 })).toBeNull();

        const snapshot = getCodexCacheBreakerSnapshot();
        expect(snapshot.breakers[0]).toMatchObject({
            status: 'watching',
            consecutiveMisses: 1,
            uncachedTokensSinceRecovery: 120_000,
        });
        expect(getCodexCacheBreakerBlock(blockKey())).toBeNull();
    });

    it('blocks repeated non-consecutive misses after the uncached budget is exceeded', () => {
        vi.stubEnv('CODEX_CACHE_BREAKER_CONSECUTIVE_MISSES', '99');
        vi.stubEnv('CODEX_CACHE_BREAKER_UNCACHED_BUDGET_TOKENS', '250000');

        expect(recordMiss({ turnId: 'turn-a', inputTokens: 90_000 })).toBeNull();
        expect(recordMiss({ turnId: 'turn-b', inputTokens: 90_000, comparison: 'baseline', phase: 'user_input' })).toBeNull();
        expect(recordMiss({ turnId: 'turn-c', inputTokens: 90_000 })).toBeNull();
        const block = recordMiss({ turnId: 'turn-d', inputTokens: 90_000 });

        expect(block).toMatchObject({
            blockReason: 'uncached_budget',
            consecutiveMisses: 3,
            uncachedTokensSinceRecovery: 270_000,
        });
    });

    it('does not count cold baseline misses toward the uncached budget', () => {
        vi.stubEnv('CODEX_CACHE_BREAKER_CONSECUTIVE_MISSES', '99');
        vi.stubEnv('CODEX_CACHE_BREAKER_UNCACHED_BUDGET_TOKENS', '100000');

        recordMiss({ comparison: 'baseline', phase: 'user_input', inputTokens: 150_000 });
        expect(getCodexCacheBreakerBlock(blockKey())).toBeNull();
    });

    it('does not block cold baseline misses without stream errors', () => {
        const result = recordMiss({
            comparison: 'baseline',
            phase: 'user_input',
            inputTokens: 150_000,
            cachedInputTokens: 0,
            streamError: null,
        });

        const snapshot = getCodexCacheBreakerSnapshot();
        expect(result).toBeNull();
        expect(snapshot.breakers[0]).toMatchObject({
            status: 'watching',
            consecutiveMisses: 0,
            uncachedTokensSinceRecovery: 0,
            recent: [
                expect.objectContaining({
                    expectedHit: false,
                    lowCache: false,
                    failureKind: null,
                }),
            ],
        });
        expect(getCodexCacheBreakerBlock(blockKey())).toBeNull();
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

    it('does not fail-closed after a single baseline stream-error cache failure', async () => {
        const dir = makeTempDir();
        const authPath = writeAuth(dir, 'auth.json', 'token-cache-breaker', 'acct-cache-breaker');
        vi.stubEnv('OPENAI_CODEX_AUTH_PATHS', authPath);
        recordMiss({
            actualModel: 'codex/gpt-5.4-mini',
            comparison: 'baseline',
            phase: 'user_input',
            inputTokens: 86_992,
            cachedInputTokens: 0,
            streamError: 'Stream error',
        });

        const fetchMock = vi.fn(async () => new Response([
            'event: response.output_text.delta',
            `data: ${JSON.stringify({ delta: 'ok after previous stream error' })}`,
            '',
            'event: response.completed',
            `data: ${JSON.stringify({ response: { status: 'completed', usage: { input_tokens: 3, output_tokens: 5 } } })}`,
            '',
        ].join('\n'), { status: 200, headers: { 'Content-Type': 'text/event-stream' } }));
        vi.stubGlobal('fetch', fetchMock);

        const response = await makeCodexRequest(
            {
                messages: [{ role: 'user', content: 'retry after failed stream' }],
                prompt_cache_key: promptCacheKey,
                stream: false,
                tools: [],
            },
            'codex/gpt-5.4-mini',
            null,
        );
        const body = await response.json() as { choices: Array<{ message: { content: string } }> };

        expect(response.status).toBe(200);
        expect(body.choices[0]?.message.content).toBe('ok after previous stream error');
        expect(fetchMock).toHaveBeenCalledTimes(1);
    });

    it('logs streaming preflight breaker blocks as zero-token routing rows', async () => {
        const dir = makeTempDir();
        const authPath = writeAuth(dir, 'auth.json', 'token-cache-breaker', 'acct-cache-breaker');
        vi.stubEnv('OPENAI_CODEX_AUTH_PATHS', authPath);
        recordMiss({ actualModel: 'codex/gpt-5.4-mini' });
        recordMiss({ actualModel: 'codex/gpt-5.4-mini' });

        const fetchMock = vi.fn();
        vi.stubGlobal('fetch', fetchMock);
        const config = createTestConfig();
        await initDb(config);
        const app = createApp(config);

        const response = await app.request('/v1/chat/completions', {
            method: 'POST',
            headers: { ...authHeaders(), 'Content-Type': 'application/json' },
            body: JSON.stringify({
                model: 'codex/gpt-5.4-mini',
                prompt_cache_key: promptCacheKey,
                stream: true,
                tools: [],
                messages: [{ role: 'user', content: 'continue without spending quota' }],
            }),
        });

        const decisions = getRecentDecisions(1);
        const context = decisions[0]?.context;

        expect(response.status).toBe(403);
        expect(fetchMock).not.toHaveBeenCalled();
        expect(decisions[0]).toMatchObject({
            actualModel: 'codex/gpt-5.4-mini',
            error: 'codex_cache_miss_breaker',
            inputTokens: 0,
            cachedInputTokens: 0,
            outputTokens: 0,
            selectedCodexSlotIndex: 0,
            selectedCodexAccountKey: accountKey,
        });
        expect(context).toMatchObject({
            cacheKeyPresent: true,
            cacheKeyHash: promptCacheKeyHash,
            policyBlock: {
                policy: 'codex_cache_miss_breaker',
                blockReason: 'consecutive_misses',
                cacheKeyHash: promptCacheKeyHash,
                toolSchemaFingerprint,
                source: 'preflight',
            },
        });
    });

    it('logs streaming Codex HTTP errors as routing rows with an error message', async () => {
        const dir = makeTempDir();
        const authPath = writeAuth(dir, 'auth.json', 'token-cache-breaker', 'acct-cache-breaker');
        vi.stubEnv('OPENAI_CODEX_AUTH_PATHS', authPath);
        const fetchMock = vi.fn(async () => new Response(JSON.stringify({
            error: {
                message: 'This operation was aborted',
                code: 'codex_error',
                type: 'server_error',
            },
        }), {
            status: 502,
            headers: { 'Content-Type': 'application/json' },
        }));
        vi.stubGlobal('fetch', fetchMock);
        const config = createTestConfig();
        await initDb(config);
        const app = createApp(config);

        const response = await app.request('/v1/chat/completions', {
            method: 'POST',
            headers: { ...authHeaders(), 'Content-Type': 'application/json' },
            body: JSON.stringify({
                model: 'codex/gpt-5.4-mini',
                stream: true,
                tools: [],
                messages: [{ role: 'user', content: 'hola' }],
            }),
        });
        await new Promise<void>((resolve) => setImmediate(resolve));
        const decisions = getRecentDecisions(1);

        expect(response.status).toBe(502);
        expect(fetchMock).toHaveBeenCalledTimes(1);
        expect(decisions[0]).toMatchObject({
            actualModel: 'codex/gpt-5.4-mini',
            error: 'Codex API error (502): This operation was aborted [slot:0 code:codex_error]',
            selectedCodexSlotIndex: 0,
            selectedCodexAccountKey: accountKey,
        });
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
