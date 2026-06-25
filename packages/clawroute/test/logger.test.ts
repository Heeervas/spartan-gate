import { afterEach, describe, expect, it, vi } from 'vitest';
import initSqlJs from 'sql.js';
import { mkdtempSync, readdirSync, rmSync, writeFileSync } from 'fs';
import { tmpdir } from 'os';
import { join } from 'path';
import {
    closeDb,
    approveCodexColdMigrationDecision,
    dismissCodexColdMigrationDecision,
    getCodexColdMigrationDecision,
    getCodexAnalysis,
    getCodexActivationCheckpoints,
    getCodexBalancerSettings,
    getCodexBalancerSlotOverrides,
    getCodexAccountSchedule,
    getCodexPromptCacheUsage,
    getCodexResetCreditSnapshots,
    getCodexUsageSnapshots,
    getLiveRoutingSessions,
    getRecentDecisions,
    getRecentSessionTraceCandidates,
    getRecentTurnTraceCandidates,
    getTurnRequests,
    initDb,
    logRouting,
    upsertCodexColdMigrationDecision,
    seedCodexAccountSchedule,
    setCodexBalancerSettings,
    setCodexActivationCheckpoint,
    setCodexBalancerSlotOverride,
    upsertCodexResetCreditSnapshots,
    upsertCodexUsageSnapshots,
} from '../src/logger.js';
import { ClawRouteConfig, LogEntry, TaskTier } from '../src/types.js';

const tempDirs: string[] = [];

function createTempDir(): string {
    const dir = mkdtempSync(join(tmpdir(), 'clawroute-logger-'));
    tempDirs.push(dir);
    return dir;
}

function createTestConfig(dbPath: string): ClawRouteConfig {
    return {
        enabled: true,
        dryRun: false,
        baselineModel: 'openrouter/anthropic/claude-sonnet-4.6',
        providerProfile: null,
        proxyPort: 18799,
        proxyHost: '127.0.0.1',
        authToken: null,
        classification: {
            conservativeMode: true,
            minConfidence: 0.7,
            toolAwareRouting: true,
        },
        escalation: {
            enabled: true,
            maxRetries: 2,
            retryDelayMs: 10,
            onlyRetryBeforeStreaming: true,
            onlyRetryWithoutToolCalls: true,
            alwaysFallbackToOriginal: true,
        },
        models: {
            [TaskTier.HEARTBEAT]: { primary: 'google/gemini-2.5-flash-lite', fallback: 'deepseek/deepseek-chat' },
            [TaskTier.SIMPLE]: { primary: 'deepseek/deepseek-chat', fallback: 'google/gemini-2.5-flash' },
            [TaskTier.MODERATE]: { primary: 'google/gemini-2.5-flash', fallback: 'openai/gpt-5-mini' },
            [TaskTier.COMPLEX]: { primary: 'anthropic/claude-sonnet-4-6', fallback: 'openai/gpt-5.2' },
            [TaskTier.FRONTIER_SONNET]: { primary: 'anthropic/claude-sonnet-4-6', fallback: 'openai/gpt-5' },
            [TaskTier.FRONTIER_OPUS]: { primary: 'anthropic/claude-opus-4-6', fallback: 'openai/o3' },
        },
        logging: {
            dbPath,
            logContent: false,
            logSystemPrompts: false,
            debugMode: false,
            retentionDays: 30,
        },
        dashboard: { enabled: true },
        overrides: { globalForceModel: null, sessions: {} },
        apiKeys: {
            anthropic: 'test-key',
            openai: 'test-key',
            codex: 'test-key',
            google: 'test-key',
            deepseek: 'test-key',
            openrouter: 'test-key',
            ollama: '',
            'x-ai': '',
            stepfun: '',
        },
        alerts: {},
    };
}

function createLogEntry(): LogEntry {
    return {
        timestamp: new Date().toISOString(),
        original_model: 'openai/gpt-5-mini',
        routed_model: 'deepseek/deepseek-chat',
        actual_model: 'deepseek/deepseek-chat',
        tier: TaskTier.SIMPLE,
        classification_reason: 'test',
        confidence: 0.9,
        input_tokens: 12,
        output_tokens: 8,
        original_cost_usd: 0.1,
        actual_cost_usd: 0.01,
        savings_usd: 0.09,
        escalated: false,
        escalation_chain: '[]',
        response_time_ms: 25,
        had_tool_calls: false,
        is_dry_run: false,
        is_override: false,
        session_id: null,
        error: null,
        prompt_preview: null,
        context_info: null,
    };
}

afterEach(() => {
    closeDb();
    vi.restoreAllMocks();
    vi.useRealTimers();

    while (tempDirs.length > 0) {
        const dir = tempDirs.pop();
        if (dir) {
            rmSync(dir, { recursive: true, force: true });
        }
    }
});

describe('logger startup recovery', () => {
    it('adds session-turn trace columns to an existing routing log', async () => {
        const dataDir = createTempDir();
        const dbPath = join(dataDir, 'clawroute.db');
        const SQL = await initSqlJs();
        const legacy = new SQL.Database();
        legacy.run(`CREATE TABLE routing_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL,
            original_model TEXT NOT NULL, routed_model TEXT NOT NULL, actual_model TEXT NOT NULL,
            tier TEXT NOT NULL, classification_reason TEXT NOT NULL DEFAULT '', confidence REAL NOT NULL DEFAULT 0,
            input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0,
            original_cost_usd REAL NOT NULL DEFAULT 0, actual_cost_usd REAL NOT NULL DEFAULT 0,
            savings_usd REAL NOT NULL DEFAULT 0, escalated INTEGER NOT NULL DEFAULT 0,
            escalation_chain TEXT NOT NULL DEFAULT '[]', response_time_ms INTEGER NOT NULL DEFAULT 0,
            had_tool_calls INTEGER NOT NULL DEFAULT 0, is_dry_run INTEGER NOT NULL DEFAULT 0,
            is_override INTEGER NOT NULL DEFAULT 0, session_id TEXT, error TEXT
        )`);
        writeFileSync(dbPath, legacy.export());
        legacy.close();

        await initDb(createTestConfig(dbPath));
        logRouting({
            ...createLogEntry(),
            session_id: 'session-a',
            turn_id: 'turn-a',
            request_id: 'request-a',
        });

        expect(getRecentDecisions(1)[0]).toMatchObject({
            sessionId: 'session-a',
            turnId: 'turn-a',
            requestId: 'request-a',
        });
    });

    it('recreates a fresh database when the persisted file is malformed', async () => {
        const dataDir = createTempDir();
        const dbPath = join(dataDir, 'clawroute.db');
        const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
        const config = createTestConfig(dbPath);

        writeFileSync(dbPath, 'not a sqlite database');

        await expect(initDb(config)).resolves.toBeUndefined();

        logRouting(createLogEntry());

        const backupFiles = readdirSync(dataDir).filter((name) =>
            name.startsWith('clawroute.db.corrupt-')
        );

        expect(backupFiles).toHaveLength(1);
        expect(getRecentDecisions(10)).toHaveLength(1);
        expect(warnSpy).toHaveBeenCalledWith(
            expect.stringContaining('Recovered corrupted ClawRoute database')
        );
    });
});

describe('logger debounced persistence', () => {
    it('returns sanitized request context with recent routing decisions', async () => {
        await initDb(createTestConfig(':memory:'));
        logRouting({
            ...createLogEntry(),
            actual_model: 'deepseek/deepseek-chat-v3',
            confidence: 0.87,
            input_tokens: 42,
            cached_input_tokens: 30,
            output_tokens: 13,
            had_tool_calls: true,
            is_override: true,
            prompt_preview: 'Explain why this route was selected',
            context_info: JSON.stringify({
                msg_count: 4,
                has_system: true,
                tool_count: 2,
                last_role: 'user',
                message_chars: 1200,
                tool_schema_chars: 8000,
                tool_schema_rough_tokens: 2000,
                top_tool_schema_groups: [{ key: 'mcp:gtm', tools: 2, chars: 8000 }],
                bloat_alerts: ['large_tool_schemas'],
            }),
        });

        expect(getRecentDecisions(1)[0]).toMatchObject({
            actualModel: 'deepseek/deepseek-chat-v3',
            confidence: 0.87,
            inputTokens: 42,
            cachedInputTokens: 30,
            outputTokens: 13,
            hadToolCalls: true,
            isOverride: true,
            promptPreview: 'Explain why this route was selected',
            context: {
                messageCount: 4,
                hasSystem: true,
                toolCount: 2,
                lastRole: 'user',
                messageChars: 1200,
                toolSchemaChars: 8000,
                toolSchemaRoughTokens: 2000,
                topToolSchemaGroups: [{ key: 'mcp:gtm', tools: 2, chars: 8000 }],
                bloatAlerts: ['large_tool_schemas'],
            },
        });
    });

    it('aggregates recent Codex prompt cache usage', async () => {
        await initDb(createTestConfig(':memory:'));
        logRouting({
            ...createLogEntry(),
            actual_model: 'codex/gpt-5.4',
            input_tokens: 100,
            cached_input_tokens: 75,
        });

        expect(getCodexPromptCacheUsage(24)).toEqual({
            inputTokens: 100,
            cachedInputTokens: 75,
            cachedPercent: 75,
            periodHours: 24,
        });
    });

    it('aggregates sessions and turns while preserving chronological request detail', async () => {
        await initDb(createTestConfig(':memory:'));
        const trace = (fingerprint: string, messageCount: number, toolCalls = 0, toolResults = 0) => JSON.stringify({
            msg_count: messageCount,
            has_system: true,
            tool_count: 2,
            last_role: toolResults ? 'tool' : 'user',
            tool_schema_fingerprint: 'tools-v1',
            request_trace: {
                version: 1,
                sessionSource: 'prompt_cache_key',
                requestFingerprint: fingerprint,
                messageFingerprints: Array.from({ length: messageCount }, (_, index) => `prefix-${index + 1}`),
                parentRequestId: null,
                phase: toolResults ? 'tool_results' : 'user_input',
                delta: {
                    comparison: 'prefix', addedMessageCount: 1, removedMessageCount: 0, addedChars: 10,
                    roleCounts: {}, toolCallCount: toolCalls, toolResultCount: toolResults,
                    items: [], omittedItems: 0,
                    toolSchemas: { status: 'unchanged', count: 2, chars: 20 },
                },
            },
        });
        logRouting({
            ...createLogEntry(),
            timestamp: '2026-06-19T08:00:00.000Z',
            request_id: 'request-1',
            session_id: 'session-a',
            turn_id: 'turn-a',
            prompt_preview: 'Inspect this request',
            input_tokens: 100,
            cached_input_tokens: 60,
            output_tokens: 10,
            response_time_ms: 100,
            context_info: trace('prefix-2', 2),
            routed_model: 'codex/gpt-5.5',
            actual_model: 'codex/gpt-5.5',
            selected_codex_slot_index: 0,
        });
        logRouting({
            ...createLogEntry(),
            timestamp: '2026-06-19T08:00:01.000Z',
            request_id: 'request-2',
            session_id: 'session-a',
            turn_id: 'turn-a',
            input_tokens: 150,
            cached_input_tokens: 100,
            output_tokens: 5,
            response_time_ms: 300,
            context_info: trace('prefix-4', 4, 1, 1),
            routed_model: 'codex/gpt-5.5',
            actual_model: 'codex/gpt-5.5',
            selected_codex_slot_index: null,
        });

        upsertCodexUsageSnapshots([{
            accountKey: 'acct-a', slotIndex: 0, window: 'fiveHour', usedPercent: 5,
            resetAt: '2026-06-19T13:00:00.000Z', windowMinutes: 300,
            updatedAt: '2026-06-19T09:00:00.000Z',
        }, {
            accountKey: 'acct-a', slotIndex: 0, window: 'weekly', usedPercent: 20,
            resetAt: '2026-06-26T08:00:00.000Z', windowMinutes: 10_080,
            updatedAt: '2026-06-19T09:00:00.000Z',
        }]);

        const live = getLiveRoutingSessions(10, 20, 30, new Date('2026-06-19T10:00:00.000Z'));
        expect(live.sessions).toHaveLength(1);
        expect(live.sessions[0]).toMatchObject({
            id: 'session-a',
            turnCount: 1,
            metrics: {
                requests: 2,
                inputTokens: 250,
                cachedInputTokens: 160,
                uncachedInputTokens: 90,
                outputTokens: 15,
                uncachedPlusOutputTokens: 105,
                averageResponseMs: 200,
                toolCalls: 1,
                toolResults: 1,
                codexTokens: 265,
                attributedCodexTokens: 110,
                quotaCoveragePercent: expect.closeTo((110 / 265) * 100, 6),
            },
            turns: [{
                id: 'turn-a',
                promptPreview: 'Inspect this request',
                metrics: { requests: 2, toolCalls: 1, toolResults: 1 },
            }],
        });
        expect(live.quotaCalibration).toMatchObject({
            source: 'calibrated_total_tokens',
            periodDays: 7,
            fiveHourBurstSensitive: true,
            fiveHour: { observedQuotaDelta: 5, totalTokens: 110 },
            weekly: { observedQuotaDelta: 20, totalTokens: 110 },
        });
        expect(getTurnRequests('turn-a').requests.map((entry) => entry.requestId)).toEqual(['request-1', 'request-2']);
        expect(getRecentTurnTraceCandidates('turn-a')[0]).toMatchObject({
            requestId: 'request-2',
            requestFingerprint: 'prefix-4',
            messageCount: 4,
        });
    });

    it('excludes policy-block rows from request trace parent candidates', async () => {
        await initDb(createTestConfig(':memory:'));
        const trace = (fingerprint: string, messageCount: number, policyBlock = false) => JSON.stringify({
            tool_schema_fingerprint: 'tools-v1',
            request_trace: {
                version: 1,
                sessionSource: 'prompt_cache_key',
                requestFingerprint: fingerprint,
                messageFingerprints: Array.from({ length: messageCount }, (_, index) => `prefix-${index + 1}`),
                parentRequestId: null,
                phase: 'tool_results',
                delta: {
                    comparison: 'prefix',
                    addedMessageCount: 1,
                    removedMessageCount: 0,
                    addedChars: 10,
                    roleCounts: { tool: 1 },
                    toolCallCount: 0,
                    toolResultCount: 1,
                    items: [],
                    omittedItems: 0,
                    toolSchemas: { status: 'unchanged', count: 0, chars: 2 },
                },
            },
            ...(policyBlock ? {
                policy_block: {
                    policy: 'codex_cache_miss_breaker',
                    breaker_id: 'breaker-a',
                    source: 'preflight',
                },
            } : {}),
        });

        logRouting({
            ...createLogEntry(),
            request_id: 'request-ok',
            session_id: 'session-a',
            turn_id: 'turn-a',
            context_info: trace('prefix-ok', 3),
        });
        logRouting({
            ...createLogEntry(),
            request_id: 'request-blocked',
            session_id: 'session-a',
            turn_id: 'turn-a',
            input_tokens: 0,
            output_tokens: 0,
            error: 'codex_cache_miss_breaker',
            context_info: trace('prefix-blocked', 4, true),
        });

        expect(getRecentTurnTraceCandidates('turn-a')).toEqual([
            expect.objectContaining({
                requestId: 'request-ok',
                requestFingerprint: 'prefix-ok',
                messageCount: 3,
            }),
        ]);
        expect(getRecentDecisions(2)[0]).toMatchObject({
            error: 'codex_cache_miss_breaker',
            inputTokens: 0,
            outputTokens: 0,
            context: {
                policyBlock: {
                    policy: 'codex_cache_miss_breaker',
                    breakerId: 'breaker-a',
                    source: 'preflight',
                },
            },
        });
    });

    it('returns bounded same-session trace candidates for the same cache key only', async () => {
        await initDb(createTestConfig(':memory:'));
        const trace = (fingerprint: string, messageCount: number, cacheKeyHash: string, policyBlock = false) => JSON.stringify({
            cache_key_hash: cacheKeyHash,
            tool_schema_fingerprint: 'tools-v1',
            request_trace: {
                version: 1,
                sessionSource: 'prompt_cache_key',
                requestFingerprint: fingerprint,
                messageFingerprints: Array.from({ length: messageCount }, (_, index) => `prefix-${index + 1}`),
                parentRequestId: null,
                phase: 'tool_results',
                delta: {
                    comparison: 'prefix',
                    addedMessageCount: 1,
                    removedMessageCount: 0,
                    addedChars: 10,
                    roleCounts: { tool: 1 },
                    toolCallCount: 0,
                    toolResultCount: 1,
                    items: [],
                    omittedItems: 0,
                    toolSchemas: { status: 'unchanged', count: 0, chars: 2 },
                },
            },
            ...(policyBlock ? {
                policy_block: {
                    policy: 'codex_cache_miss_breaker',
                    breaker_id: 'breaker-a',
                    source: 'preflight',
                },
            } : {}),
        });

        logRouting({
            ...createLogEntry(),
            request_id: 'request-other-cache',
            session_id: 'session-a',
            turn_id: 'turn-a',
            context_info: trace('prefix-other-cache', 2, 'cache-b'),
        });
        logRouting({
            ...createLogEntry(),
            request_id: 'request-ok',
            session_id: 'session-a',
            turn_id: 'turn-a',
            context_info: trace('prefix-ok', 3, 'cache-a'),
        });
        logRouting({
            ...createLogEntry(),
            request_id: 'request-blocked',
            session_id: 'session-a',
            turn_id: 'turn-b',
            input_tokens: 0,
            output_tokens: 0,
            error: 'codex_cache_miss_breaker',
            context_info: trace('prefix-blocked', 4, 'cache-a', true),
        });
        logRouting({
            ...createLogEntry(),
            request_id: 'request-other-session',
            session_id: 'session-b',
            turn_id: 'turn-c',
            context_info: trace('prefix-other-session', 5, 'cache-a'),
        });

        expect(getRecentSessionTraceCandidates('session-a', 'cache-a')).toEqual([
            expect.objectContaining({
                requestId: 'request-ok',
                requestFingerprint: 'prefix-ok',
                messageCount: 3,
            }),
        ]);
    });

    it('excludes non-Codex requests from the quota-estimation token basis', async () => {
        await initDb(createTestConfig(':memory:'));
        logRouting({
            ...createLogEntry(),
            request_id: 'request-non-codex',
            session_id: 'session-non-codex',
            turn_id: 'turn-non-codex',
            input_tokens: 1_000,
            output_tokens: 100,
            selected_codex_slot_index: 0,
        });

        expect(getLiveRoutingSessions().sessions[0]?.metrics).toMatchObject({
            inputTokens: 1_000,
            outputTokens: 100,
            codexTokens: 0,
            attributedCodexTokens: 0,
            quotaCoveragePercent: 0,
        });
    });

    it('exposes analysis summaries, daily rollups, and new telemetry dimensions', async () => {
        await initDb(createTestConfig(':memory:'));
        logRouting({
            ...createLogEntry(),
            timestamp: '2026-06-11T08:00:00.000Z',
            actual_model: 'codex/gpt-5.5',
            tier: TaskTier.COMPLEX,
            input_tokens: 100,
            cached_input_tokens: 40,
            output_tokens: 12,
            request_api_kind: 'responses',
            requested_reasoning_effort: 'high',
            selected_codex_slot_index: 2,
            selected_codex_account_key: 'acct-hash',
            session_id: 'session-a',
            context_info: JSON.stringify({
                msg_count: 12,
                has_system: true,
                tool_count: 132,
                last_role: 'user',
                cache_key_present: true,
                cache_key_hash: 'abc123def456',
                message_chars: 36000,
                tool_schema_chars: 158000,
                tool_schema_rough_tokens: 39500,
                top_tool_schema_groups: [
                    { key: 'mcp:analytics_mcp', tools: 22, chars: 39000 },
                    { key: 'mcp:measure_plan', tools: 18, chars: 31000 },
                ],
                bloat_alerts: ['many_tools', 'large_tool_schemas'],
            }),
        });
        upsertCodexUsageSnapshots([
            {
                accountKey: 'acct-hash',
                slotIndex: 2,
                window: 'fiveHour',
                usedPercent: 10,
                resetAt: '2026-06-11T12:00:00.000Z',
                windowMinutes: 300,
                updatedAt: '2026-06-11T07:55:00.000Z',
            },
            {
                accountKey: 'acct-hash',
                slotIndex: 2,
                window: 'fiveHour',
                usedPercent: 64,
                resetAt: '2026-06-11T12:00:00.000Z',
                windowMinutes: 300,
                updatedAt: '2026-06-11T08:05:00.000Z',
            },
        ]);

        const analysis = getCodexAnalysis({
            periodKey: 'custom',
            start: '2026-06-11T00:00:00.000Z',
            end: '2026-06-12T00:00:00.000Z',
        });

        expect(analysis.summary).toMatchObject({
            requests: 1,
            inputTokens: 100,
            cachedInputTokens: 40,
            outputTokens: 12,
        });
        expect(analysis.apiKindMix).toEqual([
            { key: 'responses', requests: 1, requestSharePercent: 100 },
        ]);
        expect(analysis.reasoningEffortMix).toEqual([
            { key: 'high', requests: 1, requestSharePercent: 100 },
        ]);
        expect(analysis.quotaHistory).toHaveLength(2);
        expect(analysis.dailyRollups[0]).toMatchObject({
            day: '2026-06-11',
            actualModel: 'codex/gpt-5.5',
            requestApiKind: 'responses',
            requestedReasoningEffort: 'high',
            requests: 1,
        });
        expect(analysis.summary.apiCostUsd).toBeGreaterThan(0);
        expect(analysis.quotaCalibration).toEqual(expect.arrayContaining([
            expect.objectContaining({
                window: 'fiveHour',
                observedQuotaDelta: 64,
                quotaPctPerMillionTotalTokens: expect.any(Number),
            }),
        ]));
        expect(analysis.slotUsageEstimates).toEqual(expect.arrayContaining([
            expect.objectContaining({
                slotIndex: 2,
                window: 'fiveHour',
                actualQuotaDelta: 64,
                expectedQuotaDelta: expect.any(Number),
                apiCostUsd: expect.any(Number),
            }),
        ]));
        expect(analysis.dailySlotUsage).toEqual(expect.arrayContaining([
            expect.objectContaining({
                bucket: '2026-06-11',
                slotIndex: 2,
                fiveHourActualQuotaDelta: 64,
                fiveHourExpectedQuotaDelta: expect.any(Number),
            }),
        ]));
        expect(analysis.expensiveConversations).toEqual([
            expect.objectContaining({
                key: 'cache:abc123def456',
                requests: 1,
                inputTokens: 100,
                maxToolCount: 132,
                maxToolSchemaChars: 158000,
                alertCount: 2,
                alerts: ['large_tool_schemas', 'many_tools'],
                slots: [2],
                models: ['codex/gpt-5.5'],
                topToolSchemaGroups: [
                    { key: 'mcp:analytics_mcp', tools: 22, chars: 39000 },
                    { key: 'mcp:measure_plan', tools: 18, chars: 31000 },
                ],
            }),
        ]);
        expect(analysis.apiPricing).toMatchObject({
            source: 'https://developers.openai.com/api/docs/pricing',
            currency: 'USD',
            unit: 'per_1m_tokens',
        });
        expect(analysis.flags).toEqual({
            hasQuotaHistory: true,
            hasSelectedCodexAttribution: true,
            hasReasoningEffort: true,
        });
    });

    it('does not include ISO routing timestamps before the active quota window', async () => {
        await initDb(createTestConfig(':memory:'));
        logRouting({
            ...createLogEntry(),
            timestamp: '2026-06-11T06:50:00.000Z',
            actual_model: 'codex/gpt-5.5',
            input_tokens: 1_000_000,
            cached_input_tokens: 0,
            output_tokens: 0,
            selected_codex_slot_index: 2,
            selected_codex_account_key: 'acct-hash',
        });
        logRouting({
            ...createLogEntry(),
            timestamp: '2026-06-11T08:00:00.000Z',
            actual_model: 'codex/gpt-5.5',
            input_tokens: 100,
            cached_input_tokens: 40,
            output_tokens: 12,
            selected_codex_slot_index: 2,
            selected_codex_account_key: 'acct-hash',
        });
        upsertCodexUsageSnapshots([
            {
                accountKey: 'acct-hash',
                slotIndex: 2,
                window: 'fiveHour',
                usedPercent: 64,
                resetAt: '2026-06-11T12:00:00.000Z',
                windowMinutes: 300,
                updatedAt: '2026-06-11T08:05:00.000Z',
            },
        ]);

        const analysis = getCodexAnalysis({
            periodKey: 'custom',
            start: '2026-06-11T00:00:00.000Z',
            end: '2026-06-12T00:00:00.000Z',
        });
        expect(analysis.slotUsageEstimates).toEqual([
            expect.objectContaining({
                slotIndex: 2,
                window: 'fiveHour',
                requests: 1,
                inputTokens: 100,
                outputTokens: 12,
            }),
        ]);
    });

    it('excludes ISO timestamps outside the requested cache window', async () => {
        await initDb(createTestConfig(':memory:'));
        logRouting({
            ...createLogEntry(),
            timestamp: new Date(Date.now() - 25 * 60 * 60 * 1000).toISOString(),
            actual_model: 'codex/gpt-5.4',
            input_tokens: 900,
            cached_input_tokens: 900,
        });
        logRouting({
            ...createLogEntry(),
            actual_model: 'codex/gpt-5.4',
            input_tokens: 100,
            cached_input_tokens: 50,
        });

        expect(getCodexPromptCacheUsage(24)).toMatchObject({
            inputTokens: 100,
            cachedInputTokens: 50,
            cachedPercent: 50,
        });
    });

    it('makes log rows queryable immediately and flushes pending disk writes on close', async () => {
        const dataDir = createTempDir();
        const dbPath = join(dataDir, 'clawroute.db');

        await initDb(createTestConfig(dbPath));
        vi.useFakeTimers();
        logRouting(createLogEntry());

        expect(getRecentDecisions(10)).toHaveLength(1);

        closeDb();
        vi.useRealTimers();

        await initDb(createTestConfig(dbPath));
        expect(getRecentDecisions(10)).toHaveLength(1);
    });

    it('persists scheduled log writes after the debounce window', async () => {
        const dataDir = createTempDir();
        const dbPath = join(dataDir, 'clawroute.db');

        await initDb(createTestConfig(dbPath));
        vi.useFakeTimers();
        logRouting(createLogEntry());

        await vi.advanceTimersByTimeAsync(250);
        closeDb();
        vi.useRealTimers();

        await initDb(createTestConfig(dbPath));
        expect(getRecentDecisions(10)).toHaveLength(1);
    });
});

describe('Codex account schedule persistence', () => {
    it('seeds account lanes append-only and keeps existing rows stable', async () => {
        const dataDir = createTempDir();
        const dbPath = join(dataDir, 'clawroute.db');

        await initDb(createTestConfig(dbPath));

        const first = seedCodexAccountSchedule(['acct-a', 'acct-b', 'acct-a'], 1, { flush: true });
        expect(first).toMatchObject([
            { accountKey: 'acct-a', seedOrder: 0, anchorWeekday: 1, laneRank: 0 },
            { accountKey: 'acct-b', seedOrder: 1, anchorWeekday: 2, laneRank: 0 },
        ]);

        const second = seedCodexAccountSchedule(['acct-c', 'acct-a'], 1, { flush: true });
        expect(second).toMatchObject([
            { accountKey: 'acct-a', seedOrder: 0, anchorWeekday: 1, laneRank: 0 },
            { accountKey: 'acct-b', seedOrder: 1, anchorWeekday: 2, laneRank: 0 },
            { accountKey: 'acct-c', seedOrder: 2, anchorWeekday: 3, laneRank: 0 },
        ]);
    });

    it('rebuilds object schedules deterministically by slot when an account changes', async () => {
        await initDb(createTestConfig(':memory:'));

        seedCodexAccountSchedule([
            { slotIndex: 0, accountKey: 'acct-a' },
            { slotIndex: 1, accountKey: 'acct-b' },
        ], 1);
        const replaced = seedCodexAccountSchedule([
            { slotIndex: 0, accountKey: 'acct-new' },
            { slotIndex: 1, accountKey: 'acct-b' },
        ], 1);

        expect(replaced).toMatchObject([
            { slotIndex: 0, accountKey: 'acct-new', anchorWeekday: 1 },
            { slotIndex: 1, accountKey: 'acct-b', anchorWeekday: 2 },
        ]);
    });

    it('clears stale slot telemetry when an object schedule account changes', async () => {
        await initDb(createTestConfig(':memory:'));

        seedCodexAccountSchedule([{ slotIndex: 0, accountKey: 'acct-old' }], 1);
        upsertCodexUsageSnapshots([{
            accountKey: 'acct-old',
            slotIndex: 0,
            window: 'weekly',
            usedPercent: 91,
            resetAt: '2026-06-18T09:00:00.000Z',
            windowMinutes: 10080,
            updatedAt: '2026-06-16T09:00:00.000Z',
        }]);
        setCodexActivationCheckpoint({
            slotIndex: 0,
            accountKey: 'acct-old',
            expectedWeeklyResetAt: '2026-06-18T09:00:00.000Z',
            lastUsageCheckAt: '2026-06-16T09:00:00.000Z',
            updatedAt: '2026-06-16T09:00:00.000Z',
        });
        upsertCodexResetCreditSnapshots([{
            accountKey: 'acct-old',
            slotIndex: 0,
            availableCount: 1,
            detailsAvailable: true,
            source: 'live',
            updatedAt: '2026-06-16T09:00:00.000Z',
            credits: [{
                creditKey: 'credit-old',
                status: 'available',
                resetType: null,
                title: null,
                grantedAt: '2026-06-11T09:00:00.000Z',
                expiresAt: '2026-07-11T09:00:00.000Z',
                redeemedAt: null,
            }],
        }]);

        seedCodexAccountSchedule([{ slotIndex: 0, accountKey: 'acct-new' }], 1);

        expect(getCodexAccountSchedule()).toMatchObject([
            { slotIndex: 0, accountKey: 'acct-new' },
        ]);
        expect(getCodexUsageSnapshots()).toEqual([]);
        expect(getCodexActivationCheckpoints()).toEqual([]);
        expect(getCodexResetCreditSnapshots()).toEqual([]);
    });

    it('replaces stale reset credit telemetry when a slot account changes', async () => {
        await initDb(createTestConfig(':memory:'));

        upsertCodexResetCreditSnapshots([{
            accountKey: 'acct-old',
            slotIndex: 0,
            availableCount: 1,
            detailsAvailable: true,
            source: 'live',
            updatedAt: '2026-06-16T09:00:00.000Z',
            credits: [{
                creditKey: 'credit-old',
                status: 'available',
                resetType: 'codex',
                title: 'Old reset',
                grantedAt: '2026-06-11T09:00:00.000Z',
                expiresAt: '2026-07-11T09:00:00.000Z',
                redeemedAt: null,
            }],
        }]);
        upsertCodexResetCreditSnapshots([{
            accountKey: 'acct-new',
            slotIndex: 0,
            availableCount: 0,
            detailsAvailable: false,
            source: 'liveCountOnly',
            updatedAt: '2026-06-17T09:00:00.000Z',
            credits: [],
        }]);

        expect(getCodexResetCreditSnapshots()).toEqual([
            expect.objectContaining({
                accountKey: 'acct-new',
                slotIndex: 0,
                availableCount: 0,
                detailsAvailable: false,
                credits: [],
            }),
        ]);
    });

    it('does not persist reset credit labels that can expose raw limit metadata', async () => {
        await initDb(createTestConfig(':memory:'));

        upsertCodexResetCreditSnapshots([{
            accountKey: 'acct-one',
            slotIndex: 0,
            availableCount: 1,
            detailsAvailable: true,
            source: 'live',
            updatedAt: '2026-06-16T09:00:00.000Z',
            credits: [{
                creditKey: 'credit-one',
                status: 'available',
                resetType: 'raw-limit-id',
                title: 'description-derived title',
                grantedAt: '2026-06-11T09:00:00.000Z',
                expiresAt: '2026-07-11T09:00:00.000Z',
                redeemedAt: null,
            }],
        }]);

        expect(getCodexResetCreditSnapshots()).toEqual([
            expect.objectContaining({
                accountKey: 'acct-one',
                credits: [expect.objectContaining({
                    creditKey: 'credit-one',
                    resetType: null,
                    title: null,
                })],
            }),
        ]);
    });

    it('allows duplicate account keys in distinct slot schedule rows', async () => {
        await initDb(createTestConfig(':memory:'));

        const schedule = seedCodexAccountSchedule([
            { slotIndex: 0, accountKey: 'acct-shared' },
            { slotIndex: 1, accountKey: 'acct-shared' },
        ], 1);

        expect(schedule).toMatchObject([
            { slotIndex: 0, accountKey: 'acct-shared', anchorWeekday: 1 },
            { slotIndex: 1, accountKey: 'acct-shared', anchorWeekday: 2 },
        ]);
    });

    it('persists balancer settings and independent slot overrides', async () => {
        await initDb(createTestConfig(':memory:'));

        setCodexBalancerSettings({
            mode: 'shadow',
            earlyActivationWeeklyPercent: 75,
            coldMigrationFiveHourThresholdPercent: 8,
        });
        setCodexBalancerSlotOverride({
            slotIndex: 2,
            enabled: false,
            manualActivationCycleResetAt: null,
            updatedAt: '2026-06-08T00:00:00.000Z',
        });

        expect(getCodexBalancerSettings()).toMatchObject({
            mode: 'shadow',
            earlyActivationWeeklyPercent: 75,
            coldMigrationFiveHourThresholdPercent: 8,
        });
        expect(getCodexBalancerSlotOverrides()).toMatchObject([
            { slotIndex: 2, enabled: false },
        ]);
    });

    it('persists and resolves Codex cold migration decisions', async () => {
        await initDb(createTestConfig(':memory:'));

        const decision = upsertCodexColdMigrationDecision({
            id: 'decision-1',
            expiresAt: new Date(Date.now() + 60_000).toISOString(),
            sessionKey: 'session-hash',
            previousAccountKey: 'acct-old',
            previousSlotIndex: 2,
            targetAccountKey: 'acct-new',
            targetSlotIndex: 1,
            estimatedInputTokens: 900_000,
            estimatedFiveHourPercent: 12.5,
            thresholdFiveHourPercent: 7,
            previousFiveHourUsedPercent: 100,
            previousFiveHourRemainingPercent: 0,
            targetFiveHourUsedPercent: 1,
            targetFiveHourRemainingPercent: 99,
            targetWeeklyUsedPercent: 0,
            targetWeeklyRemainingPercent: 100,
        });

        expect(decision).toMatchObject({
            id: 'decision-1',
            status: 'pending',
            estimatedFiveHourPercent: 12.5,
        });
        expect(approveCodexColdMigrationDecision('decision-1')).toMatchObject({ status: 'approved' });
        expect(dismissCodexColdMigrationDecision('decision-1')).toMatchObject({ status: 'approved' });
        expect(getCodexColdMigrationDecision('decision-1')).toMatchObject({
            targetSlotIndex: 1,
            status: 'approved',
        });
    });

    it('persists account-bound activation reset checkpoints', async () => {
        await initDb(createTestConfig(':memory:'));

        setCodexActivationCheckpoint({
            slotIndex: 3,
            accountKey: 'acct-checkpoint',
            expectedWeeklyResetAt: '2026-06-11T09:30:00.000Z',
            lastUsageCheckAt: '2026-06-04T09:30:00.000Z',
            updatedAt: '2026-06-04T09:30:00.000Z',
        });

        expect(getCodexActivationCheckpoints()).toEqual([
            expect.objectContaining({
                slotIndex: 3,
                accountKey: 'acct-checkpoint',
                expectedWeeklyResetAt: '2026-06-11T09:30:00.000Z',
            }),
        ]);
    });

    it('flushes seeded rows synchronously when requested', async () => {
        const dataDir = createTempDir();
        const dbPath = join(dataDir, 'clawroute.db');

        await initDb(createTestConfig(dbPath));
        vi.useFakeTimers();
        seedCodexAccountSchedule(['acct-flushed'], 1, { flush: true });
        closeDb();
        vi.useRealTimers();

        await initDb(createTestConfig(dbPath));
        expect(getCodexAccountSchedule()).toMatchObject([
            { accountKey: 'acct-flushed', seedOrder: 0, anchorWeekday: 1, laneRank: 0 },
        ]);
    });
});
