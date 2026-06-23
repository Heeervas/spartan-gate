/**
 * ClawRoute HTTP Server
 *
 * Hono-based HTTP proxy server with all routes.
 */

import { Context, Hono } from 'hono';
import { cors } from 'hono/cors';
import { readFileSync, statSync } from 'fs';
import { createHash } from 'crypto';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import {
    ChatCompletionRequest,
    ClassificationResult,
    ClawRouteConfig,
    CodexToolSchemaGroup,
    ExecutionResult,
    LogEntry,
    ModelEntry,
    ProviderType,
    RoutingDecision,
    RoutingSnapshot,
    TaskTier,
    isProviderType,
} from './types.js';
import { createAuthMiddleware } from './auth.js';
import { classifyRequest, explainClassification } from './classifier.js';
import { routeRequest } from './router.js';
import { executeRequest, executePassthrough } from './executor.js';
import { cloneModelCatalog, deleteModel, getEnabledModelsFromCatalog, getModelEntryFromCatalog, getModelEntryStrictFromCatalog, registerModel } from './models.js';
import {
    getCodexAnalysis,
    approveCodexColdMigrationDecision,
    dismissCodexColdMigrationDecision,
    getCodexPromptCacheUsage,
    getPendingCodexColdMigrationDecisions,
    getLiveRoutingSessions,
    getRecentTurnTraceCandidates,
    getTurnRequests,
    logRouting,
} from './logger.js';
import { getStatsResponse } from './stats.js';
import { getRedactedConfig, persistModelRegistryEntry, persistModelRemoval, persistTierSelection } from './config.js';
import { generateRequestId, nowIso, stripMetadataPreamble } from './utils.js';
import { responsesBodyToChatCompletions, chatCompletionToResponsesBody, chatCompletionStreamToResponsesSSE } from './responses-adapter.js';
import { discoverProviderModels, DiscoveredModelCandidate, getCandidateMissingFields } from './provider-discovery.js';
import { RuntimeStateManager } from './runtime-state.js';
import { getCodexUsage } from './codex-usage.js';
import {
    forceRotateCodexCacheLease,
    getCodexAutomaticUsageSlotIndexes,
    getCodexBalancerState,
    invalidateCodexCacheLeaseForSlot,
} from './codex-transport.js';
import {
    resetCodexBalancerToEnvironment,
    updateCodexBalancerSettings,
    updateCodexExpectedWeeklyReset,
    updateCodexBalancerSlot,
} from './codex-balancer.js';
import { executeImageEdit, executeImageGeneration } from './image-generation.js';
import {
    buildRequestTraceSnapshot,
    finalizeRequestTrace,
    resolveSessionIdentity,
    sanitizeLoggedPreview,
    SessionIdentity,
} from './request-trace.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

type CachedDashboard = {
    path: string;
    mtimeMs: number;
    html: string;
};

const dashboardCache = new Map<string, CachedDashboard>();

function tryReadCachedHtml(filename: string): string | null {
    const candidates = [
        join(__dirname, '..', 'web', filename),
        join(__dirname, '..', 'dist', 'web', filename),
    ];

    for (const path of candidates) {
        try {
            const stat = statSync(path);
            const cached = dashboardCache.get(filename);
            if (cached && cached.path === path && cached.mtimeMs === stat.mtimeMs) {
                return cached.html;
            }

            const html = readFileSync(path, 'utf-8');
            dashboardCache.set(filename, { path, mtimeMs: stat.mtimeMs, html });
            return html;
        } catch {
            // Try the next dashboard location.
        }
    }

    dashboardCache.delete(filename);
    return null;
}

function parseErrorBody(body: string): Record<string, unknown> {
    try {
        const parsed = JSON.parse(body) as Record<string, unknown>;
        const error = parsed.error;
        if (typeof error === 'object' && error !== null) {
            return error as Record<string, unknown>;
        }
        return parsed;
    } catch {
        return {
            message: body.trim() || 'Upstream request failed',
            type: 'server_error',
            code: 'upstream_error',
        };
    }
}

function errorBodyToChatSse(error: Record<string, unknown>): ReadableStream<Uint8Array> {
    const encoder = new TextEncoder();
    return new ReadableStream({
        start(controller) {
            controller.enqueue(encoder.encode(`data: ${JSON.stringify({ error })}\n\n`));
            controller.close();
        },
    });
}

/**
 * Create the Hono application.
 *
 * @param config - The ClawRoute configuration
 * @returns Configured Hono app
 */
type CreateAppOptions = {
    projectRoot?: string;
    runtimeState?: RuntimeStateManager;
};

type RequestBloatSnapshot = {
    messageChars: number;
    toolSchemaChars: number;
    toolSchemaRoughTokens: number;
    topToolSchemaGroups: CodexToolSchemaGroup[];
    bloatAlerts: string[];
};

function safeJsonLength(value: unknown): number {
    try {
        return JSON.stringify(value)?.length ?? 0;
    } catch {
        return 0;
    }
}

function estimateMessageChars(messages: ChatCompletionRequest['messages'] | undefined): number {
    return (messages ?? []).reduce((sum, message) => sum + safeJsonLength(message), 0);
}

function groupToolName(name: string): string {
    if (name.startsWith('mcp_measure_plan_')) return 'mcp:measure_plan';
    if (name.startsWith('mcp_analytics_mcp_')) return 'mcp:analytics_mcp';
    if (name.startsWith('mcp_gtm_')) return 'mcp:gtm';
    if (name.startsWith('mcp_')) {
        const serverName = name.slice(4).split('_')[0] || 'unknown';
        return `mcp:${serverName}`;
    }
    return name.split('_')[0] || 'builtin';
}

function estimateToolSchemaGroups(tools: ChatCompletionRequest['tools'] | undefined): {
    chars: number;
    groups: CodexToolSchemaGroup[];
} {
    const groups = new Map<string, CodexToolSchemaGroup>();
    let chars = 0;
    for (const tool of tools ?? []) {
        const toolChars = safeJsonLength(tool);
        chars += toolChars;
        const name = typeof tool.function?.name === 'string' ? tool.function.name : 'unknown';
        const key = groupToolName(name);
        const current = groups.get(key) ?? { key, tools: 0, chars: 0 };
        current.tools += 1;
        current.chars += toolChars;
        groups.set(key, current);
    }
    return {
        chars,
        groups: [...groups.values()]
            .sort((a, b) => b.chars - a.chars)
            .slice(0, 8),
    };
}

function estimateRequestBloat(body: ChatCompletionRequest): RequestBloatSnapshot {
    const messageChars = estimateMessageChars(body.messages);
    const toolSchemas = estimateToolSchemaGroups(body.tools);
    const toolSchemaRoughTokens = Math.ceil(toolSchemas.chars / 4);
    const bloatAlerts: string[] = [];
    if ((body.messages ?? []).length >= 80) bloatAlerts.push('many_messages');
    if ((body.tools ?? []).length >= 100) bloatAlerts.push('many_tools');
    if (messageChars >= 250_000) bloatAlerts.push('large_message_history');
    if (toolSchemas.chars >= 100_000) bloatAlerts.push('large_tool_schemas');
    return {
        messageChars,
        toolSchemaChars: toolSchemas.chars,
        toolSchemaRoughTokens,
        topToolSchemaGroups: toolSchemas.groups,
        bloatAlerts,
    };
}

function resolveCodexAnalysisPeriod(query: {
    period?: string;
    start?: string;
    end?: string;
}): { ok: true; periodKey: string; start: string; end: string } | { ok: false; message: string } {
    const now = new Date();
    const period = query.period ?? '7d';
    const fixedHours: Record<string, number> = {
        '24h': 24,
        '5d': 5 * 24,
        '7d': 7 * 24,
        '30d': 30 * 24,
    };
    if (period === 'custom') {
        if (!query.start || !query.end) {
            return { ok: false, message: 'custom period requires start and end ISO timestamps' };
        }
        const startMs = Date.parse(query.start);
        const endMs = Date.parse(query.end);
        if (!Number.isFinite(startMs) || !Number.isFinite(endMs) || startMs >= endMs) {
            return { ok: false, message: 'custom start and end must be valid ISO timestamps with start before end' };
        }
        return {
            ok: true,
            periodKey: 'custom',
            start: new Date(startMs).toISOString(),
            end: new Date(endMs).toISOString(),
        };
    }
    const hours = fixedHours[period];
    if (!hours) {
        return { ok: false, message: 'period must be one of 24h, 5d, 7d, 30d, or custom' };
    }
    return {
        ok: true,
        periodKey: period,
        start: new Date(now.getTime() - hours * 60 * 60 * 1000).toISOString(),
        end: now.toISOString(),
    };
}

export function createApp(config: ClawRouteConfig, options: CreateAppOptions = {}): Hono {
    const app = new Hono();
    const discoveredCandidates = new Map<string, DiscoveredModelCandidate>();

    function getRuntimeSnapshot(): RoutingSnapshot {
        if (!options.runtimeState) {
            return {
                providerProfile: config.providerProfile,
                baselineModel: config.baselineModel,
                models: config.models,
                contextOverrides: config.contextOverrides,
                modelCatalog: cloneModelCatalog(),
            };
        }

        return options.runtimeState.getSnapshot();
    }

    function getRuntimeConfigFromSnapshot(snapshot: RoutingSnapshot): ClawRouteConfig {
        return {
            ...config,
            providerProfile: snapshot.providerProfile,
            baselineModel: snapshot.baselineModel,
            models: snapshot.models,
            contextOverrides: snapshot.contextOverrides,
        };
    }

    function getRuntimeConfig(): ClawRouteConfig {
        return getRuntimeConfigFromSnapshot(getRuntimeSnapshot());
    }

    function logCompletedRequest(input: {
        requestId: string;
        requestApiKind: 'chat_completions' | 'responses';
        body: ChatCompletionRequest;
        routing: RoutingDecision;
        classification: ClassificationResult;
        result: ExecutionResult;
        session: SessionIdentity;
        promptCacheKey: string | null;
    }): void {
        const lastUserMsg = [...(input.body.messages ?? [])]
            .reverse()
            .find((m) => m.role === 'user');
        const rawText = typeof lastUserMsg?.content === 'string'
            ? lastUserMsg.content
            : Array.isArray(lastUserMsg?.content)
                ? (lastUserMsg.content as Array<{ type: string; text?: string }>)
                    .filter((p) => p.type === 'text')
                    .map((p) => p.text ?? '')
                    .join(' ')
                : null;

        // Strip untrusted metadata preamble blocks so prompt_preview shows the
        // actual user message, not agent metadata JSON.
        const cleanText = rawText ? stripMetadataPreamble(rawText) : '';
        const promptPreview = config.logging.logContent && cleanText
            ? sanitizeLoggedPreview(cleanText, 300)
            : null;

        const reasoningEffort = typeof input.body['reasoning_effort'] === 'string'
            ? input.body['reasoning_effort']
            : null;
        const bloat = estimateRequestBloat(input.body);
        const traceSnapshot = buildRequestTraceSnapshot(
            input.body,
            input.session,
            config.logging.logContent,
        );
        const requestTrace = finalizeRequestTrace(
            traceSnapshot,
            traceSnapshot.turnId ? getRecentTurnTraceCandidates(traceSnapshot.turnId) : [],
        );
        const contextInfo = JSON.stringify({
            msg_count: (input.body.messages ?? []).length,
            has_system: (input.body.messages ?? []).some((m) => m.role === 'system'),
            tool_count: (input.body.tools ?? []).length,
            last_role: (input.body.messages ?? []).at(-1)?.role ?? null,
            cache_key_present: Boolean(input.promptCacheKey),
            cache_key_hash: input.promptCacheKey
                ? createHash('sha256').update(input.promptCacheKey).digest('hex').slice(0, 12)
                : null,
            request_api_kind: input.requestApiKind,
            requested_reasoning_effort: reasoningEffort,
            message_chars: bloat.messageChars,
            tool_schema_chars: bloat.toolSchemaChars,
            tool_schema_rough_tokens: bloat.toolSchemaRoughTokens,
            top_tool_schema_groups: bloat.topToolSchemaGroups,
            bloat_alerts: bloat.bloatAlerts,
            tool_schema_fingerprint: traceSnapshot.toolSchemaFingerprint,
            request_trace: requestTrace,
        });

        const logEntry: LogEntry = {
            timestamp: nowIso(),
            original_model: input.routing.originalModel,
            routed_model: input.routing.routedModel,
            actual_model: input.result.actualModel,
            tier: input.routing.tier,
            classification_reason: input.classification.reason,
            confidence: input.classification.confidence,
            input_tokens: input.result.inputTokens,
            output_tokens: input.result.outputTokens,
            cached_input_tokens: input.result.cachedInputTokens ?? 0,
            original_cost_usd: input.result.originalCostUsd,
            actual_cost_usd: input.result.actualCostUsd,
            savings_usd: input.result.savingsUsd,
            escalated: input.result.escalated,
            escalation_chain: JSON.stringify(input.result.escalationChain),
            response_time_ms: input.result.responseTimeMs,
            had_tool_calls: input.result.hadToolCalls,
            is_dry_run: input.routing.isDryRun,
            is_override: input.routing.isOverride,
            session_id: input.session.id,
            error: input.result.streamError ?? null,
            prompt_preview: promptPreview,
            context_info: contextInfo,
            request_api_kind: input.requestApiKind,
            requested_reasoning_effort: reasoningEffort,
            selected_codex_slot_index: input.result.selectedCodexSlotIndex ?? null,
            selected_codex_account_key: input.result.selectedCodexAccountKey ?? null,
            request_id: input.requestId,
            turn_id: traceSnapshot.turnId,
        };
        logRouting(logEntry);

        if (input.result.actualModel.startsWith('codex/') && bloat.bloatAlerts.length > 0) {
            console.warn(JSON.stringify({
                event: 'codex_request_bloat_alert',
                request_id: input.requestId,
                actual_model: input.result.actualModel,
                alerts: bloat.bloatAlerts,
                input_tokens: input.result.inputTokens,
                output_tokens: input.result.outputTokens,
                cached_input_tokens: input.result.cachedInputTokens ?? 0,
                msg_count: (input.body.messages ?? []).length,
                tool_count: (input.body.tools ?? []).length,
                message_chars: bloat.messageChars,
                tool_schema_chars: bloat.toolSchemaChars,
                top_tool_schema_groups: bloat.topToolSchemaGroups,
            }));
        }

        if (config.logging.debugMode) {
            console.log(
                input.result.streamError
                    ? `[${input.requestId}] Stream error: ${input.result.streamError}`
                    : `[${input.requestId}] Complete: ${input.result.responseTimeMs}ms, saved $${input.result.savingsUsd.toFixed(4)}`
            );
        }
    }

    async function reloadRuntime(reason: string): Promise<void> {
        if (!options.runtimeState) {
            return;
        }
        await options.runtimeState.reloadNow(reason);
    }

    function getMissingFields(body: Partial<ModelEntry>): DiscoveredModelCandidate['missingFields'] {
        return getCandidateMissingFields(body);
    }

    function getInvalidFields(body: Partial<ModelEntry>): string[] {
        const invalidFields: string[] = [];
        if (body.maxContext !== undefined) {
            if (typeof body.maxContext !== 'number' || !Number.isFinite(body.maxContext) || body.maxContext <= 0) {
                invalidFields.push('maxContext');
            }
        }
        if (body.inputCostPer1M !== undefined) {
            if (typeof body.inputCostPer1M !== 'number' || !Number.isFinite(body.inputCostPer1M) || body.inputCostPer1M < 0) {
                invalidFields.push('inputCostPer1M');
            }
        }
        if (body.outputCostPer1M !== undefined) {
            if (typeof body.outputCostPer1M !== 'number' || !Number.isFinite(body.outputCostPer1M) || body.outputCostPer1M < 0) {
                invalidFields.push('outputCostPer1M');
            }
        }
        if (body.toolCapable !== undefined && typeof body.toolCapable !== 'boolean') {
            invalidFields.push('toolCapable');
        }
        if (body.multimodal !== undefined && typeof body.multimodal !== 'boolean') {
            invalidFields.push('multimodal');
        }
        if (body.enabled !== undefined && typeof body.enabled !== 'boolean') {
            invalidFields.push('enabled');
        }
        return invalidFields;
    }

    async function applyPersistentChange(
        write: () => () => void,
        reason: string
    ): Promise<void> {
        const rollback = write();
        try {
            await reloadRuntime(reason);
        } catch (error) {
            rollback();
            try {
                await reloadRuntime(`${reason} rollback`);
            } catch {
                // Best effort to restore the last known-good snapshot.
            }
            throw error;
        }
    }

    function isConfiguredTier(tier: string): tier is TaskTier {
        return Object.values(TaskTier).includes(tier as TaskTier);
    }

    function isDiscoveryOnly(modelId: string): boolean {
        const candidate = discoveredCandidates.get(modelId);
        return candidate?.discoveryOnly ?? false;
    }

    function isAllowedModel(modelId: string): boolean {
        const entry = getModelEntryFromCatalog(modelId, getRuntimeSnapshot().modelCatalog);
        if (!entry || !entry.enabled) return false;
        return !isDiscoveryOnly(modelId);
    }

    function hasModelReference(modelId: string): boolean {
        const runtimeConfig = getRuntimeConfig();
        if (runtimeConfig.baselineModel === modelId) {
            return true;
        }

        return Object.values(TaskTier).some((tier) => {
            const tierConfig = runtimeConfig.models[tier];
            return tierConfig.primary === modelId || tierConfig.fallback === modelId;
        });
    }

    // CORS for dashboard
    app.use('*', cors());

    // Auth middleware (model listing is exempt — read-only discovery on internal network)
    app.use('/v1/*', createAuthMiddleware(config, ['/v1/models']));
    app.use('/api/*', createAuthMiddleware(config));

    // Health check
    app.get('/health', (c) => {
        return c.json({
            status: 'ok',
            version: '1.1.0',
            buildRevision: process.env['CLAWROUTE_BUILD_REVISION'] ?? 'unknown',
            enabled: config.enabled,
            dryRun: config.dryRun,
            timestamp: nowIso(),
        });
    });

    // Stats API
    app.get('/stats', (c) => {
        const stats = getStatsResponse(config);
        return c.json(stats);
    });

    const serveMainDashboard = (c: Context) => {
        try {
            const filename = 'dashboard2.html';
            const html = tryReadCachedHtml(filename);
            if (html) {
                return c.html(html);
            }
            return c.html(`<html><body><h1>Dashboard v2 not found</h1><p>Expected file: web/${filename}</p></body></html>`);
        } catch (error) {
            return c.html('<html><body><h1>Error loading dashboard</h1></body></html>');
        }
    };

    // Dashboard 2 is the canonical dashboard. Keep /dashboard2 as a compatibility alias.
    app.get('/dashboard', serveMainDashboard);
    app.get('/dashboard2', serveMainDashboard);

    app.get('/dashboard-codex', (c) => {
        try {
            const filename = 'dashboard-codex.html';
            const html = tryReadCachedHtml(filename);
            if (html) {
                return c.html(html);
            }
            return c.html(`<html><body><h1>Codex dashboard not found</h1><p>Expected file: web/${filename}</p></body></html>`);
        } catch {
            return c.html('<html><body><h1>Error loading Codex dashboard</h1></body></html>');
        }
    });

    app.get('/dashboard-codex-analysis', (c) => {
        try {
            const filename = 'dashboard-codex-analysis.html';
            const html = tryReadCachedHtml(filename);
            if (html) {
                return c.html(html);
            }
            return c.html(`<html><body><h1>Codex analysis dashboard not found</h1><p>Expected file: web/${filename}</p></body></html>`);
        } catch {
            return c.html('<html><body><h1>Error loading Codex analysis dashboard</h1></body></html>');
        }
    });

    // Legacy Dashboard (v1.0), retained as an explicit archive.
    app.get('/dashboard-archive', (c) => {
        try {
            const filename = 'dashboard.html';
            const html = tryReadCachedHtml(filename);
            if (html) {
                return c.html(html);
            }
            return c.html(`<html><body><h1>Dashboard v1 not found</h1><p>Expected file: web/${filename}</p></body></html>`);
        } catch (error) {
            return c.html('<html><body><h1>Error loading dashboard</h1></body></html>');
        }
    });

    // Config API (redacted)
    app.get('/api/config', (c) => {
        const redacted = getRedactedConfig(config);
        return c.json(redacted);
    });

    app.get('/api/routing/live', (c) => {
        return c.json(getLiveRoutingSessions(10, 20, config.logging.retentionDays));
    });

    app.get('/api/routing/turns/:turnId', (c) => {
        const turnId = c.req.param('turnId');
        if (!/^[a-f0-9]{16}$/.test(turnId)) {
            return c.json({ error: { code: 'invalid_turn_id', message: 'turnId must be a 16-character hash' } }, 400);
        }
        return c.json(getTurnRequests(turnId, 200));
    });

    app.get('/api/codex/usage', async (c) => {
        try {
            const result = await getCodexUsage({
                slotIndexes: await getCodexAutomaticUsageSlotIndexes(),
            });
            return c.json({
                ...result.body,
                cacheUsage: getCodexPromptCacheUsage(24),
                cacheUsageRecent: getCodexPromptCacheUsage(1 / 6),
            }, result.status as 200 | 502);
        } catch (error) {
            return c.json({
                partial: false,
                accounts: [],
                slotErrors: [],
                resetCreditErrors: [],
                error: {
                    message: error instanceof Error ? error.message : 'Failed to load Codex usage',
                },
            }, 502);
        }
    });

    app.get('/api/codex/analysis', (c) => {
        const period = resolveCodexAnalysisPeriod({
            period: c.req.query('period'),
            start: c.req.query('start'),
            end: c.req.query('end'),
        });
        if (!period.ok) {
            return c.json({ error: { message: period.message, code: 'invalid_period' } }, 400);
        }
        return c.json(getCodexAnalysis(period));
    });

    app.post('/api/codex/usage/slots/:slotIndex', async (c) => {
        try {
            const slotIndex = Number(c.req.param('slotIndex'));
            const state = await getCodexBalancerState();
            if (!state.slots.some((slot) => slot.slotIndex === slotIndex)) {
                return c.json({ error: { message: `Unknown slot ${String(c.req.param('slotIndex'))}`, code: 'unknown_slot' } }, 404);
            }
            const result = await getCodexUsage({ slotIndexes: [slotIndex], force: true });
            const body = {
                ...result.body,
                cacheUsage: getCodexPromptCacheUsage(24),
                cacheUsageRecent: getCodexPromptCacheUsage(1 / 6),
            };
            const selectedSlotFailed = result.body.slotErrors.some((error) => error.slotIndex === slotIndex);
            if (selectedSlotFailed) {
                const slotError = result.body.slotErrors.find((error) => error.slotIndex === slotIndex);
                return c.json({
                    ...body,
                    error: {
                        message: slotError?.message
                            ?? result.body.error?.message
                            ?? `Failed to check Codex usage for slot ${slotIndex}`,
                    },
                }, 502);
            }
            return c.json(body, result.status as 200 | 502);
        } catch (error) {
            return c.json({
                partial: false,
                accounts: [],
                slotErrors: [],
                resetCreditErrors: [],
                error: {
                    message: error instanceof Error ? error.message : 'Failed to check Codex usage',
                },
            }, 502);
        }
    });

    app.get('/api/codex/balancer', async (c) => {
        try {
            return c.json(await getCodexBalancerState());
        } catch (error) {
            return c.json({
                error: {
                    message: error instanceof Error ? error.message : 'Failed to load Codex balancer state',
                    type: 'server_error',
                    code: 'balancer_state_failed',
                },
            }, 500);
        }
    });

    app.patch('/api/codex/balancer/settings', async (c) => {
        try {
            const body = await c.req.json() as {
                mode?: unknown;
                earlyActivationEnabled?: unknown;
                earlyActivationWeeklyPercent?: unknown;
                coldMigrationFiveHourThresholdPercent?: unknown;
            };
            if (body.mode !== undefined && !['off', 'shadow', 'on'].includes(String(body.mode))) {
                return c.json({ error: { message: 'mode must be off, shadow, or on', code: 'invalid_mode' } }, 400);
            }
            if (body.earlyActivationEnabled !== undefined && typeof body.earlyActivationEnabled !== 'boolean') {
                return c.json({ error: { message: 'earlyActivationEnabled must be boolean', code: 'invalid_early_activation' } }, 400);
            }
            if (body.earlyActivationWeeklyPercent !== undefined
                && (typeof body.earlyActivationWeeklyPercent !== 'number'
                    || !Number.isFinite(body.earlyActivationWeeklyPercent)
                    || body.earlyActivationWeeklyPercent < 1
                    || body.earlyActivationWeeklyPercent > 100)) {
                return c.json({ error: { message: 'earlyActivationWeeklyPercent must be between 1 and 100', code: 'invalid_threshold' } }, 400);
            }
            if (body.coldMigrationFiveHourThresholdPercent !== undefined
                && (typeof body.coldMigrationFiveHourThresholdPercent !== 'number'
                    || !Number.isFinite(body.coldMigrationFiveHourThresholdPercent)
                    || body.coldMigrationFiveHourThresholdPercent < 0
                    || body.coldMigrationFiveHourThresholdPercent > 100)) {
                return c.json({ error: { message: 'coldMigrationFiveHourThresholdPercent must be between 0 and 100', code: 'invalid_cold_migration_threshold' } }, 400);
            }
            updateCodexBalancerSettings({
                ...(body.mode !== undefined ? { mode: String(body.mode) as 'off' | 'shadow' | 'on' } : {}),
                ...(body.earlyActivationEnabled !== undefined ? { earlyActivationEnabled: body.earlyActivationEnabled } : {}),
                ...(body.earlyActivationWeeklyPercent !== undefined
                    ? { earlyActivationWeeklyPercent: body.earlyActivationWeeklyPercent }
                    : {}),
                ...(body.coldMigrationFiveHourThresholdPercent !== undefined
                    ? { coldMigrationFiveHourThresholdPercent: body.coldMigrationFiveHourThresholdPercent }
                    : {}),
            });
            return c.json(await getCodexBalancerState());
        } catch (error) {
            return c.json({
                error: {
                    message: error instanceof Error ? error.message : 'Failed to update balancer settings',
                    code: 'balancer_settings_failed',
                },
            }, 400);
        }
    });

    app.patch('/api/codex/balancer/slots/:slotIndex', async (c) => {
        try {
            const slotIndex = Number(c.req.param('slotIndex'));
            const state = await getCodexBalancerState();
            const slot = state.slots.find((candidate) => candidate.slotIndex === slotIndex);
            if (!slot) {
                return c.json({ error: { message: `Unknown slot ${String(c.req.param('slotIndex'))}`, code: 'unknown_slot' } }, 404);
            }
            const body = await c.req.json() as {
                enabled?: unknown;
                activateNow?: unknown;
                expectedWeeklyResetAt?: unknown;
            };
            if (body.enabled !== undefined && typeof body.enabled !== 'boolean') {
                return c.json({ error: { message: 'enabled must be boolean', code: 'invalid_enabled' } }, 400);
            }
            if (body.activateNow !== undefined && typeof body.activateNow !== 'boolean') {
                return c.json({ error: { message: 'activateNow must be boolean', code: 'invalid_activation' } }, 400);
            }
            if (body.expectedWeeklyResetAt !== undefined
                && body.expectedWeeklyResetAt !== null
                && (typeof body.expectedWeeklyResetAt !== 'string'
                    || !Number.isFinite(Date.parse(body.expectedWeeklyResetAt)))) {
                return c.json({ error: { message: 'expectedWeeklyResetAt must be an ISO date or null', code: 'invalid_expected_reset' } }, 400);
            }
            if (body.enabled !== undefined || body.activateNow !== undefined) {
                updateCodexBalancerSlot({
                    slotIndex,
                    ...(body.enabled !== undefined ? { enabled: body.enabled } : {}),
                    ...(body.activateNow !== undefined ? { activateNow: body.activateNow } : {}),
                    weeklyResetAt: slot.expectedWeeklyResetAt ?? slot.weeklyResetAt,
                });
            }
            if (body.expectedWeeklyResetAt !== undefined) {
                if (!slot.accountKey) {
                    return c.json({ error: { message: 'Cannot save a reset date without a known account', code: 'unknown_account' } }, 400);
                }
                updateCodexExpectedWeeklyReset({
                    slotIndex,
                    accountKey: slot.accountKey,
                    expectedWeeklyResetAt: body.expectedWeeklyResetAt as string | null,
                });
            }
            if (body.enabled === false) invalidateCodexCacheLeaseForSlot(slotIndex);
            return c.json(await getCodexBalancerState());
        } catch (error) {
            return c.json({
                error: {
                    message: error instanceof Error ? error.message : 'Failed to update slot',
                    code: 'balancer_slot_failed',
                },
            }, 400);
        }
    });

    app.post('/api/codex/balancer/reset', async (c) => {
        resetCodexBalancerToEnvironment();
        return c.json(await getCodexBalancerState());
    });

    app.post('/api/codex/balancer/rotate', async (c) => {
        forceRotateCodexCacheLease();
        return c.json(await getCodexBalancerState());
    });

    app.get('/api/codex/cold-migrations', (c) => {
        return c.json({ decisions: getPendingCodexColdMigrationDecisions() });
    });

    app.post('/api/codex/cold-migrations/:decisionId/approve', (c) => {
        const decision = approveCodexColdMigrationDecision(c.req.param('decisionId'));
        if (!decision) {
            return c.json({ error: { message: 'Unknown or expired cold migration decision', code: 'unknown_decision' } }, 404);
        }
        return c.json({ decision, decisions: getPendingCodexColdMigrationDecisions() });
    });

    app.post('/api/codex/cold-migrations/:decisionId/dismiss', (c) => {
        const decision = dismissCodexColdMigrationDecision(c.req.param('decisionId'));
        if (!decision) {
            return c.json({ error: { message: 'Unknown or expired cold migration decision', code: 'unknown_decision' } }, 404);
        }
        return c.json({ decision, decisions: getPendingCodexColdMigrationDecisions() });
    });

    app.post('/api/admin/models/discover', async (c) => {
        try {
            const body = await c.req.json() as { provider?: ProviderType };
            if (!body.provider) {
                return c.json({ error: { message: 'Provide provider', type: 'invalid_request_error', code: 'invalid_body' } }, 400);
            }
            if (!isProviderType(body.provider)) {
                return c.json({ error: { message: `Unknown provider: ${String(body.provider)}`, type: 'invalid_request_error', code: 'invalid_provider' } }, 400);
            }

            const candidates = await discoverProviderModels(body.provider, getRuntimeConfig());
            for (const candidate of candidates) {
                discoveredCandidates.set(candidate.id, candidate);
            }
            return c.json({ candidates });
        } catch (error) {
            return c.json({
                error: {
                    message: error instanceof Error ? error.message : 'Failed to discover models',
                    type: 'invalid_request_error',
                    code: 'discovery_failed',
                },
            }, 400);
        }
    });

    app.post('/api/admin/models', async (c) => {
        let body: Partial<ModelEntry>;
        try {
            body = await c.req.json() as Partial<ModelEntry>;
        } catch {
            return c.json({ error: { message: 'Invalid JSON body', type: 'invalid_request_error', code: 'invalid_json' } }, 400);
        }

        try {
            if (!body.id || !body.provider) {
                return c.json({
                    error: {
                        message: 'Provide id and provider',
                        type: 'invalid_request_error',
                        code: 'invalid_body',
                    },
                    missingFields: body.id ? ['provider'] : ['id', 'provider'],
                }, 400);
            }
            if (!isProviderType(body.provider)) {
                return c.json({
                    error: {
                        message: `Unknown provider: ${String(body.provider)}`,
                        type: 'invalid_request_error',
                        code: 'invalid_provider',
                    },
                }, 400);
            }

            const missingFields = getMissingFields(body);
            if (missingFields.length > 0) {
                discoveredCandidates.set(body.id, {
                    id: body.id,
                    provider: body.provider,
                    discoveryOnly: true,
                    missingFields,
                });
                return c.json({
                    error: {
                        message: 'Incomplete model metadata',
                        type: 'invalid_request_error',
                        code: 'incomplete_model',
                    },
                    missingFields,
                }, 400);
            }

            const invalidFields = getInvalidFields(body);
            if (invalidFields.length > 0) {
                return c.json({
                    error: {
                        message: 'Invalid model metadata',
                        type: 'invalid_request_error',
                        code: 'invalid_model',
                    },
                    invalidFields,
                }, 400);
            }

            const model: ModelEntry = {
                id: body.id,
                provider: body.provider,
                inputCostPer1M: body.inputCostPer1M ?? 0,
                outputCostPer1M: body.outputCostPer1M ?? 0,
                maxContext: body.maxContext ?? 0,
                toolCapable: body.toolCapable ?? false,
                multimodal: body.multimodal ?? false,
                enabled: body.enabled ?? false,
            };

            if (options.projectRoot) {
                await applyPersistentChange(
                    () => persistModelRegistryEntry(options.projectRoot!, model),
                    `admin add model ${model.id}`
                );
            } else {
                registerModel(model);
            }
            discoveredCandidates.delete(model.id);
            return c.json({ success: true, model });
        } catch (error) {
            const message = error instanceof Error ? error.message : 'Invalid JSON body';
            return c.json({ error: { message, type: 'invalid_request_error', code: 'reload_failed' } }, 400);
        }
    });

    app.post('/api/admin/tiers/:tier', async (c) => {
        let body: { primary?: string; fallback?: string };
        try {
            const tier = c.req.param('tier');
            if (!isConfiguredTier(tier)) {
                return c.json({ error: { message: `Unknown tier: ${tier}`, type: 'invalid_request_error', code: 'unknown_tier' } }, 404);
            }

            try {
                body = await c.req.json() as { primary?: string; fallback?: string };
            } catch {
                return c.json({ error: { message: 'Invalid JSON body', type: 'invalid_request_error', code: 'invalid_json' } }, 400);
            }
            if (!body.primary || !body.fallback) {
                return c.json({ error: { message: 'Provide primary and fallback', type: 'invalid_request_error', code: 'invalid_body' } }, 400);
            }

            if (!isAllowedModel(body.primary) || !isAllowedModel(body.fallback)) {
                return c.json({
                    error: {
                        message: 'Tier assignments must target complete, enabled, non-discovery-only models',
                        type: 'invalid_request_error',
                        code: 'invalid_model_target',
                    },
                }, 400);
            }

            const nextConfig = { primary: body.primary, fallback: body.fallback };
            if (options.projectRoot) {
                await applyPersistentChange(
                    () => persistTierSelection(options.projectRoot!, tier, nextConfig),
                    `admin update tier ${tier}`
                );
            } else {
                config.models[tier] = nextConfig;
            }
            return c.json({ success: true, tier, config: nextConfig });
        } catch (error) {
            const message = error instanceof Error ? error.message : 'Failed to update tier';
            return c.json({ error: { message, type: 'invalid_request_error', code: 'reload_failed' } }, 400);
        }
    });

    app.delete('/api/admin/models/:id{.+}', async (c) => {
        const modelId = c.req.param('id');
        if (hasModelReference(modelId)) {
            return c.json({
                error: {
                    message: `Model ${modelId} is still referenced by baselineModel or tier config`,
                    type: 'invalid_request_error',
                    code: 'model_referenced',
                },
            }, 409);
        }

        discoveredCandidates.delete(modelId);
        if (options.projectRoot) {
            try {
                await applyPersistentChange(
                    () => persistModelRemoval(options.projectRoot!, modelId),
                    `admin remove model ${modelId}`
                );
            } catch (error) {
                const message = error instanceof Error ? error.message : 'Failed to remove model';
                return c.json({ error: { message, type: 'invalid_request_error', code: 'reload_failed' } }, 400);
            }
        } else {
            deleteModel(modelId);
        }
        return c.json({ success: true, id: modelId });
    });

    // Enable/disable controls
    app.post('/api/enable', (c) => {
        config.enabled = true;
        console.log('✅ ClawRoute enabled');
        return c.json({ success: true, enabled: true });
    });

    app.post('/api/disable', (c) => {
        config.enabled = false;
        console.log('⏸️  ClawRoute disabled (passthrough mode)');
        return c.json({ success: true, enabled: false });
    });

    // Dry-run controls
    app.post('/api/dry-run/enable', (c) => {
        config.dryRun = true;
        console.log('🔬 Dry-run mode enabled');
        return c.json({ success: true, dryRun: true });
    });

    app.post('/api/dry-run/disable', (c) => {
        config.dryRun = false;
        console.log('🚀 Dry-run mode disabled (live mode)');
        return c.json({ success: true, dryRun: false });
    });

    // Global override
    app.post('/api/override/global', async (c) => {
        try {
            const body = await c.req.json() as { model?: string; enabled?: boolean };

            if (body.enabled === false) {
                config.overrides.globalForceModel = null;
                console.log('🔄 Global override removed');
                return c.json({ success: true, globalForceModel: null });
            }

            if (body.model) {
                config.overrides.globalForceModel = body.model;
                console.log(`🎯 Global override set: ${body.model}`);
                return c.json({ success: true, globalForceModel: body.model });
            }

            return c.json({ error: 'Provide model or enabled: false' }, 400);
        } catch {
            return c.json({ error: 'Invalid JSON body' }, 400);
        }
    });

    // Session override
    app.post('/api/override/session', async (c) => {
        try {
            const body = await c.req.json() as {
                sessionId?: string;
                model?: string;
                turns?: number;
            };

            if (!body.sessionId || !body.model) {
                return c.json({ error: 'Provide sessionId and model' }, 400);
            }

            config.overrides.sessions[body.sessionId] = {
                model: body.model,
                remainingTurns: body.turns ?? null,
                createdAt: nowIso(),
            };

            console.log(`📌 Session override set: ${body.sessionId} → ${body.model}`);
            return c.json({ success: true, sessionId: body.sessionId, model: body.model });
        } catch {
            return c.json({ error: 'Invalid JSON body' }, 400);
        }
    });

    app.delete('/api/override/session', async (c) => {
        try {
            const body = await c.req.json() as { sessionId?: string };

            if (!body.sessionId) {
                return c.json({ error: 'Provide sessionId' }, 400);
            }

            delete config.overrides.sessions[body.sessionId];
            console.log(`🗑️  Session override removed: ${body.sessionId}`);
            return c.json({ success: true, sessionId: body.sessionId });
        } catch {
            return c.json({ error: 'Invalid JSON body' }, 400);
        }
    });

    // Main proxy endpoint - OpenAI compatible
    app.post('/v1/chat/completions', async (c) => {
        const requestId = generateRequestId();

        try {
            // Parse request body
            const body = await c.req.json() as ChatCompletionRequest;
            const runtimeSnapshot = getRuntimeSnapshot();
            const runtimeConfig = getRuntimeConfigFromSnapshot(runtimeSnapshot);
            const explicitPromptCacheKey = typeof body['prompt_cache_key'] === 'string'
                ? body['prompt_cache_key']
                : null;
            const promptCacheKey = explicitPromptCacheKey
                ?? (typeof body['user'] === 'string' ? body['user'] : null);
            const session = resolveSessionIdentity(body.messages, explicitPromptCacheKey);
            const sessionId = session.id;

            if (config.logging.debugMode) {
                console.log(`[${requestId}] Incoming request for model: ${body.model}`);
            }

            // If ClawRoute is disabled, passthrough
            if (!runtimeConfig.enabled) {
                if (config.logging.debugMode) {
                    console.log(`[${requestId}] Passthrough (disabled)`);
                }
                const response = await executePassthrough(body, runtimeConfig, runtimeSnapshot.modelCatalog);
                return response;
            }

            // Classify the request
            const classification = classifyRequest(body, runtimeConfig);

            if (config.logging.debugMode) {
                console.log(`[${requestId}] Classification: ${explainClassification(classification)}`);
            }

            // Route to model
            const routing = routeRequest(body, classification, runtimeConfig, runtimeSnapshot.modelCatalog);

            if (config.logging.debugMode) {
                console.log(
                    `[${requestId}] Routing: ${routing.originalModel} → ${routing.routedModel} (${routing.reason})`
                );
            }

            // Execute the request
            const result = await executeRequest(body, routing, classification, runtimeConfig, runtimeSnapshot.modelCatalog, {
                sessionId,
                promptCacheKey,
            });

            const buildAndLog = () => {
                logCompletedRequest({
                    requestId,
                    requestApiKind: 'chat_completions',
                    body,
                    routing,
                    classification,
                    result,
                    session,
                    promptCacheKey,
                });
            };

            // P1: For streaming, fire log callback after stream ends (executor back-fills tokens).
            //     For non-streaming, log asynchronously via setImmediate (tokens already correct).
            if (body.stream) {
                result.logWhenDone = buildAndLog;
            } else {
                setImmediate(buildAndLog);
            }

            return result.response;
        } catch (error) {
            // Any error in ClawRoute logic → fall back to passthrough
            console.error(`[${requestId}] Error in ClawRoute, falling back to passthrough:`, error);

            try {
                const body = await c.req.json() as ChatCompletionRequest;
                const response = await executePassthrough(body, config, getRuntimeSnapshot().modelCatalog);
                return response;
            } catch {
                return c.json(
                    {
                        error: {
                            message: 'Failed to process request',
                            type: 'server_error',
                            code: 'internal_error',
                        },
                    },
                    500
                );
            }
        }
    });

    // OpenAI Responses API endpoint
    app.post('/v1/responses', async (c) => {
        const requestId = generateRequestId();
        let wantsStream = false;

        try {
            const body = await c.req.json();
            wantsStream = body.stream === true;
            const runtimeSnapshot = getRuntimeSnapshot();
            const runtimeConfig = getRuntimeConfigFromSnapshot(runtimeSnapshot);

            if (!body.model) {
                return c.json(
                    { error: { message: 'model is required', type: 'invalid_request_error' } },
                    400
                );
            }
            if (!body.input) {
                return c.json(
                    { error: { message: 'input is required', type: 'invalid_request_error' } },
                    400
                );
            }
            // Accept input as string (shorthand) or array of messages
            if (typeof body.input === 'string') {
                body.input = [{ role: 'user', content: [{ type: 'input_text', text: body.input }] }];
            } else if (!Array.isArray(body.input)) {
                return c.json(
                    { error: { message: 'input must be a string or array', type: 'invalid_request_error' } },
                    400
                );
            }

            // Translate Responses API → Chat Completions
            const ccRequest = responsesBodyToChatCompletions(body);
            const promptCacheKey = typeof body.prompt_cache_key === 'string' ? body.prompt_cache_key : null;
            const session = resolveSessionIdentity(ccRequest.messages, promptCacheKey);
            const sessionId = session.id;

            ccRequest.stream = wantsStream;

            if (config.logging.debugMode) {
                console.log(`[${requestId}] /v1/responses → CC for model: ${ccRequest.model} (stream=${wantsStream})`);
            }

            // If ClawRoute is disabled, passthrough
            if (!runtimeConfig.enabled) {
                const response = await executePassthrough(ccRequest, runtimeConfig, runtimeSnapshot.modelCatalog);
                if (wantsStream) {
                    if (response.ok && response.body) {
                        return chatCompletionStreamToResponsesSSE(response.body, body);
                    }
                    const errorText = await response.text();
                    return chatCompletionStreamToResponsesSSE(
                        errorBodyToChatSse(parseErrorBody(errorText)),
                        body,
                    );
                }
                const ccJson = await response.json() as Record<string, unknown>;
                const responsesBody = chatCompletionToResponsesBody(ccJson);
                return c.json(responsesBody);
            }

            // Classify → Route → Execute
            const classification = classifyRequest(ccRequest, runtimeConfig);
            const routing = routeRequest(ccRequest, classification, runtimeConfig, runtimeSnapshot.modelCatalog);
            const result = await executeRequest(ccRequest, routing, classification, runtimeConfig, runtimeSnapshot.modelCatalog, {
                sessionId,
                promptCacheKey,
            });
            const buildAndLog = () => {
                logCompletedRequest({
                    requestId,
                    requestApiKind: 'responses',
                    body: ccRequest,
                    routing,
                    classification,
                    result,
                    session,
                    promptCacheKey,
                });
            };
            if (wantsStream) {
                result.logWhenDone = buildAndLog;
            } else {
                setImmediate(buildAndLog);
            }
            if (wantsStream) {
                if (result.response.ok && result.response.body) {
                    return chatCompletionStreamToResponsesSSE(result.response.body, body);
                }
                const errorText = await result.response.text();
                return chatCompletionStreamToResponsesSSE(
                    errorBodyToChatSse(parseErrorBody(errorText)),
                    body,
                );
            }

            // Translate CC response back to Responses API format
            const ccJson = await result.response.json() as Record<string, unknown>;
            const responsesBody = chatCompletionToResponsesBody(ccJson);
            return c.json(responsesBody);
        } catch (error) {
            console.error(`[${requestId}] Error in /v1/responses:`, error);
            const errorBody = {
                id: `resp_err_${requestId}`,
                object: 'response',
                status: 'failed',
                output: [],
                error: {
                    message: 'Failed to process request',
                    type: 'server_error',
                    code: 'internal_error',
                },
            };
            // When client wants SSE, return SSE-formatted error so the SDK parser
            // doesn't choke on raw JSON where it expects event-stream events.
            if (wantsStream) {
                const encoder = new TextEncoder();
                const stream = new ReadableStream({
                    start(controller) {
                        controller.enqueue(encoder.encode(
                            `event: error\ndata: ${JSON.stringify(errorBody)}\n\n`
                        ));
                        controller.close();
                    },
                });
                return new Response(stream, {
                    status: 200,
                    headers: {
                        'Content-Type': 'text/event-stream',
                        'Cache-Control': 'no-cache',
                    },
                });
            }
            return c.json(errorBody, 500);
        }
    });

    app.post('/v1/images/generations', async (c) => {
        try {
            const body = await c.req.json();
            const runtimeSnapshot = getRuntimeSnapshot();
            const runtimeConfig = getRuntimeConfigFromSnapshot(runtimeSnapshot);
            return await executeImageGeneration(body, runtimeConfig, runtimeSnapshot.modelCatalog);
        } catch {
            return c.json(
                {
                    error: {
                        message: 'Invalid JSON body',
                        type: 'invalid_request_error',
                        code: 'invalid_json',
                    },
                },
                400
            );
        }
    });

    app.post('/v1/images/edits', async (c) => {
        try {
            const body = await c.req.parseBody({ all: true });
            const runtimeSnapshot = getRuntimeSnapshot();
            const runtimeConfig = getRuntimeConfigFromSnapshot(runtimeSnapshot);
            return await executeImageEdit(body, runtimeConfig, runtimeSnapshot.modelCatalog);
        } catch {
            return c.json(
                {
                    error: {
                        message: 'Invalid multipart body',
                        type: 'invalid_request_error',
                        code: 'invalid_multipart',
                    },
                },
                400
            );
        }
    });

    // Anthropic-compatible endpoint placeholder
    app.post('/v1/messages', async (c) => {
        // For now, return a helpful error
        // Full Anthropic format support coming in v1.1
        return c.json(
            {
                error: {
                    message:
                        'Anthropic native format not yet supported in v1.0. Use OpenAI-compatible format or OpenRouter.',
                    type: 'invalid_request_error',
                    code: 'unsupported_format',
                },
            },
            400
        );
    });

    // Legacy license endpoints removed

    // OpenAI-compatible: List models
    app.get('/v1/models', (c) => {
        const runtimeSnapshot = getRuntimeSnapshot();
        const models = getEnabledModelsFromCatalog(runtimeSnapshot.modelCatalog);

        // Virtual model: clawroute/auto — agents can select this to let
        // ClawRoute classify and route to the best model automatically.
        const autoModel = {
            id: 'clawroute/auto',
            object: 'model' as const,
            created: 1700000000,
            owned_by: 'clawroute',
            max_context: 1000000,
            context_length: 1000000,
            max_model_len: 1000000,
            tool_capable: true,
            multimodal: true,
            description: 'Auto-routes to the best model based on request complexity',
        };

        return c.json({
            object: 'list',
            data: [
                autoModel,
                ...models.map(m => ({
                    id: m.id,
                    object: 'model',
                    created: 1700000000,
                    owned_by: m.provider,
                    // Extension fields for ClawRoute-aware clients
                    max_context: m.maxContext,
                    context_length: m.maxContext,
                    max_model_len: m.maxContext,
                    tool_capable: m.toolCapable,
                    multimodal: m.multimodal,
                })),
            ],
        });
    });

    // OpenAI-compatible: Retrieve model
    app.get('/v1/models/:id{.+}', (c) => {
        const runtimeSnapshot = getRuntimeSnapshot();
        const modelId = c.req.param('id');

        // Virtual model: clawroute/auto
        if (modelId === 'clawroute/auto') {
            return c.json({
                id: 'clawroute/auto',
                object: 'model',
                created: 1700000000,
                owned_by: 'clawroute',
                max_context: 1000000,
                context_length: 1000000,
                max_model_len: 1000000,
                tool_capable: true,
                multimodal: true,
                description: 'Auto-routes to the best model based on request complexity',
            });
        }

        const entry = getModelEntryStrictFromCatalog(modelId, runtimeSnapshot.modelCatalog);
        if (!entry || !entry.enabled) {
            return c.json({
                error: {
                    message: `The model '${modelId}' does not exist`,
                    type: 'invalid_request_error',
                    code: 'model_not_found',
                },
            }, 404);
        }
        return c.json({
            id: entry.id,
            object: 'model',
            created: 1700000000,
            owned_by: entry.provider,
            max_context: entry.maxContext,
            context_length: entry.maxContext,
            max_model_len: entry.maxContext,
            tool_capable: entry.toolCapable,
            multimodal: entry.multimodal,
        });
    });

    // ClawRoute-specific: Full model info with costs
    app.get('/api/models', (c) => {
        const runtimeSnapshot = getRuntimeSnapshot();
        const models = getEnabledModelsFromCatalog(runtimeSnapshot.modelCatalog);
        return c.json({
            models: models.map(m => ({
                id: m.id,
                provider: m.provider,
                maxContext: m.maxContext,
                inputCostPer1M: m.inputCostPer1M,
                outputCostPer1M: m.outputCostPer1M,
                toolCapable: m.toolCapable,
                multimodal: m.multimodal,
                enabled: m.enabled,
            })),
        });
    });

    // Legacy completions API (not supported)
    app.post('/v1/completions', (c) => {
        return c.json({
            error: {
                message: 'Legacy completions API not supported. Use /v1/chat/completions instead.',
                type: 'invalid_request_error',
                code: 'unsupported_endpoint',
            },
        }, 400);
    });

    // Embeddings API (not supported)
    app.post('/v1/embeddings', (c) => {
        return c.json({
            error: {
                message: 'Embeddings API not supported by ClawRoute.',
                type: 'invalid_request_error',
                code: 'unsupported_endpoint',
            },
        }, 400);
    });

    // Catch-all for unknown routes
    app.all('*', (c) => {
        return c.json(
            {
                error: {
                    message: `Unknown endpoint: ${c.req.method} ${c.req.path}`,
                    type: 'invalid_request_error',
                    code: 'unknown_endpoint',
                },
            },
            404
        );
    });

    return app;
}
