import { mkdtempSync, rmSync, writeFileSync } from 'fs';
import { createHash } from 'crypto';
import { tmpdir } from 'os';
import { join } from 'path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { loadConfig, resetConfig } from '../src/config.js';
import {
    getRotationState,
    initializeSlots,
    makeCodexRequest,
    performRotation,
    releaseCodexAuth,
    resetRotationState,
    resolveAuthPaths,
    selectAutomaticCodexUsageSlotIndexes,
    shouldRotate,
} from '../src/codex-transport.js';

const tempDirs: string[] = [];
const baseRequest = {
    messages: [{ role: 'user', content: 'hello from the regression test' }],
    stream: false,
};

function makeTempDir(): string {
    const dir = mkdtempSync(join(tmpdir(), 'codex-rotation-'));
    tempDirs.push(dir);
    return dir;
}

function writeAuth(dir: string, name: string, accessToken: string, accountId: string): string {
    const path = join(dir, name);
    writeFileSync(path, JSON.stringify({ tokens: { access_token: accessToken, account_id: accountId } }));
    return path;
}

function expiredJwt(): string {
    const header = Buffer.from(JSON.stringify({ alg: 'none', typ: 'JWT' })).toString('base64url');
    const payload = Buffer.from(JSON.stringify({ exp: 1 })).toString('base64url');
    return `${header}.${payload}.signature`;
}

function authHeader(init?: RequestInit): string {
    return String((init?.headers as Record<string, string> | undefined)?.Authorization ?? '');
}

function hashAccountKey(accountId: string): string {
    return createHash('sha256').update(accountId).digest('hex').slice(0, 16);
}

function rateLimitResponse(message: string, metadata: Record<string, number> = {}): Response {
    return new Response(JSON.stringify({ error: { message, type: 'usage_limit_reached', ...metadata } }), {
        status: 429,
        headers: { 'Content-Type': 'application/json' },
    });
}

function successResponse(text: string): Response {
    const body = [
        'event: response.output_text.delta',
        `data: ${JSON.stringify({ delta: text })}`,
        '',
        'event: response.completed',
        `data: ${JSON.stringify({ response: { status: 'completed', usage: { input_tokens: 3, output_tokens: 5 } } })}`,
        '',
    ].join('\n');
    return new Response(body, { status: 200, headers: { 'Content-Type': 'text/event-stream' } });
}

async function importTransportWithBalanceLoaderMocks(options: {
    selectorSnapshot: Record<string, unknown>;
    selectorResult: Record<string, unknown>;
}) {
    vi.resetModules();
    const getCodexUsageSelectorSnapshot = vi.fn(async () => options.selectorSnapshot);
    const getCodexUsage = vi.fn(async () => ({
        status: 200,
        body: { partial: false, accounts: [], slotErrors: [] },
    }));
    const selectCodexBalanceCandidate = vi.fn(() => options.selectorResult);
    const upsertCodexColdMigrationDecision = vi.fn();
    const hasApprovedCodexColdMigrationDecision = vi.fn(() => false);
    const consumeCodexColdMigrationDecision = vi.fn();
    const seedCodexAccountSchedule = vi.fn((accountKeys: string[], startWeekday: number) => (
        accountKeys.map((accountKey, index) => ({
            accountKey,
            seedOrder: index,
            anchorWeekday: (startWeekday + index) % 7,
            laneRank: Math.floor(index / 7),
            updatedAt: new Date().toISOString(),
        }))
    ));

    vi.doMock('../src/codex-usage.js', async () => {
        const actual = await vi.importActual<Record<string, unknown>>('../src/codex-usage.js');
        return {
            ...actual,
            getCodexUsage,
            getCodexUsageSelectorSnapshot,
        };
    });
    vi.doMock('../src/codex-balance-loader.js', () => ({ selectCodexBalanceCandidate }));
    vi.doMock('../src/logger.js', async () => {
        const actual = await vi.importActual<Record<string, unknown>>('../src/logger.js');
        return {
            ...actual,
            consumeCodexColdMigrationDecision,
            getLiveQuotaCalibration: vi.fn(() => ({
                source: 'calibrated_total_tokens',
                periodDays: 7,
                fiveHour: { window: 'fiveHour', quotaPctPerMillionTotalTokens: 100 },
                weekly: null,
                fiveHourBurstSensitive: true,
            })),
            getPendingCodexColdMigrationDecisions: vi.fn(() => []),
            hasApprovedCodexColdMigrationDecision,
            seedCodexAccountSchedule,
            upsertCodexColdMigrationDecision,
        };
    });

    return {
        transport: await import('../src/codex-transport.js'),
        getCodexUsageSelectorSnapshot,
        getCodexUsage,
        selectCodexBalanceCandidate,
        seedCodexAccountSchedule,
        upsertCodexColdMigrationDecision,
        hasApprovedCodexColdMigrationDecision,
        consumeCodexColdMigrationDecision,
    };
}

beforeEach(() => {
    resetRotationState();
    resetConfig();
    vi.stubEnv('CLAWROUTE_PROVIDER', '');
    vi.stubEnv('OPENAI_CODEX_AUTH_PATHS', '');
    vi.stubEnv('OPENAI_CODEX_AUTH_PATH', '');
    vi.stubEnv('OPENAI_CODEX_TOKEN', '');
    vi.stubEnv('CODEX_HOME', '');
});

afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
    vi.resetModules();
    resetRotationState();
    resetConfig();
    while (tempDirs.length > 0) rmSync(tempDirs.pop()!, { recursive: true, force: true });
});

describe('resolveAuthPaths', () => {
    it('prefers OPENAI_CODEX_AUTH_PATHS and trims whitespace', () => {
        vi.stubEnv('OPENAI_CODEX_AUTH_PATHS', ' /a.json , /b.json ');
        vi.stubEnv('OPENAI_CODEX_AUTH_PATH', '/single.json');
        expect(resolveAuthPaths()).toEqual(['/a.json', '/b.json']);
    });

    it('falls back to the single auth path', () => {
        vi.stubEnv('OPENAI_CODEX_AUTH_PATH', '/single.json');
        expect(resolveAuthPaths()).toEqual(['/single.json']);
    });

    it('treats auth path entries without .json as CODEX_HOME directories', () => {
        vi.stubEnv('OPENAI_CODEX_AUTH_PATHS', ' /profiles/work , /profiles/personal/auth.json ');
        expect(resolveAuthPaths()).toEqual(['/profiles/work/auth.json', '/profiles/personal/auth.json']);
    });

    it('returns no auth paths in token mode', () => {
        vi.stubEnv('OPENAI_CODEX_TOKEN', 'test-session-token');
        expect(resolveAuthPaths()).toEqual([]);
    });

    it('uses the default codex auth file when env vars are absent', () => {
        expect(resolveAuthPaths()[0]).toMatch(/\.codex\/auth\.json$/);
    });

    it('loads the codex token from CODEX_HOME/auth.json', () => {
        const dir = makeTempDir();
        writeAuth(dir, 'auth.json', 'token-home', 'acct-home');
        vi.stubEnv('CLAWROUTE_PROVIDER', 'codex');
        vi.stubEnv('CODEX_HOME', dir);

        expect(loadConfig().apiKeys.codex).toBe('token-home');
    });
});

describe('rotation helpers', () => {
    it('keeps rotation disabled when only one slot exists', () => {
        initializeSlots(['/a.json']);
        expect(shouldRotate()).toBe(false);
    });

    it('rotates round-robin and records the new index', () => {
        initializeSlots(['/a.json', '/b.json', '/c.json']);
        performRotation();
        performRotation();
        performRotation();
        expect(getRotationState().currentSlotIndex).toBe(0);
    });

    it('keeps slot 0 but excludes disabled slots from automatic usage polling', () => {
        expect(selectAutomaticCodexUsageSlotIndexes({
            slots: [{ slotIndex: 0 }, { slotIndex: 1 }, { slotIndex: 2 }, { slotIndex: 3 }],
            activatedSlotIndexes: new Set([0, 1, 2]),
            enabledSlotIndexes: new Set([0, 2, 3]),
        })).toEqual([0, 2]);
    });

    it('updates lastQueryEndTime without letting activeRequests go negative', () => {
        initializeSlots(['/a.json']);
        const before = getRotationState().lastQueryEndTime;
        releaseCodexAuth();
        const after = getRotationState();
        expect(after.lastQueryEndTime).toBeGreaterThanOrEqual(before);
        expect(after.activeRequests).toBe(0);
    });
});

describe('makeCodexRequest regressions', () => {
    it('retries the second auth slot on the same first cold-start request after slot 0 returns 429', async () => {
        const dir = makeTempDir();
        const firstPath = writeAuth(dir, 'first.json', 'token-first', 'acct-first');
        const secondPath = writeAuth(dir, 'second.json', 'token-second', 'acct-second');
        vi.stubEnv('OPENAI_CODEX_AUTH_PATHS', `${firstPath},${secondPath}`);

        const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
            const authorization = authHeader(init);
            if (authorization === 'Bearer token-first') {
                return rateLimitResponse('slot 0 exhausted', { resets_in_seconds: 45 });
            }
            if (authorization === 'Bearer token-second') {
                return successResponse('slot 1 answered after the retry');
            }
            throw new Error(`Unexpected Authorization header: ${authorization}`);
        });
        vi.stubGlobal('fetch', fetchMock);

        const response = await makeCodexRequest(baseRequest, 'codex/gpt-5.4-mini', null);

        expect(fetchMock).toHaveBeenCalledTimes(2);
        expect(fetchMock.mock.calls.map(([, init]) => authHeader(init))).toEqual([
            'Bearer token-first',
            'Bearer token-second',
        ]);
        expect(response.status).toBe(200);
        const body = await response.json() as Record<string, unknown>;
        const choices = body['choices'] as Array<Record<string, unknown>>;
        const message = choices[0]?.['message'] as Record<string, unknown>;
        expect(message['content']).toBe('slot 1 answered after the retry');
    });

    it('returns a single-wrapped exhausted-slots 429 that preserves cooldown metadata from upstream JSON', async () => {
        const dir = makeTempDir();
        const firstPath = writeAuth(dir, 'first.json', 'token-first', 'acct-first');
        const secondPath = writeAuth(dir, 'second.json', 'token-second', 'acct-second');
        const resetsAt = Math.floor(Date.now() / 1000) + 600;
        vi.stubEnv('OPENAI_CODEX_AUTH_PATHS', `${firstPath},${secondPath}`);
        initializeSlots([firstPath, secondPath]);

        const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
            const authorization = authHeader(init);
            if (authorization === 'Bearer token-first') {
                return rateLimitResponse('slot 0 exhausted', { resets_in_seconds: 30 });
            }
            if (authorization === 'Bearer token-second') {
                return rateLimitResponse('slot 1 exhausted', {
                    resets_at: resetsAt,
                    resets_in_seconds: 600,
                });
            }
            throw new Error(`Unexpected Authorization header: ${authorization}`);
        });
        vi.stubGlobal('fetch', fetchMock);

        const response = await makeCodexRequest(baseRequest, 'codex/gpt-5.4-mini', null);
        const body = await response.json() as { error: Record<string, unknown> };

        expect(response.status).toBe(429);
        expect(fetchMock).toHaveBeenCalledTimes(2);
        expect(body.error['resets_at']).toBe(resetsAt);
        expect(body.error['resets_in_seconds']).toBe(600);
        expect(body.error['message']).toContain('slot 1 exhausted');
        expect(body.error['message']).not.toContain('{"error":');
    });

    it('uses a single attempt in token mode even when auth paths are also configured', async () => {
        vi.stubEnv('OPENAI_CODEX_TOKEN', 'test-session-token');
        vi.stubEnv('OPENAI_CODEX_AUTH_PATHS', '/unused/first.json,/unused/second.json');
        const fetchMock = vi.fn(async () => rateLimitResponse('token mode quota', { resets_in_seconds: 30 }));
        vi.stubGlobal('fetch', fetchMock);

        const response = await makeCodexRequest(baseRequest, 'codex/gpt-5.4-mini', null);
        expect(fetchMock).toHaveBeenCalledTimes(1);
        expect(fetchMock.mock.calls[0]?.[1]?.headers).not.toHaveProperty('chatgpt-account-id');
        expect(fetchMock.mock.calls[0]?.[1]?.headers).toHaveProperty('Authorization', 'Bearer test-session-token');
        expect(response.status).toBe(429);
    });

    it('does not send an expired access token upstream after OAuth refresh fails', async () => {
        const dir = makeTempDir();
        const authPath = join(dir, 'expired.json');
        writeFileSync(authPath, JSON.stringify({
            tokens: {
                access_token: expiredJwt(),
                refresh_token: 'refresh-invalid',
                account_id: 'acct-expired',
            },
        }));
        vi.stubEnv('OPENAI_CODEX_AUTH_PATHS', authPath);
        const fetchMock = vi.fn(async (url: string) => {
            if (url.includes('/oauth/token')) {
                return new Response(JSON.stringify({ error: 'invalid_grant' }), { status: 400 });
            }
            throw new Error('expired access token should not be sent upstream');
        });
        vi.stubGlobal('fetch', fetchMock);

        const first = await makeCodexRequest(baseRequest, 'codex/gpt-5.4-mini', null);
        const second = await makeCodexRequest(baseRequest, 'codex/gpt-5.4-mini', null);

        expect(first.status).toBe(401);
        expect(second.status).toBe(401);
        expect(fetchMock.mock.calls.filter(([url]) => String(url).includes('/oauth/token'))).toHaveLength(1);
        expect(fetchMock.mock.calls.filter(([url]) => !String(url).includes('/oauth/token'))).toHaveLength(0);
    });

    it('skips a refresh-failed slot and uses the next healthy slot', async () => {
        const dir = makeTempDir();
        const expiredPath = join(dir, 'expired.json');
        const healthyPath = writeAuth(dir, 'healthy.json', 'token-healthy', 'acct-healthy');
        writeFileSync(expiredPath, JSON.stringify({
            tokens: {
                access_token: expiredJwt(),
                refresh_token: 'refresh-invalid',
                account_id: 'acct-expired',
            },
        }));
        vi.stubEnv('OPENAI_CODEX_AUTH_PATHS', `${expiredPath},${healthyPath}`);
        const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
            if (url.includes('/oauth/token')) {
                return new Response(JSON.stringify({ error: 'invalid_grant' }), { status: 400 });
            }
            if (authHeader(init) === 'Bearer token-healthy') {
                return successResponse('healthy slot handled request');
            }
            throw new Error(`Unexpected upstream auth header: ${authHeader(init)}`);
        });
        vi.stubGlobal('fetch', fetchMock);

        const response = await makeCodexRequest(baseRequest, 'codex/gpt-5.4-mini', null);
        const body = await response.json() as Record<string, unknown>;
        const choices = body['choices'] as Array<Record<string, unknown>>;
        const message = choices[0]?.['message'] as Record<string, unknown>;

        expect(response.status).toBe(200);
        expect(message['content']).toBe('healthy slot handled request');
        expect(fetchMock.mock.calls.map(([, init]) => authHeader(init)).filter(Boolean)).toEqual(['Bearer token-healthy']);
    });

    it('uses the selector-chosen persisted winner first on cold start when balance-loader mode is on', async () => {
        const dir = makeTempDir();
        const firstPath = writeAuth(dir, 'first.json', 'token-first', 'acct-first');
        const secondPath = writeAuth(dir, 'second.json', 'token-second', 'acct-second');
        vi.stubEnv('OPENAI_CODEX_AUTH_PATHS', `${firstPath},${secondPath}`);
        vi.stubEnv('CODEX_BALANCE_LOADER_MODE', 'on');

        const {
            transport,
            getCodexUsageSelectorSnapshot,
            selectCodexBalanceCandidate,
        } = await importTransportWithBalanceLoaderMocks({
            selectorSnapshot: {
                fallbackReason: null,
                accounts: [
                    {
                        accountKey: 'acct-second-key',
                        slotIndex: 1,
                        slotIndexes: [1],
                        slotPaths: [secondPath],
                        source: 'persisted',
                        stale: false,
                        cooldownUntil: null,
                        lastFetchedAt: null,
                        updatedAt: new Date().toISOString(),
                        fiveHour: { usedPercent: 12, resetAt: new Date(Date.now() + 3_600_000).toISOString(), updatedAt: new Date().toISOString(), window: 'fiveHour', windowMinutes: 300 },
                        weekly: { usedPercent: 18, resetAt: new Date(Date.now() + 86_400_000).toISOString(), updatedAt: new Date().toISOString(), window: 'weekly', windowMinutes: 10_080 },
                    },
                ],
            },
            selectorResult: {
                fallbackReason: null,
                selectedAccountKey: 'acct-second-key',
                selectedSlotIndex: 1,
                affinityApplied: false,
            },
        });
        transport.resetRotationState();

        const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
            const authorization = authHeader(init);
            if (authorization === 'Bearer token-second') {
                return successResponse('selector-picked slot 1');
            }
            if (authorization === 'Bearer token-first') {
                return successResponse('legacy slot 0 should not run first');
            }
            throw new Error(`Unexpected Authorization header: ${authorization}`);
        });
        vi.stubGlobal('fetch', fetchMock);

        const response = await transport.makeCodexRequest(baseRequest, 'codex/gpt-5.4-mini', null);
        const body = await response.json() as Record<string, unknown>;
        const choices = body['choices'] as Array<Record<string, unknown>>;
        const message = choices[0]?.['message'] as Record<string, unknown>;

        expect(getCodexUsageSelectorSnapshot).toHaveBeenCalledTimes(1);
        expect(selectCodexBalanceCandidate).toHaveBeenCalledTimes(1);
        expect(fetchMock.mock.calls.map(([, init]) => authHeader(init))).toEqual(['Bearer token-second']);
        expect(message['content']).toBe('selector-picked slot 1');
    });

    it('blocks high-impact cold migrations before calling the target slot', async () => {
        const dir = makeTempDir();
        const firstPath = writeAuth(dir, 'first.json', 'token-first', 'acct-first');
        const secondPath = writeAuth(dir, 'second.json', 'token-second', 'acct-second');
        const firstAccountKey = hashAccountKey('acct-first');
        const secondAccountKey = hashAccountKey('acct-second');
        vi.stubEnv('OPENAI_CODEX_AUTH_PATHS', `${firstPath},${secondPath}`);
        vi.stubEnv('CODEX_BALANCE_LOADER_MODE', 'on');

        const {
            transport,
            selectCodexBalanceCandidate,
            upsertCodexColdMigrationDecision,
        } = await importTransportWithBalanceLoaderMocks({
            selectorSnapshot: {
                fallbackReason: null,
                accounts: [
                    {
                        accountKey: firstAccountKey,
                        slotIndex: 0,
                        slotIndexes: [0],
                        slotPaths: [firstPath],
                        source: 'persisted',
                        stale: false,
                        cooldownUntil: null,
                        lastFetchedAt: null,
                        updatedAt: new Date().toISOString(),
                        fiveHour: { usedPercent: 100, resetAt: new Date(Date.now() + 3_600_000).toISOString(), updatedAt: new Date().toISOString(), window: 'fiveHour', windowMinutes: 300 },
                        weekly: { usedPercent: 10, resetAt: new Date(Date.now() + 86_400_000).toISOString(), updatedAt: new Date().toISOString(), window: 'weekly', windowMinutes: 10_080 },
                    },
                    {
                        accountKey: secondAccountKey,
                        slotIndex: 1,
                        slotIndexes: [1],
                        slotPaths: [secondPath],
                        source: 'persisted',
                        stale: false,
                        cooldownUntil: null,
                        lastFetchedAt: null,
                        updatedAt: new Date().toISOString(),
                        fiveHour: { usedPercent: 1, resetAt: new Date(Date.now() + 3_600_000).toISOString(), updatedAt: new Date().toISOString(), window: 'fiveHour', windowMinutes: 300 },
                        weekly: { usedPercent: 0, resetAt: new Date(Date.now() + 86_400_000).toISOString(), updatedAt: new Date().toISOString(), window: 'weekly', windowMinutes: 10_080 },
                    },
                ],
            },
            selectorResult: {
                fallbackReason: null,
                selectedAccountKey: firstAccountKey,
                selectedSlotIndex: 0,
                affinityApplied: false,
            },
        });
        transport.resetRotationState();

        const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
            const authorization = authHeader(init);
            if (authorization === 'Bearer token-first') return successResponse('first slot establishes affinity');
            if (authorization === 'Bearer token-second') return successResponse('target slot should not be called');
            throw new Error(`Unexpected Authorization header: ${authorization}`);
        });
        vi.stubGlobal('fetch', fetchMock);

        const promptCacheKey = 'hermes:session-cold-migration';
        const firstResponse = await transport.makeCodexRequest(
            { ...baseRequest, prompt_cache_key: promptCacheKey },
            'codex/gpt-5.4-mini',
            null,
        );
        expect(firstResponse.status).toBe(200);

        selectCodexBalanceCandidate.mockReturnValue({
            fallbackReason: null,
            selectedAccountKey: secondAccountKey,
            selectedSlotIndex: 1,
            affinityApplied: false,
        });

        const blocked = await transport.makeCodexRequest(
            {
                messages: [{ role: 'user', content: 'x'.repeat(400_000) }],
                prompt_cache_key: promptCacheKey,
                stream: false,
            },
            'codex/gpt-5.4-mini',
            null,
        );
        const body = await blocked.json() as { error: Record<string, unknown> };

        expect(blocked.status).toBe(403);
        expect(blocked.headers.get('X-ClawRoute-Policy-Block')).toBe('codex_cold_migration');
        expect(body.error.code).toBe('codex_cold_migration_blocked');
        expect(fetchMock.mock.calls.map(([, init]) => authHeader(init))).toEqual(['Bearer token-first']);
        expect(upsertCodexColdMigrationDecision).toHaveBeenCalledWith(expect.objectContaining({
            previousAccountKey: firstAccountKey,
            targetAccountKey: secondAccountKey,
            targetSlotIndex: 1,
            thresholdFiveHourPercent: 7,
        }));
    });

    it('computes a selector recommendation in shadow mode but still uses the legacy slot first', async () => {
        const dir = makeTempDir();
        const firstPath = writeAuth(dir, 'first.json', 'token-first', 'acct-first');
        const secondPath = writeAuth(dir, 'second.json', 'token-second', 'acct-second');
        vi.stubEnv('OPENAI_CODEX_AUTH_PATHS', `${firstPath},${secondPath}`);
        vi.stubEnv('CODEX_BALANCE_LOADER_MODE', 'shadow');
        const logSpy = vi.spyOn(console, 'log').mockImplementation(() => {});

        const {
            transport,
            getCodexUsageSelectorSnapshot,
            selectCodexBalanceCandidate,
        } = await importTransportWithBalanceLoaderMocks({
            selectorSnapshot: {
                fallbackReason: null,
                accounts: [
                    {
                        accountKey: 'acct-second-key',
                        slotIndex: 1,
                        slotIndexes: [1],
                        slotPaths: [secondPath],
                        source: 'persisted',
                        stale: false,
                        cooldownUntil: null,
                        lastFetchedAt: null,
                        updatedAt: new Date().toISOString(),
                        fiveHour: { usedPercent: 8, resetAt: new Date(Date.now() + 3_600_000).toISOString(), updatedAt: new Date().toISOString(), window: 'fiveHour', windowMinutes: 300 },
                        weekly: { usedPercent: 16, resetAt: new Date(Date.now() + 86_400_000).toISOString(), updatedAt: new Date().toISOString(), window: 'weekly', windowMinutes: 10_080 },
                    },
                ],
            },
            selectorResult: {
                fallbackReason: null,
                selectedAccountKey: 'acct-second-key',
                selectedSlotIndex: 1,
                affinityApplied: false,
            },
        });
        transport.resetRotationState();

        const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
            const authorization = authHeader(init);
            if (authorization === 'Bearer token-first') {
                return successResponse('legacy slot 0 still answers first in shadow mode');
            }
            if (authorization === 'Bearer token-second') {
                return successResponse('selector-picked slot 1 should stay unused in shadow mode');
            }
            throw new Error(`Unexpected Authorization header: ${authorization}`);
        });
        vi.stubGlobal('fetch', fetchMock);

        const response = await transport.makeCodexRequest(baseRequest, 'codex/gpt-5.4-mini', null);
        const body = await response.json() as Record<string, unknown>;
        const choices = body['choices'] as Array<Record<string, unknown>>;
        const message = choices[0]?.['message'] as Record<string, unknown>;

        expect(getCodexUsageSelectorSnapshot).toHaveBeenCalledTimes(1);
        expect(selectCodexBalanceCandidate).toHaveBeenCalledTimes(1);
        expect(fetchMock.mock.calls.map(([, init]) => authHeader(init))).toEqual(['Bearer token-first']);
        expect(logSpy).toHaveBeenCalledWith(expect.stringContaining('"event":"codex_schedule_shadow_decision"'));
        expect(logSpy).toHaveBeenCalledWith(expect.stringContaining('"scheduled_winner"'));
        expect(logSpy).toHaveBeenCalledWith(expect.stringContaining('"legacy_winner"'));
        expect(message['content']).toBe('legacy slot 0 still answers first in shadow mode');
    });

    it('keeps scheduled selection when selector telemetry is stale', async () => {
        const dir = makeTempDir();
        const firstPath = writeAuth(dir, 'first.json', 'token-first', 'acct-first');
        const secondPath = writeAuth(dir, 'second.json', 'token-second', 'acct-second');
        vi.stubEnv('OPENAI_CODEX_AUTH_PATHS', `${firstPath},${secondPath}`);
        vi.stubEnv('CODEX_BALANCE_LOADER_MODE', 'on');

        const { transport, getCodexUsageSelectorSnapshot, selectCodexBalanceCandidate } = await importTransportWithBalanceLoaderMocks({
            selectorSnapshot: {
                fallbackReason: 'stale_usage',
                accounts: [],
            },
            selectorResult: {
                fallbackReason: null,
                selectedAccountKey: 'acct-second-key',
                selectedSlotIndex: 1,
                affinityApplied: false,
            },
        });
        transport.resetRotationState();

        const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
            const authorization = authHeader(init);
            if (authorization === 'Bearer token-first') {
                return successResponse('legacy slot 0 should stay unused');
            }
            if (authorization === 'Bearer token-second') {
                return successResponse('scheduled slot 1 handled stale telemetry');
            }
            throw new Error(`Unexpected Authorization header: ${authorization}`);
        });
        vi.stubGlobal('fetch', fetchMock);

        const response = await transport.makeCodexRequest(baseRequest, 'codex/gpt-5.4-mini', null);
        const body = await response.json() as Record<string, unknown>;
        const choices = body['choices'] as Array<Record<string, unknown>>;
        const message = choices[0]?.['message'] as Record<string, unknown>;

        expect(getCodexUsageSelectorSnapshot).toHaveBeenCalledTimes(1);
        expect(selectCodexBalanceCandidate).toHaveBeenCalledTimes(1);
        expect(fetchMock.mock.calls.map(([, init]) => authHeader(init))).toEqual(['Bearer token-second']);
        expect(message['content']).toBe('scheduled slot 1 handled stale telemetry');
    });

    it('keeps scheduled selection when selector telemetry is missing', async () => {
        const dir = makeTempDir();
        const firstPath = writeAuth(dir, 'first.json', 'token-first', 'acct-first');
        const secondPath = writeAuth(dir, 'second.json', 'token-second', 'acct-second');
        vi.stubEnv('OPENAI_CODEX_AUTH_PATHS', `${firstPath},${secondPath}`);
        vi.stubEnv('CODEX_BALANCE_LOADER_MODE', 'on');

        const {
            transport,
            getCodexUsage,
            getCodexUsageSelectorSnapshot,
            selectCodexBalanceCandidate,
        } = await importTransportWithBalanceLoaderMocks({
            selectorSnapshot: {
                fallbackReason: 'missing_usage',
                accounts: [],
                missingUsageSlotIndexes: [0, 1],
            },
            selectorResult: {
                fallbackReason: null,
                selectedAccountKey: 'acct-second-key',
                selectedSlotIndex: 1,
                affinityApplied: false,
            },
        });
        transport.resetRotationState();

        const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
            const authorization = authHeader(init);
            if (authorization === 'Bearer token-first') {
                return successResponse('legacy slot 0 should stay unused');
            }
            if (authorization === 'Bearer token-second') {
                return successResponse('scheduled slot 1 handled missing telemetry');
            }
            throw new Error(`Unexpected Authorization header: ${authorization}`);
        });
        vi.stubGlobal('fetch', fetchMock);

        const response = await transport.makeCodexRequest(baseRequest, 'codex/gpt-5.4-mini', null);
        const body = await response.json() as Record<string, unknown>;
        const choices = body['choices'] as Array<Record<string, unknown>>;
        const message = choices[0]?.['message'] as Record<string, unknown>;

        expect(getCodexUsageSelectorSnapshot).toHaveBeenCalledTimes(1);
        expect(getCodexUsage).toHaveBeenCalledWith({ slotIndexes: [0, 1] });
        expect(selectCodexBalanceCandidate).toHaveBeenCalledTimes(1);
        expect(fetchMock.mock.calls.map(([, init]) => authHeader(init))).toEqual(['Bearer token-second']);
        expect(message['content']).toBe('scheduled slot 1 handled missing telemetry');
    });

    it('keeps the leased account while eligible and rotates after an explicit request', async () => {
        const dir = makeTempDir();
        const firstPath = writeAuth(dir, 'first.json', 'token-first', 'acct-first');
        const secondPath = writeAuth(dir, 'second.json', 'token-second', 'acct-second');
        vi.stubEnv('OPENAI_CODEX_AUTH_PATHS', `${firstPath},${secondPath}`);
        vi.stubEnv('CODEX_BALANCE_LOADER_MODE', 'on');

        const { transport, selectCodexBalanceCandidate } = await importTransportWithBalanceLoaderMocks({
            selectorSnapshot: {
                fallbackReason: null,
                accounts: [],
            },
            selectorResult: {
                fallbackReason: null,
                selectedAccountKey: 'acct-second-key',
                selectedSlotIndex: 1,
                affinityApplied: false,
                scores: [
                    { accountKey: 'acct-second-key', slotIndexes: [1] },
                    { accountKey: 'acct-first-key', slotIndexes: [0] },
                ],
            },
        });
        selectCodexBalanceCandidate
            .mockReturnValueOnce({
                fallbackReason: null,
                selectedAccountKey: 'acct-second-key',
                selectedSlotIndex: 1,
                affinityApplied: false,
                scores: [
                    { accountKey: 'acct-second-key', slotIndexes: [1] },
                    { accountKey: 'acct-first-key', slotIndexes: [0] },
                ],
            })
            .mockReturnValue({
                fallbackReason: null,
                selectedAccountKey: 'acct-first-key',
                selectedSlotIndex: 0,
                affinityApplied: false,
                scores: [
                    { accountKey: 'acct-first-key', slotIndexes: [0] },
                    { accountKey: 'acct-second-key', slotIndexes: [1] },
                ],
            });
        transport.resetRotationState();

        const fetchMock = vi.fn(async () => successResponse('ok'));
        vi.stubGlobal('fetch', fetchMock);

        const cacheRequest = { ...baseRequest, prompt_cache_key: 'lease-test' };
        await transport.makeCodexRequest(cacheRequest, 'codex/gpt-5.4-mini', null);
        await transport.makeCodexRequest(cacheRequest, 'codex/gpt-5.4-mini', null);
        transport.forceRotateCodexCacheLease();
        await transport.makeCodexRequest(cacheRequest, 'codex/gpt-5.4-mini', null);

        expect(fetchMock.mock.calls.map(([, init]) => authHeader(init))).toEqual([
            'Bearer token-second',
            'Bearer token-second',
            'Bearer token-first',
        ]);
    });

    it('does not reuse the active cache lease for unrelated requests without a cache key', async () => {
        const dir = makeTempDir();
        const firstPath = writeAuth(dir, 'first.json', 'token-first', 'acct-first');
        const secondPath = writeAuth(dir, 'second.json', 'token-second', 'acct-second');
        vi.stubEnv('OPENAI_CODEX_AUTH_PATHS', `${firstPath},${secondPath}`);
        vi.stubEnv('CODEX_BALANCE_LOADER_MODE', 'on');

        const { transport, selectCodexBalanceCandidate } = await importTransportWithBalanceLoaderMocks({
            selectorSnapshot: {
                fallbackReason: null,
                accounts: [],
            },
            selectorResult: {
                fallbackReason: null,
                selectedAccountKey: 'acct-second-key',
                selectedSlotIndex: 1,
                affinityApplied: false,
                scores: [
                    { accountKey: 'acct-second-key', slotIndexes: [1] },
                    { accountKey: 'acct-first-key', slotIndexes: [0] },
                ],
            },
        });
        selectCodexBalanceCandidate
            .mockReturnValueOnce({
                fallbackReason: null,
                selectedAccountKey: 'acct-second-key',
                selectedSlotIndex: 1,
                affinityApplied: false,
                scores: [
                    { accountKey: 'acct-second-key', slotIndexes: [1] },
                    { accountKey: 'acct-first-key', slotIndexes: [0] },
                ],
            })
            .mockReturnValue({
                fallbackReason: null,
                selectedAccountKey: 'acct-first-key',
                selectedSlotIndex: 0,
                affinityApplied: false,
                scores: [
                    { accountKey: 'acct-first-key', slotIndexes: [0] },
                    { accountKey: 'acct-second-key', slotIndexes: [1] },
                ],
            });
        transport.resetRotationState();

        const fetchMock = vi.fn(async () => successResponse('ok'));
        vi.stubGlobal('fetch', fetchMock);

        await transport.makeCodexRequest({ ...baseRequest, prompt_cache_key: 'lease-test' }, 'codex/gpt-5.4-mini', null);
        await transport.makeCodexRequest(baseRequest, 'codex/gpt-5.4-mini', null);

        expect(fetchMock.mock.calls.map(([, init]) => authHeader(init))).toEqual([
            'Bearer token-second',
            'Bearer token-first',
        ]);
    });
});
