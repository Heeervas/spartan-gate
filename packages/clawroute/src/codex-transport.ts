/**
 * Codex OAuth Transport for ClawRoute
 *
 * Translates OpenAI Chat Completions API requests into the ChatGPT
 * Codex Responses API format used by the ChatGPT subscription endpoint.
 *
 * Protocol details:
 * - Upstream URL: https://chatgpt.com/backend-api/codex/responses
 * - Auth: Bearer <access_token> + chatgpt-account-id header
 * - Request: OpenAI Responses API format (input[] instead of messages[])
 * - Response: SSE events with response.output_text.delta etc.
 *
 * Reference: https://github.com/EvanZhouDev/openai-oauth
 */

import { createHash } from 'node:crypto';
import { readFileSync, writeFileSync, existsSync, mkdirSync } from 'fs';
import { join, dirname } from 'path';
import { homedir } from 'os';
import { ProxyAgent } from 'undici';
import { FetchInitWithDispatcher, fetchWithProxyAgent } from './http-proxy.js';
import {
    CODEX_CACHE_BREAKER_POLICY,
    getCodexCacheBreakerBlock,
    hashPromptCacheKey,
    resetCodexCacheBreakerState,
} from './codex-cache-breaker.js';
import { buildToolSchemaFingerprint } from './request-trace.js';
import {
    CodexAccountScheduleRow,
    CodexAuthUnavailableReason,
    CodexBalanceLoaderMode,
    CodexBalancerLease,
    CodexBalancerState,
    CodexScheduleStartDay,
    CodexSessionAccountAffinity,
    ImageEditRequest,
    ImageGenerationRequest,
    RequestExecutionContext,
} from './types.js';
import {
    buildCodexBalancerState,
    getDisabledCodexSlotIndexes,
    getEffectiveCodexBalancerSettings,
    resolveCodexActivatedSlots,
} from './codex-balancer.js';
import {
    consumeCodexColdMigrationDecision,
    getLiveQuotaCalibration,
    getPendingCodexColdMigrationDecisions,
    hasApprovedCodexColdMigrationDecision,
    setLastCodexBalancerDecision,
    upsertCodexColdMigrationDecision,
} from './logger.js';

// ── Types ──────────────────────────────────────────────────────────

export interface CodexAuth {
    accessToken: string;
    accountId: string;
    refreshToken?: string;
    idToken?: string;
    sourcePath?: string;
    sourceFingerprint?: string;
}

export interface CodexAuthSlotSnapshot {
    slotIndex: number;
    path: string | null;
    rateLimitedUntil: number;
    authAvailable: boolean;
    authUnavailableReason: CodexAuthUnavailableReason | null;
    authRetryAt: string | null;
}

interface ChatMessage {
    role: string;
    content: unknown;
    tool_calls?: Array<{
        id: string;
        type: string;
        function: { name: string; arguments: string };
    }>;
    tool_call_id?: string;
}

interface CodexAuthSlot {
    path: string;
    auth: CodexAuth | null;
    authFileFingerprint: string | null;
    lastLoadAttempt: number;
    rateLimitedUntil: number; // epoch ms — skip this slot until then
    authUnavailableReason: CodexAuthUnavailableReason | null;
    authRetryAt: number | null;
}

interface CodexErrorContext {
    slot?: number;
    path?: string;
}

export function buildCodexUsageDebugPayload(
    eventType: string,
    usage: Record<string, unknown> | undefined,
): Record<string, unknown> {
    const inputDetails = usage?.['input_tokens_details'] as Record<string, unknown> | undefined;
    const outputDetails = usage?.['output_tokens_details'] as Record<string, unknown> | undefined;
    const payload: Record<string, unknown> = { event: eventType };
    const numericFields: Array<[string, unknown]> = [
        ['input_tokens', usage?.['input_tokens']],
        ['output_tokens', usage?.['output_tokens']],
        ['total_tokens', usage?.['total_tokens']],
        ['cached_tokens', inputDetails?.['cached_tokens']],
        ['reasoning_tokens', outputDetails?.['reasoning_tokens']],
    ];
    for (const [key, value] of numericFields) {
        if (typeof value === 'number' && Number.isFinite(value)) payload[key] = value;
    }
    return payload;
}

function maybeLogCodexUsageDebug(eventType: string, usage: Record<string, unknown> | undefined): void {
    if (process.env['CLAWROUTE_DEBUG_CODEX_USAGE'] !== '1') return;
    console.debug('[clawroute:codex-usage]', JSON.stringify(buildCodexUsageDebugPayload(eventType, usage)));
}

type CodexSelectionLease = {
    accountKey: string | null;
    slotIndex: number;
    selectedAt: number;
};
type BalanceLoaderSelection = {
    selectedSlotIndex: number | null;
    selectedAccountKey: string | null;
    affinityApplied: boolean;
    fallbackReason: string | null;
    decisionReason?: string;
    currentWeekdayUtc?: number;
    activeWeekday?: number | null;
    activeLaneTelemetryFresh?: boolean;
    weeklyUsedPercent?: number | null;
    weeklyResetAt?: string | null;
    fiveHourUsedPercent?: number | null;
    fiveHourResetAt?: string | null;
    requiredBurnRate?: number | null;
    preferredAccountKey?: string | null;
    preferredSlotIndex?: number | null;
    preferredFiveHourUsedPercent?: number | null;
    preferredFiveHourResetAt?: string | null;
    eligibleAccountKeys?: Set<string>;
    eligibleSlotIndexes?: Set<number>;
};

// ── Rotation State ─────────────────────────────────────────────────
let authSlots: CodexAuthSlot[] = [];
let currentSlotIndex = 0;
let lastRotationTime = 0;
let lastQueryEndTime = 0;
let activeRequests = 0;
const sessionAffinities = new Map<string, CodexSessionAccountAffinity>();
const pendingLeasesByAccountKey = new Map<string, number>();
const pendingLeasesBySlotIndex = new Map<number, number>();
const slotLastSelectedAtByIndex = new Map<number, number>();
type ActiveCodexCacheLease = {
    id: string;
    affinityKey: string;
    accountKey: string;
    slotIndex: number;
    startedAt: number;
    lastUsedAt: number;
    nominalExpiresAt: number;
    maxExpiresAt: number;
    selectionReason: string | null;
};
const activeCacheLeases = new Map<string, ActiveCodexCacheLease>();
const authRefreshPromises = new Map<string, Promise<Awaited<ReturnType<typeof refreshTokens>>>>();
const authRefreshRetryAt = new Map<string, number>();
type AuthFileSnapshot = {
    data: Record<string, unknown>;
    fingerprint: string;
};

// Configurable via env vars (read once on first use)
let rotationIntervalMs = -1;  // -1 = not yet loaded
let rotationIdleMs = -1;

function getRotationIntervalMs(): number {
    if (rotationIntervalMs < 0) {
        const hours = parseFloat(process.env['CODEX_ROTATION_INTERVAL_HOURS'] ?? '2');
        rotationIntervalMs = (isNaN(hours) ? 2 : hours) * 3_600_000;
    }
    return rotationIntervalMs;
}

function getRotationIdleMs(): number {
    if (rotationIdleMs < 0) {
        const minutes = parseFloat(process.env['CODEX_ROTATION_IDLE_MINUTES'] ?? '30');
        rotationIdleMs = (isNaN(minutes) ? 30 : minutes) * 60_000;
    }
    return rotationIdleMs;
}

function envMinutes(name: string, fallback: number): number {
    const value = Number(process.env[name] ?? fallback);
    return Number.isFinite(value) && value > 0 ? value * 60_000 : fallback * 60_000;
}

function envMilliseconds(name: string, fallback: number, min: number, max: number): number {
    const value = Number(process.env[name] ?? fallback);
    if (!Number.isFinite(value)) return fallback;
    return Math.min(max, Math.max(min, Math.round(value)));
}

function envHours(name: string, fallback: number): number {
    const value = Number(process.env[name] ?? fallback);
    return Number.isFinite(value) && value > 0 ? value * 3_600_000 : fallback * 3_600_000;
}

function safeJsonLength(value: unknown): number {
    try {
        return JSON.stringify(value)?.length ?? 0;
    } catch {
        return 0;
    }
}

function estimateCodexRequestInputTokens(request: Record<string, unknown>): number {
    const messages = Array.isArray(request['messages']) ? request['messages'] : [];
    const tools = Array.isArray(request['tools']) ? request['tools'] : [];
    const messageChars = messages.reduce((sum, message) => sum + safeJsonLength(message), 0);
    const toolChars = tools.reduce((sum, tool) => sum + safeJsonLength(tool), 0);
    return Math.ceil((messageChars + toolChars) / 4);
}

function getColdMigrationDecisionTtlMs(): number {
    return envHours('CODEX_COLD_MIGRATION_DECISION_TTL_HOURS', 6);
}

function getCacheLeaseDurationMs(): number {
    return envMinutes('CODEX_CACHE_LEASE_MINUTES', 60);
}

function getCacheLeaseIdleGraceMs(): number {
    return envMinutes('CODEX_CACHE_LEASE_IDLE_GRACE_MINUTES', 10);
}

function getCacheLeaseMaxMs(): number {
    return Math.max(getCacheLeaseDurationMs(), envMinutes('CODEX_CACHE_LEASE_MAX_MINUTES', 120));
}

function getCacheLeaseMaxEntries(): number {
    const value = Number(process.env['CODEX_CACHE_LEASE_MAX_ENTRIES'] ?? 256);
    return Number.isFinite(value) && value > 0 ? Math.floor(value) : 256;
}

function getCacheLeaseMode(): 'keyed' | 'global' | 'off' {
    const mode = (process.env['CODEX_CACHE_LEASE_MODE'] ?? 'keyed').trim().toLowerCase();
    if (mode === 'global' || mode === 'off') return mode;
    return 'keyed';
}

function getCodexRequestTimeoutMs(): number {
    return envMilliseconds('CODEX_REQUEST_TIMEOUT_MS', 180_000, 10_000, 900_000);
}

function isCacheLeaseUsable(lease: ActiveCodexCacheLease, now: number): boolean {
    if (now >= lease.maxExpiresAt) return false;
    const inGrace = now >= lease.nominalExpiresAt;
    return !(inGrace && now - lease.lastUsedAt >= getCacheLeaseIdleGraceMs());
}

function serializeCacheLease(lease: ActiveCodexCacheLease, now: number): CodexBalancerLease {
    const inGrace = now >= lease.nominalExpiresAt;
    return {
        id: lease.id,
        affinityKeyHash: hashPromptCacheKey(lease.affinityKey),
        accountKey: lease.accountKey,
        slotIndex: lease.slotIndex,
        startedAt: new Date(lease.startedAt).toISOString(),
        lastUsedAt: new Date(lease.lastUsedAt).toISOString(),
        nominalExpiresAt: new Date(lease.nominalExpiresAt).toISOString(),
        maxExpiresAt: new Date(lease.maxExpiresAt).toISOString(),
        status: inGrace ? 'grace' : 'active',
        selectionReason: lease.selectionReason,
    };
}

function serializeActiveCacheLease(now: number): CodexBalancerLease | null {
    const leases = serializeActiveCacheLeases(now);
    return leases[0] ?? null;
}

function serializeActiveCacheLeases(now: number): CodexBalancerLease[] {
    clearExpiredCacheLeases(now);
    return Array.from(activeCacheLeases.values())
        .sort((left, right) => right.lastUsedAt - left.lastUsedAt)
        .map((lease) => serializeCacheLease(lease, now));
}

function clearExpiredCacheLeases(now: number): void {
    for (const [key, lease] of activeCacheLeases.entries()) {
        if (!isCacheLeaseUsable(lease, now)) activeCacheLeases.delete(key);
    }
}

function getLeaseMapKey(affinityKey: string): string {
    return getCacheLeaseMode() === 'global' ? '__global__' : affinityKey;
}

function evictOverflowCacheLeases(): void {
    const maxEntries = getCacheLeaseMaxEntries();
    while (activeCacheLeases.size > maxEntries) {
        let oldestKey: string | null = null;
        let oldestLastUsedAt = Number.POSITIVE_INFINITY;
        for (const [key, lease] of activeCacheLeases.entries()) {
            if (lease.lastUsedAt < oldestLastUsedAt) {
                oldestKey = key;
                oldestLastUsedAt = lease.lastUsedAt;
            }
        }
        if (!oldestKey) return;
        activeCacheLeases.delete(oldestKey);
    }
}

function startCacheLease(selection: BalanceLoaderSelection, now: number, affinityKey: string | null): void {
    if (getCacheLeaseMode() === 'off') return;
    if (!affinityKey) return;
    if (selection.selectedSlotIndex === null || !selection.selectedAccountKey) return;
    activeCacheLeases.set(getLeaseMapKey(affinityKey), {
        id: crypto.randomUUID(),
        affinityKey,
        accountKey: selection.selectedAccountKey,
        slotIndex: selection.selectedSlotIndex,
        startedAt: now,
        lastUsedAt: now,
        nominalExpiresAt: now + getCacheLeaseDurationMs(),
        maxExpiresAt: now + getCacheLeaseMaxMs(),
        selectionReason: selection.decisionReason ?? null,
    });
    evictOverflowCacheLeases();
}

function applyCacheLease(selection: BalanceLoaderSelection, now: number, affinityKey: string | null): BalanceLoaderSelection {
    if (getCacheLeaseMode() === 'off') return selection;
    if (!affinityKey) return selection;
    clearExpiredCacheLeases(now);
    const lease = activeCacheLeases.get(getLeaseMapKey(affinityKey));
    if (lease
        && lease.affinityKey === affinityKey
        && selection.eligibleAccountKeys?.has(lease.accountKey)
        && selection.eligibleSlotIndexes?.has(lease.slotIndex)) {
        return {
            ...selection,
            selectedSlotIndex: lease.slotIndex,
            selectedAccountKey: lease.accountKey,
            affinityApplied: true,
            fallbackReason: null,
            decisionReason: 'cache_lease_reuse',
        };
    }
    if (lease && getCacheLeaseMode() === 'global') activeCacheLeases.delete(getLeaseMapKey(affinityKey));
    if (selection.fallbackReason === null) startCacheLease(selection, now, affinityKey);
    return selection;
}

export function forceRotateCodexCacheLease(): void {
    activeCacheLeases.clear();
}

export function invalidateCodexCacheLeaseForSlot(slotIndex: number): void {
    for (const [key, lease] of activeCacheLeases.entries()) {
        if (lease.slotIndex === slotIndex) activeCacheLeases.delete(key);
    }
}

function getBalanceLoaderMode(): CodexBalanceLoaderMode {
    return getEffectiveCodexBalancerSettings().settings.mode;
}

const CODEX_SCHEDULE_START_DAY_VALUES: Record<CodexScheduleStartDay, number> = {
    sun: 0,
    mon: 1,
    tue: 2,
    wed: 3,
    thu: 4,
    fri: 5,
    sat: 6,
};

function getCodexScheduleStartWeekday(): number {
    const configured = (process.env['CODEX_SCHEDULE_START_DAY'] ?? 'mon').trim().toLowerCase();
    return CODEX_SCHEDULE_START_DAY_VALUES[configured as CodexScheduleStartDay] ?? CODEX_SCHEDULE_START_DAY_VALUES.mon;
}

function getUtcWeekday(now: number): number {
    return new Date(now).getUTCDay();
}

// ── Constants ──────────────────────────────────────────────────────

const CODEX_BASE_URL = 'https://chatgpt.com/backend-api/codex';
const OAUTH_CLIENT_ID = 'app_EMoamEEZ73f0CkXaXp7hrann';
const OAUTH_TOKEN_URL = 'https://auth.openai.com/oauth/token';
const REFRESH_MARGIN_MS = 5 * 60 * 1000; // 5 minutes before expiry
const REFRESH_RETRY_BACKOFF_MS = 5 * 60 * 1000;
const CODEX_IMAGE_HOST_MODEL = 'gpt-5.4';
const DEFAULT_IMAGE_SIZE = '1024x1024';
const DEFAULT_IMAGE_QUALITY = 'medium';
const CODEX_IMAGE_INSTRUCTIONS = 'You must fulfill image requests by using the image_generation tool.';
const INLINE_REASONING_PLACEHOLDER = '<think> </think>';

function resolveAuthPathEntry(pathOrHome: string | undefined): string {
    const candidate = pathOrHome?.trim() ?? '';
    if (!candidate) return '';
    return candidate.endsWith('.json') ? candidate : join(candidate, 'auth.json');
}

function hashAccountKey(accountId: string): string {
    return createHash('sha256').update(accountId).digest('hex').slice(0, 16);
}

function extractSessionIdFromRequest(request: Record<string, unknown>): string | null {
    const messages = Array.isArray(request['messages']) ? request['messages'] as Array<Record<string, unknown>> : [];
    const systemMessage = messages.find((message) => message['role'] === 'system');
    const systemContent = typeof systemMessage?.['content'] === 'string' ? systemMessage['content'] : '';
    const senderMatch = systemContent.match(/"sender_id"\s*:\s*"(\d+)"/);
    const senderId = senderMatch?.[1];
    return senderId ? createHash('sha256').update(senderId).digest('hex').slice(0, 8) : null;
}

function resolvePromptCacheKey(
    request: Record<string, unknown>,
    executionContext: RequestExecutionContext,
): string | null {
    const raw = executionContext.promptCacheKey
        ?? (typeof request['prompt_cache_key'] === 'string' ? request['prompt_cache_key'] : null)
        ?? (typeof request['user'] === 'string' ? request['user'] : null)
        ?? executionContext.sessionId
        ?? extractSessionIdFromRequest(request);
    if (!raw?.trim()) return null;
    return `clawroute:${createHash('sha256').update(raw.trim()).digest('hex').slice(0, 32)}`;
}

function resolveCacheBreakerPromptCacheKey(
    request: Record<string, unknown>,
    executionContext: RequestExecutionContext,
): string | null {
    return executionContext.promptCacheKey
        ?? (typeof request['prompt_cache_key'] === 'string' ? request['prompt_cache_key'] : null)
        ?? (typeof request['user'] === 'string' ? request['user'] : null)
        ?? executionContext.sessionId
        ?? extractSessionIdFromRequest(request);
}

function getSlotAccountId(slot: CodexAuthSlot): string | null {
    validateSlotAuthFileFresh(slot);
    if (slot.auth?.accountId) return slot.auth.accountId;
    const snapshot = readAuthFileSnapshot(slot.path);
    if (!snapshot) return null;
    const tokens = snapshot.data['tokens'] as Record<string, unknown> | undefined;
    const explicitAccountId = tokens?.['account_id'];
    if (typeof explicitAccountId === 'string' && explicitAccountId.length > 0) return explicitAccountId;
    const idToken = typeof tokens?.['id_token'] === 'string' ? tokens['id_token'] : undefined;
    return deriveAccountId(idToken) ?? null;
}

function getSlotAccountKey(slot: CodexAuthSlot): string | null {
    const accountId = getSlotAccountId(slot);
    return accountId ? hashAccountKey(accountId) : null;
}

function authRetryAtIso(retryAt: number | null | undefined): string | null {
    return retryAt && retryAt > Date.now() ? new Date(retryAt).toISOString() : null;
}

function markSlotAuthAvailable(slot: CodexAuthSlot): void {
    slot.authUnavailableReason = null;
    slot.authRetryAt = null;
}

function markSlotAuthUnavailable(
    slot: CodexAuthSlot,
    reason: CodexAuthUnavailableReason,
    retryAt: number | null = null,
): void {
    slot.auth = null;
    slot.authFileFingerprint = null;
    slot.authUnavailableReason = reason;
    slot.authRetryAt = retryAt;
}

function validateSlotAuthFileFresh(slot: CodexAuthSlot): void {
    if (!slot.auth || !slot.authFileFingerprint) return;
    const fingerprint = readAuthFileFingerprint(slot.path);
    if (fingerprint === slot.authFileFingerprint) return;

    const oldAccountKey = slot.auth.accountId ? hashAccountKey(slot.auth.accountId) : null;
    slot.auth = null;
    slot.authFileFingerprint = null;
    markSlotAuthAvailable(slot);
    if (oldAccountKey) {
        sessionAffinities.forEach((affinity, key) => {
            if (affinity.accountKey === oldAccountKey) sessionAffinities.delete(key);
        });
        pendingLeasesByAccountKey.delete(oldAccountKey);
    }
    const slotIndex = authSlots.indexOf(slot);
    if (slotIndex >= 0) {
        pendingLeasesBySlotIndex.delete(slotIndex);
        slotLastSelectedAtByIndex.delete(slotIndex);
        invalidateCodexCacheLeaseForSlot(slotIndex);
    }
}

function getSlotAuthStatus(slot: CodexAuthSlot): {
    authAvailable: boolean;
    authUnavailableReason: CodexAuthUnavailableReason | null;
    authRetryAt: string | null;
} {
    validateSlotAuthFileFresh(slot);
    if (slot.auth && !isTokenExpired(slot.auth.accessToken)) {
        markSlotAuthAvailable(slot);
        return { authAvailable: true, authUnavailableReason: null, authRetryAt: null };
    }

    const authData = readAuthFile(slot.path);
    const tokens = authData?.['tokens'] as Record<string, unknown> | undefined;
    const accessToken = tokens?.['access_token'];
    if (!authData || typeof accessToken !== 'string' || accessToken.length === 0) {
        markSlotAuthUnavailable(slot, 'missing');
        return { authAvailable: false, authUnavailableReason: 'missing', authRetryAt: null };
    }

    const refreshToken = tokens?.['refresh_token'];
    if (isTokenExpired(accessToken)) {
        const retryAt = authRefreshRetryAt.get(slot.path) ?? null;
        const reason: CodexAuthUnavailableReason = typeof refreshToken === 'string' && retryAt
            ? 'expired_refresh_failed'
            : 'expired';
        markSlotAuthUnavailable(slot, reason, retryAt);
        return { authAvailable: false, authUnavailableReason: reason, authRetryAt: authRetryAtIso(retryAt) };
    }

    if (!getSlotAccountId(slot)) {
        markSlotAuthUnavailable(slot, 'unknown_account');
        return { authAvailable: false, authUnavailableReason: 'unknown_account', authRetryAt: null };
    }

    markSlotAuthAvailable(slot);
    return { authAvailable: true, authUnavailableReason: null, authRetryAt: null };
}

function serializeSlotIdentity(slot: CodexAuthSlot, slotIndex: number) {
    validateSlotAuthFileFresh(slot);
    const status = getSlotAuthStatus(slot);
    return {
        slotIndex,
        slotPath: slot.path,
        accountKey: getSlotAccountKey(slot),
        rateLimitedUntil: slot.rateLimitedUntil,
        ...status,
    };
}

function isSlotAuthSelectable(slot: {
    authAvailable: boolean;
    authUnavailableReason: CodexAuthUnavailableReason | null;
}): boolean {
    return slot.authAvailable || slot.authUnavailableReason === 'expired';
}

function incrementLeaseCounter(map: Map<string | number, number>, key: string | number): void {
    map.set(key, (map.get(key) ?? 0) + 1);
}

function decrementLeaseCounter(map: Map<string | number, number>, key: string | number): void {
    const next = (map.get(key) ?? 0) - 1;
    if (next > 0) {
        map.set(key, next);
    } else {
        map.delete(key);
    }
}

function claimSelectionLease(accountKey: string | null, slotIndex: number): CodexSelectionLease {
    const selectedAt = Date.now();
    if (accountKey) incrementLeaseCounter(pendingLeasesByAccountKey, accountKey);
    incrementLeaseCounter(pendingLeasesBySlotIndex, slotIndex);
    slotLastSelectedAtByIndex.set(slotIndex, selectedAt);
    return { accountKey, slotIndex, selectedAt };
}

function releaseSelectionLease(
    lease: CodexSelectionLease | null,
    sessionId: string | null,
    rememberAffinity: boolean,
): void {
    if (!lease) return;
    if (lease.accountKey) decrementLeaseCounter(pendingLeasesByAccountKey, lease.accountKey);
    decrementLeaseCounter(pendingLeasesBySlotIndex, lease.slotIndex);
    if (!rememberAffinity || !sessionId || !lease.accountKey) return;
    const slot = authSlots[lease.slotIndex];
    if (slot && getSlotAccountKey(slot) !== lease.accountKey) return;
    sessionAffinities.set(sessionId, {
        provider: 'codex',
        accountKey: lease.accountKey,
        slotIndex: lease.slotIndex,
        lastSelectedAt: new Date(lease.selectedAt).toISOString(),
        lastCompletedAt: new Date().toISOString(),
    });
}

function getSessionAffinityContext(sessionId: string | null) {
    if (!sessionId) return null;
    const preferred = sessionAffinities.get(sessionId);
    return {
        sessionId,
        cacheEligible: true,
        preferredProvider: 'codex' as const,
        preferredAccountKey: preferred?.accountKey ?? '',
    };
}

function getLegacyFirstEligibleSlot(input: {
    now: number;
    excludedSlotIndexes: Set<number>;
    excludedAccountKeys: Set<string>;
}): { slotIndex: number | null; accountKey: string | null } {
    if (authSlots.length === 0) return { slotIndex: null, accountKey: null };
    for (let attempt = 0; attempt < authSlots.length; attempt++) {
        const slotIndex = (currentSlotIndex + attempt) % authSlots.length;
        if (input.excludedSlotIndexes.has(slotIndex)) continue;
        const slot = authSlots[slotIndex]!;
        if (slot.rateLimitedUntil > input.now) continue;
        const accountKey = getSlotAccountKey(slot);
        if (accountKey && input.excludedAccountKeys.has(accountKey)) continue;
        return { slotIndex, accountKey };
    }
    return { slotIndex: null, accountKey: null };
}

function logCodexScheduleShadowDecision(selection: BalanceLoaderSelection, legacy: {
    slotIndex: number | null;
    accountKey: string | null;
}): void {
    const scheduledWinner = selection.selectedSlotIndex === null
        ? null
        : { slotIndex: selection.selectedSlotIndex, accountKey: selection.selectedAccountKey };
    const legacyWinner = legacy.slotIndex === null
        ? null
        : { slotIndex: legacy.slotIndex, accountKey: legacy.accountKey };
    const differs = scheduledWinner?.slotIndex !== legacyWinner?.slotIndex
        || scheduledWinner?.accountKey !== legacyWinner?.accountKey;
    if (!differs && !selection.fallbackReason) return;

    console.log(JSON.stringify({
        event: 'codex_schedule_shadow_decision',
        currentWeekdayUtc: selection.currentWeekdayUtc,
        activeWeekday: selection.activeWeekday ?? null,
        scheduled_winner: scheduledWinner,
        legacy_winner: legacyWinner,
        decision_reason: selection.decisionReason ?? null,
        fallback_reason: selection.fallbackReason,
        active_lane_telemetry_fresh: selection.activeLaneTelemetryFresh ?? false,
    }));
}

async function getBalanceLoaderSelection(input: {
    now: number;
    sessionId: string | null;
    excludedSlotIndexes: Set<number>;
    excludedAccountKeys: Set<string>;
}): Promise<BalanceLoaderSelection> {
    if (process.env['OPENAI_CODEX_TOKEN']) {
        return { selectedSlotIndex: null, selectedAccountKey: null, affinityApplied: false, fallbackReason: 'missing_usage' };
    }

    ensureCodexSlots();
    const slotIdentities = authSlots.map(serializeSlotIdentity);
    const scheduledSlots = slotIdentities
        .filter((slot): slot is typeof slot & { accountKey: string } => Boolean(slot.accountKey))
        .map((slot) => ({ accountKey: slot.accountKey, slotIndex: slot.slotIndex }));
    if (scheduledSlots.length === 0) {
        return { selectedSlotIndex: null, selectedAccountKey: null, affinityApplied: false, fallbackReason: 'unknown_account' };
    }

    const { seedCodexAccountSchedule } = await import('./logger.js');
    const scheduleRows: CodexAccountScheduleRow[] = seedCodexAccountSchedule(
        scheduledSlots,
        getCodexScheduleStartWeekday(),
        { flush: true },
    );
    if (scheduleRows.length === 0) {
        return { selectedSlotIndex: null, selectedAccountKey: null, affinityApplied: false, fallbackReason: 'selector_error' };
    }

    const { getCodexUsageSelectorSnapshot } = await import('./codex-usage.js');
    const selectorSnapshot = await getCodexUsageSelectorSnapshot({
        slots: slotIdentities,
        allowBackgroundRefresh: false,
    });
    const activation = resolveCodexActivatedSlots({
        now: input.now,
        startWeekday: getCodexScheduleStartWeekday(),
        slots: slotIdentities,
        scheduleRows,
        accounts: selectorSnapshot.accounts,
    });
    const unavailableSlotIndexes = new Set([
        ...input.excludedSlotIndexes,
        ...slotIdentities
            .filter((slot) => !isSlotAuthSelectable(slot))
            .map((slot) => slot.slotIndex),
        ...slotIdentities
            .filter((slot) => !activation.enabledSlotIndexes.has(slot.slotIndex)
                || !activation.activatedSlotIndexes.has(slot.slotIndex))
            .map((slot) => slot.slotIndex),
    ]);
    const activatedRefreshSlotIndexes = new Set([
        ...(selectorSnapshot.missingUsageSlotIndexes ?? []),
        ...selectorSnapshot.accounts
            .filter((account) => account.stale)
            .flatMap((account) => account.slotIndexes),
    ].filter((slotIndex) => activation.activatedSlotIndexes.has(slotIndex)));
    if (activatedRefreshSlotIndexes.size > 0) {
        const { getCodexUsage } = await import('./codex-usage.js');
        void getCodexUsage({ slotIndexes: [...activatedRefreshSlotIndexes] }).catch(() => undefined);
    }

    const relevantSlots = slotIdentities.filter((slot) => {
        if (!isSlotAuthSelectable(slot)) return false;
        if (slot.rateLimitedUntil > input.now) return false;
        if (unavailableSlotIndexes.has(slot.slotIndex)) return false;
        return !(slot.accountKey && input.excludedAccountKeys.has(slot.accountKey));
    });
    const relevantSlotIndexes = new Set(relevantSlots.map((slot) => slot.slotIndex));
    if ((selectorSnapshot.unknownAccountSlotIndexes ?? []).some((slotIndex) => relevantSlotIndexes.has(slotIndex))) {
        return { selectedSlotIndex: null, selectedAccountKey: null, affinityApplied: false, fallbackReason: 'unknown_account' };
    }

    const { selectCodexBalanceCandidate } = await import('./codex-balance-loader.js');
    const sessionAffinity = getSessionAffinityContext(input.sessionId);
    const result = selectCodexBalanceCandidate({
        now: input.now,
        provider: 'codex',
        accounts: selectorSnapshot.accounts,
        slots: slotIdentities
            .filter((slot) => slot.accountKey)
            .map((slot) => ({
                slotIndex: slot.slotIndex,
                accountKey: slot.accountKey!,
                pendingLeases: pendingLeasesBySlotIndex.get(slot.slotIndex) ?? 0,
                lastSelectedAt: slotLastSelectedAtByIndex.get(slot.slotIndex)
                    ? new Date(slotLastSelectedAtByIndex.get(slot.slotIndex)!).toISOString()
                    : null,
                rateLimitedUntil: slot.rateLimitedUntil,
            })),
        excludedSlotIndexes: unavailableSlotIndexes,
        excludedAccountKeys: input.excludedAccountKeys,
        sessionAffinity,
        schedule: {
            rows: scheduleRows.filter((row) => activation.activatedSlotIndexes.has(row.slotIndex)),
            currentWeekdayUtc: getUtcWeekday(input.now),
        },
    });
    const selectedAccount = selectorSnapshot.accounts.find((account) => account.accountKey === result.selectedAccountKey);
    const preferredAccount = sessionAffinity?.preferredAccountKey
        ? selectorSnapshot.accounts.find((account) => account.accountKey === sessionAffinity.preferredAccountKey)
        : null;
    const weeklyResetAt = selectedAccount?.weekly?.resetAt ?? null;
    const weeklyResidual = selectedAccount?.weekly
        ? Math.max(0, 1 - selectedAccount.weekly.usedPercent / 100)
        : null;
    const remainingFraction = weeklyResetAt
        ? Math.max((Date.parse(weeklyResetAt) - input.now) / (7 * 24 * 60 * 60_000), 1 / (7 * 24))
        : null;

    return {
        selectedSlotIndex: result.selectedSlotIndex,
        selectedAccountKey: result.selectedAccountKey,
        affinityApplied: result.affinityApplied,
        fallbackReason: result.fallbackReason,
        decisionReason: result.decisionReason,
        currentWeekdayUtc: result.currentWeekdayUtc,
        activeWeekday: result.activeWeekday,
        activeLaneTelemetryFresh: result.activeLaneTelemetryFresh,
        weeklyUsedPercent: selectedAccount?.weekly?.usedPercent ?? null,
        weeklyResetAt,
        fiveHourUsedPercent: selectedAccount?.fiveHour?.usedPercent ?? null,
        fiveHourResetAt: selectedAccount?.fiveHour?.resetAt ?? null,
        preferredAccountKey: sessionAffinity?.preferredAccountKey || null,
        preferredSlotIndex: sessionAffinities.get(input.sessionId ?? '')?.slotIndex ?? null,
        preferredFiveHourUsedPercent: preferredAccount?.fiveHour?.usedPercent ?? null,
        preferredFiveHourResetAt: preferredAccount?.fiveHour?.resetAt ?? null,
        requiredBurnRate: weeklyResidual !== null && remainingFraction !== null
            ? weeklyResidual / remainingFraction
            : null,
        eligibleAccountKeys: new Set([
            ...(result.selectedAccountKey ? [result.selectedAccountKey] : []),
            ...(result.scores ?? []).map((score) => score.accountKey),
        ]),
        eligibleSlotIndexes: new Set([
            ...(result.selectedSlotIndex !== null ? [result.selectedSlotIndex] : []),
            ...(result.scores ?? []).flatMap((score) => score.slotIndexes ?? []),
        ]),
    };
}

export async function getCodexBalancerState(): Promise<CodexBalancerState> {
    ensureCodexSlots();
    const now = Date.now();
    const slots = authSlots.map(serializeSlotIdentity);
    const scheduledSlots = slots
        .filter((slot): slot is typeof slot & { accountKey: string } => Boolean(slot.accountKey))
        .map((slot) => ({ accountKey: slot.accountKey, slotIndex: slot.slotIndex }));
    const { seedCodexAccountSchedule } = await import('./logger.js');
    const scheduleRows = seedCodexAccountSchedule(
        scheduledSlots,
        getCodexScheduleStartWeekday(),
        { flush: true },
    );
    const { getCodexUsageSelectorSnapshot } = await import('./codex-usage.js');
    const selector = await getCodexUsageSelectorSnapshot({ slots, allowBackgroundRefresh: false });
    const state = buildCodexBalancerState({
        now,
        startWeekday: getCodexScheduleStartWeekday(),
        slots,
        scheduleRows,
        accounts: selector.accounts,
    });
    clearExpiredCacheLeases(now);
    return {
        ...state,
        activeLease: serializeActiveCacheLease(now),
        coldMigrationDecisions: getPendingCodexColdMigrationDecisions(),
    };
}

export function selectAutomaticCodexUsageSlotIndexes(input: {
    slots: Array<{ slotIndex: number }>;
    activatedSlotIndexes: Set<number>;
    enabledSlotIndexes: Set<number>;
}): number[] {
    return input.slots
        .filter((slot) => slot.slotIndex === 0
            || (input.activatedSlotIndexes.has(slot.slotIndex)
                && input.enabledSlotIndexes.has(slot.slotIndex)))
        .map((slot) => slot.slotIndex)
        .sort((left, right) => left - right);
}

export async function getCodexAutomaticUsageSlotIndexes(): Promise<number[]> {
    ensureCodexSlots();
    const slots = authSlots.map(serializeSlotIdentity);
    if (process.env['OPENAI_CODEX_TOKEN']) return [0];
    if (getEffectiveCodexBalancerSettings().settings.mode !== 'on') {
        return slots.map((slot) => slot.slotIndex);
    }

    const scheduledSlots = slots
        .filter((slot): slot is typeof slot & { accountKey: string } => Boolean(slot.accountKey))
        .map((slot) => ({ accountKey: slot.accountKey, slotIndex: slot.slotIndex }));
    const { seedCodexAccountSchedule } = await import('./logger.js');
    const scheduleRows = seedCodexAccountSchedule(
        scheduledSlots,
        getCodexScheduleStartWeekday(),
        { flush: true },
    );
    const { getCodexUsageSelectorSnapshot } = await import('./codex-usage.js');
    const selector = await getCodexUsageSelectorSnapshot({ slots, allowBackgroundRefresh: false });
    const activation = resolveCodexActivatedSlots({
        now: Date.now(),
        startWeekday: getCodexScheduleStartWeekday(),
        slots,
        scheduleRows,
        accounts: selector.accounts,
    });
    activation.activatedSlotIndexes.add(0);
    return selectAutomaticCodexUsageSlotIndexes({
        slots,
        activatedSlotIndexes: activation.activatedSlotIndexes,
        enabledSlotIndexes: activation.enabledSlotIndexes,
    });
}

// ── Auth Loading ───────────────────────────────────────────────────

/**
 * Parse JWT claims without validation (we only need the expiry and account_id).
 */
function parseJwtClaims(token: string): Record<string, unknown> | null {
    const parts = token.split('.');
    if (parts.length !== 3 || !parts[1]) return null;
    try {
        const padded = parts[1] + '='.repeat((4 - (parts[1].length % 4)) % 4);
        const payload = Buffer.from(padded, 'base64url').toString('utf-8');
        const parsed = JSON.parse(payload);
        return typeof parsed === 'object' && parsed !== null ? parsed : null;
    } catch {
        return null;
    }
}

/**
 * Derive the ChatGPT account ID from an id_token JWT.
 */
function deriveAccountId(idToken: string | undefined): string | undefined {
    if (!idToken) return undefined;
    const claims = parseJwtClaims(idToken);
    if (!claims) return undefined;
    const authClaim = claims['https://api.openai.com/auth'];
    if (typeof authClaim === 'object' && authClaim !== null) {
        const accountId = (authClaim as Record<string, unknown>)['chatgpt_account_id'];
        if (typeof accountId === 'string' && accountId.length > 0) return accountId;
    }
    return undefined;
}

/**
 * Check if the access_token JWT is expired or about to expire.
 */
function isTokenExpired(accessToken: string): boolean {
    const claims = parseJwtClaims(accessToken);
    if (!claims || typeof claims['exp'] !== 'number') return false;
    const expiryMs = (claims['exp'] as number) * 1000;
    return expiryMs <= Date.now() + REFRESH_MARGIN_MS;
}

/**
 * Resolve auth file paths (supports multi-key rotation and CODEX_HOME-style dirs).
 */
export function resolveAuthPaths(): string[] {
    const multiPaths = process.env['OPENAI_CODEX_AUTH_PATHS'];
    if (multiPaths && multiPaths.trim()) {
        return multiPaths.split(',').map(resolveAuthPathEntry).filter(Boolean);
    }
    if (process.env['OPENAI_CODEX_AUTH_PATH']) {
        return [resolveAuthPathEntry(process.env['OPENAI_CODEX_AUTH_PATH'])];
    }
    if (process.env['OPENAI_CODEX_TOKEN']) {
        return []; // Token mode, no file-based rotation
    }
    const codexHome = process.env['CODEX_HOME'] || join(homedir(), '.codex');
    return [resolveAuthPathEntry(codexHome)];
}

/**
 * Read and parse the auth.json file.
 */
function readAuthFileSnapshot(path: string): AuthFileSnapshot | null {
    try {
        if (!existsSync(path)) return null;
        const content = readFileSync(path, 'utf-8');
        const parsed = JSON.parse(content);
        if (typeof parsed !== 'object' || parsed === null) return null;
        return {
            data: parsed as Record<string, unknown>,
            fingerprint: createHash('sha256').update(content).digest('hex'),
        };
    } catch {
        return null;
    }
}

function readAuthFileFingerprint(path: string): string | null {
    return readAuthFileSnapshot(path)?.fingerprint ?? null;
}

function readAuthFile(path: string): Record<string, unknown> | null {
    return readAuthFileSnapshot(path)?.data ?? null;
}

/**
 * Refresh the access token using the OAuth refresh_token flow.
 */
async function refreshTokens(
    refreshToken: string,
    proxyAgent: ProxyAgent | null,
    timeoutMs?: number,
): Promise<{ accessToken: string; idToken?: string; refreshToken: string; accountId?: string } | null> {
    const controller = new AbortController();
    const timeoutId = timeoutMs && timeoutMs > 0
        ? setTimeout(() => controller.abort(), timeoutMs)
        : null;

    try {
        const fetchOptions: FetchInitWithDispatcher = {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                grant_type: 'refresh_token',
                refresh_token: refreshToken,
                client_id: OAUTH_CLIENT_ID,
                scope: 'openid profile email offline_access',
            }),
            signal: controller.signal,
        };
        if (proxyAgent) fetchOptions.dispatcher = proxyAgent;

        const response = proxyAgent
            ? await fetchWithProxyAgent(OAUTH_TOKEN_URL, fetchOptions)
            : await fetch(OAUTH_TOKEN_URL, fetchOptions as RequestInit);
        if (!response.ok) return null;

        const payload = await response.json() as Record<string, unknown>;
        const newAccessToken = payload['access_token'];
        if (typeof newAccessToken !== 'string') return null;

        const newIdToken = typeof payload['id_token'] === 'string' ? payload['id_token'] : undefined;
        const newRefreshToken = typeof payload['refresh_token'] === 'string'
            ? payload['refresh_token']
            : refreshToken;

        return {
            accessToken: newAccessToken,
            idToken: newIdToken,
            refreshToken: newRefreshToken,
            accountId: deriveAccountId(newIdToken),
        };
    } catch (err) {
        console.warn('[codex-transport] Token refresh failed:', err instanceof Error ? err.message : err);
        return null;
    } finally {
        if (timeoutId) clearTimeout(timeoutId);
    }
}

async function refreshTokensForPath(
    path: string,
    refreshToken: string,
    proxyAgent: ProxyAgent | null,
    timeoutMs?: number,
): Promise<Awaited<ReturnType<typeof refreshTokens>>> {
    if ((authRefreshRetryAt.get(path) ?? 0) > Date.now()) return null;
    const existing = authRefreshPromises.get(path);
    if (existing) return existing;

    const refreshPromise = refreshTokens(refreshToken, proxyAgent, timeoutMs)
        .then((refreshed) => {
            if (refreshed) authRefreshRetryAt.delete(path);
            else authRefreshRetryAt.set(path, Date.now() + REFRESH_RETRY_BACKOFF_MS);
            return refreshed;
        })
        .finally(() => authRefreshPromises.delete(path));
    authRefreshPromises.set(path, refreshPromise);
    return refreshPromise;
}

type AuthLoadResult =
    | { ok: true; auth: CodexAuth }
    | { ok: false; reason: CodexAuthUnavailableReason; retryAt?: number | null };

/**
 * Write updated tokens back to auth.json.
 */
function writeAuthFile(
    path: string,
    authData: Record<string, unknown>,
    tokens: Record<string, string | undefined>,
): void {
    try {
        mkdirSync(dirname(path), { recursive: true });
        const updated = {
            ...authData,
            tokens: {
                ...(authData['tokens'] as Record<string, unknown> ?? {}),
                ...tokens,
            },
            last_refresh: new Date().toISOString(),
        };
        writeFileSync(path, JSON.stringify(updated, null, 2), { encoding: 'utf-8', mode: 0o600 });
    } catch {
        console.warn(`[codex-transport] Refreshed token for ${path} is memory-only; auth path is not writable`);
    }
}

/**
 * Load auth from a specific file path (read, check expiry, refresh if needed).
 * Returns null if no valid credentials are found.
 */
async function loadAuthFromFileDetailed(
    path: string,
    proxyAgent: ProxyAgent | null,
    timeoutMs?: number,
    options: { refreshExpired?: boolean } = {},
): Promise<AuthLoadResult> {
    const snapshot = readAuthFileSnapshot(path);
    if (!snapshot) return { ok: false, reason: 'missing' };
    const authData = snapshot.data;
    let sourceFingerprint = snapshot.fingerprint;

    const tokens = authData['tokens'] as Record<string, unknown> | undefined;
    let accessToken = tokens?.['access_token'] as string | undefined;
    let idToken = tokens?.['id_token'] as string | undefined;
    let refreshToken = tokens?.['refresh_token'] as string | undefined;
    let accountId = (tokens?.['account_id'] as string | undefined) ?? deriveAccountId(idToken);

    if (!accessToken) return { ok: false, reason: 'missing' };

    // Refresh if expired or about to expire
    const refreshInFlight = authRefreshPromises.has(path);
    const refreshBackoffElapsed = (authRefreshRetryAt.get(path) ?? 0) <= Date.now();
    if (options.refreshExpired !== false
        && isTokenExpired(accessToken)
        && refreshToken
        && (refreshInFlight || refreshBackoffElapsed)) {
        if (!refreshInFlight) {
            console.log(`[codex-transport] Access token expired for ${path}, refreshing...`);
        }
        const refreshed = await refreshTokensForPath(path, refreshToken, proxyAgent, timeoutMs);
        if (refreshed) {
            accessToken = refreshed.accessToken;
            idToken = refreshed.idToken ?? idToken;
            refreshToken = refreshed.refreshToken;
            accountId = refreshed.accountId ?? accountId;

            writeAuthFile(path, authData, {
                access_token: accessToken,
                id_token: idToken,
                refresh_token: refreshToken,
                account_id: accountId,
            });
            sourceFingerprint = readAuthFileFingerprint(path) ?? sourceFingerprint;
            console.log('[codex-transport] Token refreshed successfully');
        } else {
            console.warn('[codex-transport] Token refresh failed, using existing token');
            return { ok: false, reason: 'expired_refresh_failed', retryAt: authRefreshRetryAt.get(path) ?? null };
        }
    }

    if (isTokenExpired(accessToken) && refreshToken && options.refreshExpired === false) {
        return { ok: false, reason: 'expired', retryAt: authRefreshRetryAt.get(path) ?? null };
    }

    if (isTokenExpired(accessToken) && refreshToken && !refreshBackoffElapsed && !refreshInFlight) {
        return { ok: false, reason: 'expired_refresh_failed', retryAt: authRefreshRetryAt.get(path) ?? null };
    }

    if (!accountId) {
        console.warn(`[codex-transport] No account_id found in ${path} — skipping`);
        return { ok: false, reason: 'unknown_account' };
    }

    return { ok: true, auth: { accessToken, accountId, refreshToken, idToken, sourcePath: path, sourceFingerprint } };
}

function ensureCodexSlots(): void {
    if (process.env['OPENAI_CODEX_TOKEN']) return;
    if (authSlots.length === 0) initializeSlots(resolveAuthPaths());
}

export function getCodexAuthSlots(): CodexAuthSlotSnapshot[] {
    if (process.env['OPENAI_CODEX_TOKEN']) {
        return [{
            slotIndex: 0,
            path: null,
            rateLimitedUntil: 0,
            authAvailable: true,
            authUnavailableReason: null,
            authRetryAt: null,
        }];
    }

    ensureCodexSlots();
    return authSlots.map((slot, slotIndex) => {
        const status = getSlotAuthStatus(slot);
        return {
            slotIndex,
            path: slot.path,
            rateLimitedUntil: slot.rateLimitedUntil,
            ...status,
        };
    });
}

export async function loadCodexUsageAuthSlot(
    slot: CodexAuthSlotSnapshot,
    proxyAgent: ProxyAgent | null,
    timeoutMs?: number,
): Promise<CodexAuth | null> {
    if (process.env['OPENAI_CODEX_TOKEN']) {
        const accessToken = process.env['OPENAI_CODEX_TOKEN'];
        return accessToken ? { accessToken, accountId: '' } : null;
    }

    ensureCodexSlots();
    const existing = authSlots[slot.slotIndex];
    if (!existing) return null;
    validateSlotAuthFileFresh(existing);
    if (existing.auth && !isTokenExpired(existing.auth.accessToken)) {
        markSlotAuthAvailable(existing);
        return existing.auth;
    }

    const result = await loadAuthFromFileDetailed(existing.path, proxyAgent, timeoutMs, { refreshExpired: false });
    if (result.ok) {
        existing.auth = result.auth;
        existing.authFileFingerprint = result.auth.sourceFingerprint ?? readAuthFileFingerprint(existing.path);
        existing.lastLoadAttempt = Date.now();
        markSlotAuthAvailable(existing);
        return result.auth;
    }
    markSlotAuthUnavailable(existing, result.reason, result.retryAt ?? null);
    return null;
}

// ── Rotation Helpers ───────────────────────────────────────────────

export function resetRotationState(): void {
    authSlots = [];
    currentSlotIndex = 0;
    lastRotationTime = 0;
    lastQueryEndTime = 0;
    activeRequests = 0;
    sessionAffinities.clear();
    activeCacheLeases.clear();
    pendingLeasesByAccountKey.clear();
    pendingLeasesBySlotIndex.clear();
    slotLastSelectedAtByIndex.clear();
    authRefreshPromises.clear();
    authRefreshRetryAt.clear();
    resetCodexCacheBreakerState();
    rotationIntervalMs = -1;
    rotationIdleMs = -1;
}

export function getRotationState(): {
    currentSlotIndex: number;
    activeRequests: number;
    lastRotationTime: number;
    lastQueryEndTime: number;
    slotCount: number;
} {
    return {
        currentSlotIndex,
        activeRequests,
        lastRotationTime,
        lastQueryEndTime,
        slotCount: authSlots.length,
    };
}

export function initializeSlots(paths: string[]): void {
    authSlots = paths.map(path => ({
        path,
        auth: null,
        authFileFingerprint: null,
        lastLoadAttempt: 0,
        rateLimitedUntil: 0,
        authUnavailableReason: null,
        authRetryAt: null,
    }));
    const now = Date.now();
    if (lastRotationTime === 0) lastRotationTime = now;
    if (lastQueryEndTime === 0) lastQueryEndTime = now;
    console.log(`[codex-rotation] Initialized ${authSlots.length} auth slot(s)`);
}

export function shouldRotate(): boolean {
    if (authSlots.length <= 1) return false;
    if (activeRequests > 0) return false;
    const now = Date.now();
    return (now - lastRotationTime) >= getRotationIntervalMs()
        && (now - lastQueryEndTime) >= getRotationIdleMs();
}

export function performRotation(): void {
    const oldIndex = currentSlotIndex;
    currentSlotIndex = (currentSlotIndex + 1) % authSlots.length;
    const now = Date.now();
    const idleMinutes = Math.round((now - lastQueryEndTime) / 60_000);
    const sinceRotation = formatDuration(now - lastRotationTime);
    lastRotationTime = now;
    const newSlot = authSlots[currentSlotIndex];
    if (newSlot) {
        newSlot.auth = null;
        newSlot.authFileFingerprint = null;
    }
    console.log(
        `[codex-rotation] Rotated from slot ${oldIndex} to slot ${currentSlotIndex}`
        + ` (idle: ${idleMinutes}m, since rotation: ${sinceRotation})`,
    );
}

function formatDuration(ms: number): string {
    const hours = Math.floor(ms / 3_600_000);
    const minutes = Math.round((ms % 3_600_000) / 60_000);
    return hours > 0 ? `${hours}h${minutes}m` : `${minutes}m`;
}

export function releaseCodexAuth(): void {
    if (activeRequests > 0) activeRequests--;
    lastQueryEndTime = Date.now();
}

/**
 * Advance to the next slot without imposing a rate-limit cooldown.
 * Used after transient errors (e.g. 500) so the next attempt tries a different account.
 */
function advanceSlot(): void {
    if (authSlots.length <= 1) return;
    const oldIndex = currentSlotIndex;
    currentSlotIndex = (currentSlotIndex + 1) % authSlots.length;
    console.log(`[codex-rotation] Advanced from slot ${oldIndex} to slot ${currentSlotIndex} (transient error)`);
}

type AuthResult =
    | { ok: true; auth: CodexAuth }
    | { ok: false; reason: 'all_rate_limited' | 'auth_missing' };

/**
 * Get the active Codex auth, handling slot rotation and fallback.
 * Increments activeRequests on success — caller MUST call releaseCodexAuth() when done.
 */
export async function getActiveCodexAuth(
    proxyAgent: ProxyAgent | null,
    excludedSlotIndexes = new Set<number>(),
    preferredSlotIndex?: number,
    excludedAccountKeys = new Set<string>(),
): Promise<AuthResult> {
    // Fast path: explicit token (no rotation)
    if (process.env['OPENAI_CODEX_TOKEN']) {
        activeRequests++;
        return {
            ok: true,
            auth: {
                accessToken: process.env['OPENAI_CODEX_TOKEN'],
                accountId: '', // Token mode — account ID derived at request time
            },
        };
    }

    // Initialize slots on first call
    if (authSlots.length === 0) {
        initializeSlots(resolveAuthPaths());
    }
    if (authSlots.length === 0) return { ok: false, reason: 'auth_missing' };

    // Check rotation BEFORE starting the request unless the selector requested a specific starting slot.
    if (preferredSlotIndex !== undefined && authSlots[preferredSlotIndex] && !excludedSlotIndexes.has(preferredSlotIndex)) {
        currentSlotIndex = preferredSlotIndex;
    } else if (shouldRotate()) {
        performRotation();
    }

    // Increment BEFORE async work (prevents race conditions)
    activeRequests++;

    // Try current slot, then rotate through others on failure
    const now = Date.now();
    let sawRateLimitedSlot = false;
    for (let attempt = 0; attempt < authSlots.length; attempt++) {
        const slotIndex = (currentSlotIndex + attempt) % authSlots.length;
        if (excludedSlotIndexes.has(slotIndex)) continue;
        const slot = authSlots[slotIndex]!;
        validateSlotAuthFileFresh(slot);
        const slotAccountKey = getSlotAccountKey(slot);
        if (slotAccountKey && excludedAccountKeys.has(slotAccountKey)) continue;

        // Skip slots that are currently rate-limited
        if (slot.rateLimitedUntil > now) {
            sawRateLimitedSlot = true;
            const remainMin = Math.ceil((slot.rateLimitedUntil - now) / 60_000);
            console.log(`[codex-rotation] Slot ${slotIndex} rate-limited for ${remainMin}m, skipping`);
            continue;
        }

        // Use cached slot auth if token still valid
        if (slot.auth && !isTokenExpired(slot.auth.accessToken)) {
            markSlotAuthAvailable(slot);
            if (attempt > 0) {
                currentSlotIndex = slotIndex;
                console.log(`[codex-rotation] Slot ${slotIndex} selected after ${attempt} skip(s)`);
            }
            return { ok: true, auth: slot.auth };
        }

        // Load from file
        const result = await loadAuthFromFileDetailed(slot.path, proxyAgent);
        if (result.ok) {
            slot.auth = result.auth;
            slot.authFileFingerprint = result.auth.sourceFingerprint ?? readAuthFileFingerprint(slot.path);
            slot.lastLoadAttempt = Date.now();
            markSlotAuthAvailable(slot);
            if (attempt > 0) currentSlotIndex = slotIndex;
            return { ok: true, auth: result.auth };
        }

        markSlotAuthUnavailable(slot, result.reason, result.retryAt ?? null);
        const retrySuffix = result.retryAt ? ` retry after ${new Date(result.retryAt).toISOString()}` : '';
        console.warn(`[codex-rotation] Slot ${slotIndex} (${slot.path}): auth load failed (${result.reason}), trying next${retrySuffix}`);
    }

    // All slots failed
    activeRequests--;
    return { ok: false, reason: sawRateLimitedSlot ? 'all_rate_limited' : 'auth_missing' };
}

// ── Request Translation ────────────────────────────────────────────

/**
 * Extract plain text from OpenAI message content (string or content parts array).
 */
function textContent(content: unknown): string {
    if (typeof content === 'string') return content;
    if (!Array.isArray(content)) return '';
    return content
        .filter((p: { type?: string; text?: string }) => p.type === 'text' && p.text)
        .map((p: { text: string }) => p.text)
        .join('');
}

function assistantReasoningContent(message: ChatMessage): string | undefined {
    const rawMessage = message as ChatMessage & Record<string, unknown>;
    const reasoningContent = rawMessage['reasoning_content'];
    if (typeof reasoningContent === 'string' && reasoningContent.length > 0) {
        return reasoningContent;
    }

    const reasoning = rawMessage['reasoning'];
    if (typeof reasoning === 'string' && reasoning.length > 0) {
        return reasoning;
    }

    return undefined;
}

/**
 * Convert OpenAI Chat Completions messages to Responses API input items.
 *
 * Chat Completions format:
 *   [{role: "system", content: "..."}, {role: "user", content: "..."}, ...]
 *
 * Responses API input format:
 *   [{role: "developer", content: "..."}, {role: "user", content: "..."},
 *    {type: "function_call", ...}, {type: "function_call_output", ...}]
 */
function chatMessagesToResponsesInput(messages: ChatMessage[]): unknown[] {
    const input: unknown[] = [];

    for (const msg of messages) {
        switch (msg.role) {
            case 'system':
            case 'developer':
                input.push({ role: 'developer', content: textContent(msg.content) });
                break;

            case 'user': {
                // Preserve multimodal content arrays (images etc.)
                if (Array.isArray(msg.content)) {
                    const parts = [];
                    for (const item of msg.content as Array<Record<string, unknown>>) {
                        if (item.type === 'text' && typeof item.text === 'string') {
                            parts.push({ type: 'input_text', text: item.text });
                        } else if (item.type === 'image_url' && typeof (item.image_url as Record<string, unknown>)?.url === 'string') {
                            parts.push({ type: 'input_image', image_url: (item.image_url as Record<string, unknown>).url });
                        }
                    }
                    input.push({ role: 'user', content: parts.length === 1 && parts[0]?.type === 'input_text' ? (parts[0] as { text: string }).text : parts });
                } else {
                    input.push({ role: 'user', content: textContent(msg.content) });
                }
                break;
            }

            case 'assistant': {
                const rawMessage = msg as ChatMessage & Record<string, unknown>;
                const reasoningContent = assistantReasoningContent(msg);
                if (reasoningContent) {
                    input.push({
                        type: 'reasoning',
                        summary: [{ type: 'summary_text', text: reasoningContent }],
                    });
                }

                const text = textContent(msg.content);
                if (text) {
                    const assistantItem: Record<string, unknown> = {
                        type: 'message',
                        role: 'assistant',
                        content: [{ type: 'output_text', text }],
                    };
                    if (typeof rawMessage['phase'] === 'string' && rawMessage['phase'].length > 0) {
                        assistantItem['phase'] = rawMessage['phase'];
                    }
                    input.push(assistantItem);
                }
                // Tool calls become separate function_call items
                if (msg.tool_calls) {
                    for (const tc of msg.tool_calls) {
                        input.push({
                            type: 'function_call',
                            call_id: tc.id,
                            name: tc.function.name,
                            arguments: tc.function.arguments,
                        });
                    }
                }
                break;
            }

            case 'tool':
                if (msg.tool_call_id) {
                    input.push({
                        type: 'function_call_output',
                        call_id: msg.tool_call_id,
                        output: typeof msg.content === 'string'
                            ? msg.content
                            : JSON.stringify(msg.content),
                    });
                }
                break;
        }
    }

    return input;
}

/**
 * Build the Codex Responses API request body from a Chat Completions request.
 */
export function buildCodexRequestBody(
    request: Record<string, unknown>,
    modelName: string,
    promptCacheKey?: string | null,
): Record<string, unknown> {
    const body: Record<string, unknown> = {
        model: modelName,
        input: chatMessagesToResponsesInput(request['messages'] as ChatMessage[]),
        stream: true, // Always stream from upstream; we'll collect for non-stream clients
        instructions: '',
        store: false,
    };
    if (promptCacheKey) body['prompt_cache_key'] = promptCacheKey;

    // Forward compatible parameters
    // Note: temperature and prompt_cache_retention are intentionally omitted — Codex endpoint rejects them as unsupported.
    if (request['top_p'] !== undefined) body['top_p'] = request['top_p'];
    if (request['tools']) {
        // Translate Chat Completions tool format → Responses API format.
        // CC: {type: "function", function: {name, description, parameters}}
        // RA: {type: "function", name, description, parameters, strict}
        const ccTools = request['tools'] as Array<Record<string, unknown>>;
        body['tools'] = ccTools.map(tool => {
            if (tool['type'] === 'function' && tool['function']) {
                const fn = tool['function'] as Record<string, unknown>;
                return {
                    type: 'function',
                    name: fn['name'],
                    ...(fn['description'] !== undefined ? { description: fn['description'] } : {}),
                    ...(fn['parameters'] !== undefined ? { parameters: fn['parameters'] } : {}),
                    ...(fn['strict'] !== undefined ? { strict: fn['strict'] } : {}),
                };
            }
            return tool; // Already in Responses API format or unknown — pass through
        });
    }
    if (request['tool_choice']) body['tool_choice'] = request['tool_choice'];
    if (request['reasoning_effort']) {
        body['reasoning'] = { effort: request['reasoning_effort'] };
    }
    // max_tokens → not directly supported; omit (Responses API has max_output_tokens
    // but the Codex endpoint strips it anyway per openai-oauth source)

    return body;
}

function isImageEditRequest(request: ImageGenerationRequest | ImageEditRequest): request is ImageEditRequest {
    return Array.isArray((request as ImageEditRequest).images);
}

async function fileToDataUrl(file: File): Promise<string> {
    const bytes = Buffer.from(await file.arrayBuffer());
    const mediaType = file.type && file.type.startsWith('image/') ? file.type : 'image/png';
    return `data:${mediaType};base64,${bytes.toString('base64')}`;
}

async function buildCodexImageInput(request: ImageGenerationRequest | ImageEditRequest): Promise<unknown> {
    if (!isImageEditRequest(request)) {
        return [
            {
                role: 'user',
                content: request.prompt,
            },
        ];
    }

    const content: Array<Record<string, string>> = [
        {
            type: 'input_text',
            text: request.prompt,
        },
    ];

    for (const image of request.images) {
        content.push({
            type: 'input_image',
            image_url: await fileToDataUrl(image.file),
        });
    }

    return [
        {
            role: 'user',
            content,
        },
    ];
}

async function buildCodexImageRequestBody(request: ImageGenerationRequest | ImageEditRequest): Promise<Record<string, unknown>> {
    const tool: Record<string, unknown> = {
        type: 'image_generation',
        model: 'gpt-image-2',
        size: request.size ?? DEFAULT_IMAGE_SIZE,
        quality: request.quality ?? DEFAULT_IMAGE_QUALITY,
        output_format: request.output_format ?? 'png',
        background: request.background ?? 'opaque',
        partial_images: 1,
    };

    if (isImageEditRequest(request)) {
        tool['action'] = 'edit';
        if (request.output_compression !== undefined) {
            const outputCompression = Number(request.output_compression);
            if (Number.isFinite(outputCompression)) {
                tool['output_compression'] = outputCompression;
            }
        }
    }

    return {
        model: CODEX_IMAGE_HOST_MODEL,
        input: await buildCodexImageInput(request),
        stream: true,
        store: false,
        instructions: CODEX_IMAGE_INSTRUCTIONS,
        tools: [tool],
        tool_choice: {
            type: 'allowed_tools',
            mode: 'required',
            tools: [{ type: 'image_generation' }],
        },
    };
}

function extractCodexImageResult(output: unknown): { imageB64: string | null; revisedPrompt?: string } {
    if (!Array.isArray(output)) {
        return { imageB64: null };
    }

    for (const rawItem of output) {
        const item = getErrorRecord(rawItem);
        if (!item || item['type'] !== 'image_generation_call') {
            continue;
        }

        const imageB64 = asNonEmptyString(item['result']);
        if (imageB64) {
            return {
                imageB64,
                revisedPrompt: asNonEmptyString(item['revised_prompt']),
            };
        }
    }

    return { imageB64: null };
}

async function executeCodexImageCall(
    auth: CodexAuth,
    request: ImageGenerationRequest | ImageEditRequest,
    proxyAgent: ProxyAgent | null,
    errorContext: CodexErrorContext = {},
): Promise<Response> {
    const url = `${CODEX_BASE_URL}/responses`;
    const headers: Record<string, string> = {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${auth.accessToken}`,
        'OpenAI-Beta': 'responses=experimental',
    };
    if (auth.accountId) headers['chatgpt-account-id'] = auth.accountId;

    const fetchOptions: FetchInitWithDispatcher = {
        method: 'POST',
        headers,
        body: JSON.stringify(await buildCodexImageRequestBody(request)),
    };
    if (proxyAgent) fetchOptions.dispatcher = proxyAgent;

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), getCodexRequestTimeoutMs());
    fetchOptions.signal = controller.signal;

    try {
        const upstream = proxyAgent
            ? await fetchWithProxyAgent(url, fetchOptions)
            : await fetch(url, fetchOptions as RequestInit);
        clearTimeout(timeoutId);

        if (!upstream.ok) {
            const errorBody = await upstream.text();
            return new Response(
                buildCodexErrorBody(upstream.status, errorBody, errorContext),
                { status: upstream.status, headers: { 'Content-Type': 'application/json' } },
            );
        }

        if (!upstream.body) {
            return new Response(
                JSON.stringify({
                    error: buildCodexErrorPayload(
                        'No response body from Codex image generation',
                        'No response body from Codex image generation',
                        'codex_image_empty',
                        'server_error',
                        errorContext,
                    ),
                }),
                { status: 502, headers: { 'Content-Type': 'application/json' } },
            );
        }

        let imageB64: string | null = null;
        let revisedPrompt: string | undefined;

        for await (const event of parseSSE(upstream.body)) {
            if (!event.data || event.data === '[DONE]') {
                continue;
            }

            let parsed: Record<string, unknown> | null = null;
            try {
                parsed = JSON.parse(event.data) as Record<string, unknown>;
            } catch {
                continue;
            }

            if (event.event === 'error' || parsed['error']) {
                const errorRecord = parsed['error'] ?? parsed;
                return new Response(
                    JSON.stringify({
                        error: buildCodexErrorPayload(
                            errorRecord,
                            'Codex image generation failed',
                            'codex_image_error',
                            'upstream_error',
                            errorContext,
                        ),
                    }),
                    { status: 502, headers: { 'Content-Type': 'application/json' } },
                );
            }

            if (event.event === 'response.image_generation_call.partial_image') {
                const partialImage = asNonEmptyString(parsed['partial_image_b64']);
                if (partialImage) {
                    imageB64 = partialImage;
                }
                continue;
            }

            if (event.event === 'response.output_item.done') {
                const result = extractCodexImageResult([parsed['item']]);
                if (result.imageB64) {
                    imageB64 = result.imageB64;
                    revisedPrompt = result.revisedPrompt;
                }
                continue;
            }

            if (event.event === 'response.completed') {
                const responseRecord = getErrorRecord(parsed['response']) ?? parsed;
                const result = extractCodexImageResult(responseRecord['output']);
                if (result.imageB64) {
                    imageB64 = result.imageB64;
                    revisedPrompt = result.revisedPrompt;
                }
            }
        }

        if (!imageB64) {
            return new Response(
                JSON.stringify({
                    error: buildCodexErrorPayload(
                        'Codex response contained no image_generation_call result',
                        'Codex response contained no image_generation_call result',
                        'codex_image_empty',
                        'server_error',
                        errorContext,
                    ),
                }),
                { status: 502, headers: { 'Content-Type': 'application/json' } },
            );
        }

        const body: Record<string, unknown> = {
            created: Math.floor(Date.now() / 1000),
            data: [
                {
                    b64_json: imageB64,
                    ...(revisedPrompt ? { revised_prompt: revisedPrompt } : {}),
                },
            ],
        };

        return new Response(JSON.stringify(body), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
        });
    } catch (err) {
        clearTimeout(timeoutId);
        const message = err instanceof Error ? err.message : 'Codex image generation failed';
        return new Response(
            JSON.stringify({
                error: buildCodexErrorPayload(
                    message,
                    'Codex image generation failed',
                    'codex_image_error',
                    'server_error',
                    errorContext,
                ),
            }),
            { status: 502, headers: { 'Content-Type': 'application/json' } },
        );
    }
}

// ── Response Translation ───────────────────────────────────────────

interface SSEEvent {
    event?: string;
    data?: string;
}

/**
 * Parse SSE events from a ReadableStream.
 */
async function* parseSSE(stream: ReadableStream<Uint8Array>): AsyncGenerator<SSEEvent> {
    const reader = stream.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    try {
        while (true) {
            const { value, done } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const blocks = buffer.split(/\r?\n\r?\n/);
            buffer = blocks.pop() ?? '';

            for (const block of blocks) {
                if (!block.trim()) continue;
                const event: SSEEvent = {};
                const dataLines: string[] = [];

                for (const line of block.split(/\r?\n/)) {
                    if (line.startsWith('event:')) {
                        event.event = line.slice(6).trim();
                    } else if (line.startsWith('data:')) {
                        dataLines.push(line.slice(5).trimStart());
                    }
                }

                if (dataLines.length > 0) {
                    event.data = dataLines.join('\n');
                }
                yield event;
            }
        }

        // Process remaining buffer
        if (buffer.trim()) {
            const event: SSEEvent = {};
            const dataLines: string[] = [];
            for (const line of buffer.split(/\r?\n/)) {
                if (line.startsWith('event:')) {
                    event.event = line.slice(6).trim();
                } else if (line.startsWith('data:')) {
                    dataLines.push(line.slice(5).trimStart());
                }
            }
            if (dataLines.length > 0) {
                event.data = dataLines.join('\n');
                yield event;
            }
        }
    } finally {
        reader.releaseLock();
    }
}

/**
 * Map a Responses API finish reason to Chat Completions format.
 */
function mapFinishReason(responseStatus: string | undefined): string {
    switch (responseStatus) {
        case 'completed': return 'stop';
        case 'incomplete': return 'length';
        case 'cancelled': return 'stop';
        default: return 'stop';
    }
}

function asNonEmptyString(value: unknown): string | undefined {
    return typeof value === 'string' && value.length > 0 ? value : undefined;
}

function asErrorCode(value: unknown): string | undefined {
    if (typeof value === 'string' && value.length > 0) return value;
    if (typeof value === 'number' && Number.isFinite(value)) return String(value);
    return undefined;
}

function getErrorRecord(value: unknown): Record<string, unknown> | null {
    return typeof value === 'object' && value !== null ? value as Record<string, unknown> : null;
}

function getCodexErrorSlot(errorRecord: Record<string, unknown> | null, errorContext?: CodexErrorContext): number | undefined {
    const slot = errorRecord?.['slot'];
    if (typeof slot === 'number' && Number.isInteger(slot) && slot >= 0) {
        return slot;
    }
    return errorContext?.slot;
}

function formatCodexErrorMessage(
    message: string,
    code: string | undefined,
    slot: number | undefined,
): string {
    const parts: string[] = [];
    if (slot !== undefined) {
        parts.push(`slot:${slot}`);
    }
    if (code) {
        parts.push(`code:${code}`);
    }
    return parts.length > 0 ? `${message} [${parts.join(' ')}]` : message;
}

function buildCodexErrorPayload(
    errorValue: unknown,
    fallbackMessage: string,
    fallbackCode: string,
    fallbackType: string,
    errorContext?: CodexErrorContext,
): Record<string, unknown> {
    const errorRecord = getErrorRecord(errorValue);
    const code = asErrorCode(errorRecord?.['code']) ?? fallbackCode;
    const type = asNonEmptyString(errorRecord?.['type']) ?? fallbackType;
    const stack = asNonEmptyString(errorRecord?.['stack']);
    const slot = getCodexErrorSlot(errorRecord, errorContext);
    const baseMessage = typeof errorValue === 'string'
        ? errorValue
        : asNonEmptyString(errorRecord?.['message']) ?? fallbackMessage;

    const error: Record<string, unknown> = {
        message: formatCodexErrorMessage(baseMessage, code, slot),
        type,
        code,
    };
    if (stack) {
        error['stack'] = stack;
    }
    if (slot !== undefined) {
        error['slot'] = slot;
    }
    if (typeof errorRecord?.['resets_at'] === 'number') {
        error['resets_at'] = errorRecord['resets_at'];
    }
    if (typeof errorRecord?.['resets_in_seconds'] === 'number') {
        error['resets_in_seconds'] = errorRecord['resets_in_seconds'];
    }
    return error;
}

function getCodexErrorResponseRecord(body: string): Record<string, unknown> | null {
    try {
        const parsed = JSON.parse(body) as Record<string, unknown>;
        return getErrorRecord(parsed['error']);
    } catch {
        return null;
    }
}

function getCodexResponseStatus(body: string): number {
    const error = getCodexErrorResponseRecord(body);
    if (!error) {
        return 200;
    }

    const code = asErrorCode(error['code']);
    const type = asNonEmptyString(error['type']);
    if (type === 'auth_error' || code === 'invalid_api_key' || code === 'unauthorized') {
        return 401;
    }
    if (type === 'rate_limit_error' || type === 'usage_limit_reached' || code === 'rate_limit_exceeded' || code === 'usage_limit_reached' || code === 'codex_429') {
        return 429;
    }
    return 502;
}

function applyCodexTerminalErrorSideEffects(errorPayload: Record<string, unknown>): void {
    const slot = getCodexErrorSlot(errorPayload);
    if (slot === undefined) return;

    const errorBody = JSON.stringify({ error: errorPayload });
    const status = getCodexResponseStatus(errorBody);
    if (status === 401) {
        invalidateSlotAuth(slot);
        console.warn(`[codex-rotation] Slot ${slot} returned streaming auth error, invalidating auth`);
        return;
    }
    if (status === 429 && getCodexUpstreamType(errorBody) === 'usage_limit_reached') {
        markSlotRateLimited(slot, errorBody);
        return;
    }

    const code = asNonEmptyString(errorPayload['code']);
    const type = asNonEmptyString(errorPayload['type']);
    if (
        status >= 500 ||
        code === 'server_error' ||
        type === 'server_error' ||
        type === 'upstream_error' ||
        code?.startsWith('codex_5')
    ) {
        console.warn(`[codex-rotation] Slot ${slot} returned streaming ${code ?? type ?? 'server error'}, advancing`);
        if (slot === currentSlotIndex) {
            advanceSlot();
        } else if (authSlots.length > 1) {
            currentSlotIndex = (slot + 1) % authSlots.length;
            console.log(`[codex-rotation] Advanced from slot ${slot} to slot ${currentSlotIndex} (streaming error)`);
        }
    }
}

/**
 * Transform a Codex Responses API SSE stream into an OpenAI Chat Completions SSE stream.
 *
 * Responses API events → Chat Completions chunks:
 *   response.output_text.delta      → delta.content
 *   response.output_item.added      → (tool call start) delta.tool_calls
 *   response.function_call_arguments.delta → delta.tool_calls[].function.arguments
 *   response.completed              → finish_reason + usage + [DONE]
 */
export function codexResponseToStream(
    upstreamBody: ReadableStream<Uint8Array>,
    model: string,
    wantsStream: boolean,
    errorContext: CodexErrorContext = {},
): ReadableStream<Uint8Array> {
    const encoder = new TextEncoder();
    const chatId = `chatcmpl_${crypto.randomUUID()}`;
    const created = Math.floor(Date.now() / 1000);

    // Track tool call indices — key is call_id OR item_id (both map to same index)
    const toolIndexByCallId = new Map<string, number>();
    // Map item_id → call_id for non-streaming argument accumulation
    const itemIdToCallId = new Map<string, string>();
    let nextToolIndex = 0;

    // For non-streaming: collect all text and tool calls, emit as single response
    let collectedText = '';
    const collectedToolCalls: Array<{
        id: string;
        type: string;
        function: { name: string; arguments: string };
    }> = [];
    let collectedUsage: {
        prompt_tokens?: number;
        completion_tokens?: number;
        total_tokens?: number;
        prompt_tokens_details?: { cached_tokens?: number };
    } = {};
    let finishReason = 'stop';
    const itemsWithTextDelta = new Set<string>();
    const itemsWithDoneText = new Set<string>();
    const toolCallsWithArgumentDelta = new Set<string>();
    let collectedReasoning = '';
    let terminalErrorPayload: Record<string, unknown> | null = null;
    const reasoningPartKeysWithDelta = new Set<string>();
    const reasoningPartKeysWithDone = new Set<string>();
    const reasoningItemsWithEvents = new Set<string>();
    const reasoningItemsWithPresence = new Set<string>();

    const sseIter = parseSSE(upstreamBody);

    function reasoningPartKey(
        itemId: string | undefined,
        partKind: 'content' | 'summary',
        partIndex: number | undefined,
    ): string | undefined {
        if (!itemId) return undefined;
        return `${itemId}:${partKind}:${String(partIndex ?? 0)}`;
    }

    function collectDoneText(
        itemId: string | undefined,
        text: string | undefined,
        controller: ReadableStreamDefaultController<Uint8Array>,
        encoder: InstanceType<typeof TextEncoder>,
        chatId: string,
        created: number,
        model: string,
        wantsStream: boolean,
    ): void {
        if (!text) return;
        if (itemId && (itemsWithTextDelta.has(itemId) || itemsWithDoneText.has(itemId))) {
            return;
        }

        if (itemId) itemsWithDoneText.add(itemId);

        if (wantsStream) {
            controller.enqueue(encoder.encode(
                `data: ${JSON.stringify({
                    id: chatId, object: 'chat.completion.chunk', created, model,
                    choices: [{ index: 0, delta: { content: text }, finish_reason: null }],
                })}\n\n`
            ));
            return;
        }

        collectedText += text;
    }

    function emitToolCallStart(
        controller: ReadableStreamDefaultController<Uint8Array>,
        encoder: InstanceType<typeof TextEncoder>,
        chatId: string,
        created: number,
        model: string,
        idx: number,
        callId: string,
        name: string,
    ): void {
        controller.enqueue(encoder.encode(
            `data: ${JSON.stringify({
                id: chatId, object: 'chat.completion.chunk', created, model,
                choices: [{
                    index: 0,
                    delta: {
                        tool_calls: [{
                            index: idx, id: callId, type: 'function',
                            function: { name, arguments: '' },
                        }],
                    },
                    finish_reason: null,
                }],
            })}\n\n`
        ));
    }

    function ensureToolCall(
        callId: string,
        itemId: string | undefined,
        name: string,
        controller: ReadableStreamDefaultController<Uint8Array>,
        encoder: InstanceType<typeof TextEncoder>,
        chatId: string,
        created: number,
        model: string,
        wantsStream: boolean,
    ): number {
        let idx = toolIndexByCallId.get(callId);
        if (idx === undefined && itemId) {
            idx = toolIndexByCallId.get(itemId);
        }

        if (idx === undefined) {
            idx = nextToolIndex++;
            if (wantsStream) {
                emitToolCallStart(controller, encoder, chatId, created, model, idx, callId, name);
            } else {
                collectedToolCalls.push({
                    id: callId,
                    type: 'function',
                    function: { name, arguments: '' },
                });
            }
        }

        toolIndexByCallId.set(callId, idx);
        if (itemId) {
            toolIndexByCallId.set(itemId, idx);
            itemIdToCallId.set(itemId, callId);
        }

        if (!wantsStream && !collectedToolCalls.some(t => t.id === callId)) {
            collectedToolCalls.push({
                id: callId,
                type: 'function',
                function: { name, arguments: '' },
            });
        }

        return idx;
    }

    function collectDoneToolArguments(
        callId: string,
        itemId: string | undefined,
        name: string,
        args: string | undefined,
        controller: ReadableStreamDefaultController<Uint8Array>,
        encoder: InstanceType<typeof TextEncoder>,
        chatId: string,
        created: number,
        model: string,
        wantsStream: boolean,
    ): void {
        const idx = ensureToolCall(callId, itemId, name, controller, encoder, chatId, created, model, wantsStream);
        if (!args) return;

        if (wantsStream) {
            const hasArgumentDelta = toolCallsWithArgumentDelta.has(callId)
                || (itemId ? toolCallsWithArgumentDelta.has(itemId) : false);
            if (!hasArgumentDelta) {
                controller.enqueue(encoder.encode(
                    `data: ${JSON.stringify({
                        id: chatId, object: 'chat.completion.chunk', created, model,
                        choices: [{
                            index: 0,
                            delta: {
                                tool_calls: [{
                                    index: idx,
                                    function: { arguments: args },
                                }],
                            },
                            finish_reason: null,
                        }],
                    })}\n\n`
                ));
            }
            return;
        }

        const tc = collectedToolCalls.find(t => t.id === callId);
        if (tc) tc.function.arguments = args;
    }

    function collectReasoning(
        text: string | undefined,
        controller: ReadableStreamDefaultController<Uint8Array>,
        encoder: InstanceType<typeof TextEncoder>,
        chatId: string,
        created: number,
        model: string,
        wantsStream: boolean,
    ): void {
        if (!text) return;

        if (wantsStream) {
            controller.enqueue(encoder.encode(
                `data: ${JSON.stringify({
                    id: chatId, object: 'chat.completion.chunk', created, model,
                    choices: [{ index: 0, delta: { reasoning_content: text }, finish_reason: null }],
                })}\n\n`
            ));
            return;
        }

        collectedReasoning += text;
    }

    function collectReasoningPresence(
        itemId: string | undefined,
        controller: ReadableStreamDefaultController<Uint8Array>,
        encoder: InstanceType<typeof TextEncoder>,
        chatId: string,
        created: number,
        model: string,
        wantsStream: boolean,
    ): void {
        const key = itemId ?? '__reasoning_without_id__';
        if (reasoningItemsWithPresence.has(key)) return;
        reasoningItemsWithPresence.add(key);
        collectReasoning(' ', controller, encoder, chatId, created, model, wantsStream);

        if (wantsStream) {
            controller.enqueue(encoder.encode(
                `data: ${JSON.stringify({
                    id: chatId, object: 'chat.completion.chunk', created, model,
                    choices: [{ index: 0, delta: { content: INLINE_REASONING_PLACEHOLDER }, finish_reason: null }],
                })}\n\n`
            ));
        } else if (collectedText.length === 0) {
            collectedText = INLINE_REASONING_PLACEHOLDER;
        }
    }

    function collectReasoningDelta(
        itemId: string | undefined,
        partKey: string | undefined,
        text: string | undefined,
        controller: ReadableStreamDefaultController<Uint8Array>,
        encoder: InstanceType<typeof TextEncoder>,
        chatId: string,
        created: number,
        model: string,
        wantsStream: boolean,
    ): void {
        if (partKey) reasoningPartKeysWithDelta.add(partKey);
        if (itemId) reasoningItemsWithEvents.add(itemId);
        collectReasoning(text, controller, encoder, chatId, created, model, wantsStream);
    }

    function collectDoneReasoning(
        itemId: string | undefined,
        partKey: string | undefined,
        text: string | undefined,
        controller: ReadableStreamDefaultController<Uint8Array>,
        encoder: InstanceType<typeof TextEncoder>,
        chatId: string,
        created: number,
        model: string,
        wantsStream: boolean,
    ): void {
        if (!text) return;
        if (partKey && (reasoningPartKeysWithDelta.has(partKey) || reasoningPartKeysWithDone.has(partKey))) {
            return;
        }

        if (partKey) reasoningPartKeysWithDone.add(partKey);
        if (itemId) reasoningItemsWithEvents.add(itemId);
        collectReasoning(text, controller, encoder, chatId, created, model, wantsStream);
    }

    function extractDoneItemText(item: Record<string, unknown> | undefined): string | undefined {
        const content = item?.['content'];
        if (!Array.isArray(content)) return undefined;

        const text = content
            .map(part => {
                if (typeof part !== 'object' || part === null) return '';
                return part['type'] === 'output_text' && typeof part['text'] === 'string'
                    ? part['text']
                    : '';
            })
            .join('');

        return text.length > 0 ? text : undefined;
    }

    function extractDoneItemReasoning(item: Record<string, unknown> | undefined): string | undefined {
        const summary = item?.['summary'];
        const content = item?.['content'];
        const parts: string[] = [];

        if (Array.isArray(summary)) {
            const summaryText = summary
                .map(part => {
                    if (typeof part !== 'object' || part === null) return '';
                    return part['type'] === 'summary_text' && typeof part['text'] === 'string'
                        ? part['text']
                        : '';
                })
                .join('');
            if (summaryText.length > 0) parts.push(summaryText);
        }

        if (Array.isArray(content)) {
            const reasoningText = content
                .map(part => {
                    if (typeof part !== 'object' || part === null) return '';
                    return part['type'] === 'reasoning_text' && typeof part['text'] === 'string'
                        ? part['text']
                        : '';
                })
                .join('');
            if (reasoningText.length > 0) parts.push(reasoningText);
        }

        return parts.length > 0 ? parts.join('\n\n') : undefined;
    }

    function extractDoneFunctionCall(
        item: Record<string, unknown> | undefined,
    ): { callId: string; itemId: string | undefined; name: string; arguments: string | undefined } | undefined {
        if (item?.['type'] !== 'function_call') return undefined;

        const itemId = typeof item['id'] === 'string' ? item['id'] : undefined;
        const callId = typeof item['call_id'] === 'string' ? item['call_id'] : itemId;
        const name = typeof item['name'] === 'string' ? item['name'] : undefined;
        const args = typeof item['arguments'] === 'string' ? item['arguments'] : undefined;

        if (!callId || !name) return undefined;

        return { callId, itemId, name, arguments: args };
    }

    function processOutputItem(
        item: Record<string, unknown> | undefined,
        controller: ReadableStreamDefaultController<Uint8Array>,
        encoder: InstanceType<typeof TextEncoder>,
        chatId: string,
        created: number,
        model: string,
        wantsStream: boolean,
    ): void {
        const itemType = item?.['type'] as string | undefined;
        if (itemType === 'message') {
            collectDoneText(
                item?.['id'] as string | undefined,
                extractDoneItemText(item),
                controller,
                encoder,
                chatId,
                created,
                model,
                wantsStream,
            );
        }
        if (itemType === 'reasoning' && !reasoningItemsWithEvents.has(item?.['id'] as string | undefined ?? '')) {
            const itemId = item?.['id'] as string | undefined;
            const reasoningText = extractDoneItemReasoning(item);
            if (reasoningText) {
                collectDoneReasoning(
                    itemId,
                    reasoningPartKey(itemId, 'summary', undefined),
                    reasoningText,
                    controller,
                    encoder,
                    chatId,
                    created,
                    model,
                    wantsStream,
                );
            } else {
                collectReasoningPresence(itemId, controller, encoder, chatId, created, model, wantsStream);
            }
        }
        if (itemType === 'function_call') {
            const doneToolCall = extractDoneFunctionCall(item);
            if (doneToolCall) {
                collectDoneToolArguments(
                    doneToolCall.callId,
                    doneToolCall.itemId,
                    doneToolCall.name,
                    doneToolCall.arguments,
                    controller,
                    encoder,
                    chatId,
                    created,
                    model,
                    wantsStream,
                );
            }
        }
    }

    return new ReadableStream<Uint8Array>({
        async start(controller) {
            try {
                if (wantsStream) {
                    // Emit initial role chunk
                    controller.enqueue(encoder.encode(
                        `data: ${JSON.stringify({
                            id: chatId, object: 'chat.completion.chunk', created, model,
                            choices: [{ index: 0, delta: { role: 'assistant' }, finish_reason: null }],
                        })}\n\n`
                    ));
                }

                for await (const sse of sseIter) {
                    if (!sse.data || !sse.event) continue;

                    let parsed: Record<string, unknown>;
                    try {
                        parsed = JSON.parse(sse.data);
                    } catch {
                        continue;
                    }

                    switch (sse.event) {
                        case 'response.output_text.delta': {
                            const delta = parsed['delta'] as string | undefined;
                            const itemId = parsed['item_id'] as string | undefined;
                            if (!delta) break;
                            if (itemId) itemsWithTextDelta.add(itemId);
                            if (wantsStream) {
                                controller.enqueue(encoder.encode(
                                    `data: ${JSON.stringify({
                                        id: chatId, object: 'chat.completion.chunk', created, model,
                                        choices: [{ index: 0, delta: { content: delta }, finish_reason: null }],
                                    })}\n\n`
                                ));
                            } else {
                                collectedText += delta;
                            }
                            break;
                        }

                        case 'response.output_text.done': {
                            collectDoneText(
                                parsed['item_id'] as string | undefined,
                                parsed['text'] as string | undefined,
                                controller,
                                encoder,
                                chatId,
                                created,
                                model,
                                wantsStream,
                            );
                            break;
                        }

                        case 'response.reasoning_text.delta': {
                            const itemId = parsed['item_id'] as string | undefined;
                            collectReasoningDelta(
                                itemId,
                                reasoningPartKey(itemId, 'content', parsed['content_index'] as number | undefined),
                                parsed['delta'] as string | undefined,
                                controller,
                                encoder,
                                chatId,
                                created,
                                model,
                                wantsStream,
                            );
                            break;
                        }

                        case 'response.reasoning_text.done': {
                            const itemId = parsed['item_id'] as string | undefined;
                            collectDoneReasoning(
                                itemId,
                                reasoningPartKey(itemId, 'content', parsed['content_index'] as number | undefined),
                                parsed['text'] as string | undefined,
                                controller,
                                encoder,
                                chatId,
                                created,
                                model,
                                wantsStream,
                            );
                            break;
                        }

                        case 'response.reasoning_summary_text.delta': {
                            const itemId = parsed['item_id'] as string | undefined;
                            collectReasoningDelta(
                                itemId,
                                reasoningPartKey(itemId, 'summary', parsed['summary_index'] as number | undefined),
                                parsed['delta'] as string | undefined,
                                controller,
                                encoder,
                                chatId,
                                created,
                                model,
                                wantsStream,
                            );
                            break;
                        }

                        case 'response.reasoning_summary_text.done': {
                            const itemId = parsed['item_id'] as string | undefined;
                            collectDoneReasoning(
                                itemId,
                                reasoningPartKey(itemId, 'summary', parsed['summary_index'] as number | undefined),
                                parsed['text'] as string | undefined,
                                controller,
                                encoder,
                                chatId,
                                created,
                                model,
                                wantsStream,
                            );
                            break;
                        }

                        case 'response.output_item.added': {
                            // Tool call start — data is nested under parsed['item']
                            const item = parsed['item'] as Record<string, unknown> | undefined;
                            const itemType = item?.['type'] as string | undefined;
                            if (itemType === 'function_call' && item) {
                                const callId = item['call_id'] as string;
                                const itemId = item['id'] as string | undefined;
                                const name = item['name'] as string;
                                ensureToolCall(callId, itemId, name, controller, encoder, chatId, created, model, wantsStream);
                            }
                            break;
                        }

                        case 'response.output_item.done': {
                            const item = parsed['item'] as Record<string, unknown> | undefined;
                            processOutputItem(item, controller, encoder, chatId, created, model, wantsStream);
                            break;
                        }

                        case 'response.function_call_arguments.delta': {
                            const delta = parsed['delta'] as string | undefined;
                            // The call identifier is 'item_id' in argument delta events
                            const itemId = parsed['item_id'] as string | undefined;
                            if (!delta || !itemId) break;
                            // Look up index by item_id; fall back to using it as call_id
                            const callId = itemId;
                            const resolvedCallId = itemIdToCallId.get(callId) ?? callId;
                            toolCallsWithArgumentDelta.add(resolvedCallId);
                            toolCallsWithArgumentDelta.add(itemId);

                            const idx = toolIndexByCallId.get(callId) ?? toolIndexByCallId.get(resolvedCallId);
                            if (wantsStream && idx !== undefined) {
                                controller.enqueue(encoder.encode(
                                    `data: ${JSON.stringify({
                                        id: chatId, object: 'chat.completion.chunk', created, model,
                                        choices: [{
                                            index: 0,
                                            delta: {
                                                tool_calls: [{
                                                    index: idx,
                                                    function: { arguments: delta },
                                                }],
                                            },
                                            finish_reason: null,
                                        }],
                                    })}\n\n`
                                ));
                            } else {
                                // Append to collected tool call arguments
                                // item_id → call_id lookup for non-streaming
                                const tc = collectedToolCalls.find(t => t.id === resolvedCallId);
                                if (tc) tc.function.arguments += delta;
                            }
                            break;
                        }

                        case 'response.function_call_arguments.done': {
                            const itemId = parsed['item_id'] as string | undefined;
                            const name = parsed['name'] as string | undefined;
                            const args = parsed['arguments'] as string | undefined;
                            if (!itemId || !name) break;

                            const callId = itemIdToCallId.get(itemId) ?? itemId;
                            collectDoneToolArguments(
                                callId,
                                itemId,
                                name,
                                args,
                                controller,
                                encoder,
                                chatId,
                                created,
                                model,
                                wantsStream,
                            );
                            break;
                        }

                        case 'response.completed':
                        case 'response.incomplete': {
                            const response = parsed['response'] as Record<string, unknown> | undefined;
                            const usage = response?.['usage'] as Record<string, unknown> | undefined;
                            const output = response?.['output'];
                            maybeLogCodexUsageDebug(sse.event, usage);

                            if (Array.isArray(output)) {
                                for (const item of output) {
                                    if (typeof item === 'object' && item !== null) {
                                        processOutputItem(item as Record<string, unknown>, controller, encoder, chatId, created, model, wantsStream);
                                    }
                                }
                            }

                            if (usage) {
                                const inputDetails = usage['input_tokens_details'] as Record<string, unknown> | undefined;
                                const cachedTokens = typeof inputDetails?.['cached_tokens'] === 'number'
                                    ? inputDetails['cached_tokens']
                                    : undefined;
                                collectedUsage = {
                                    prompt_tokens: usage['input_tokens'] as number | undefined,
                                    completion_tokens: usage['output_tokens'] as number | undefined,
                                    total_tokens: ((usage['input_tokens'] as number) ?? 0) + ((usage['output_tokens'] as number) ?? 0),
                                    ...(cachedTokens !== undefined
                                        ? { prompt_tokens_details: { cached_tokens: cachedTokens } }
                                        : {}),
                                };
                            }

                            finishReason = mapFinishReason(response?.['status'] as string | undefined);
                            // If tool calls were emitted, override to 'tool_calls'
                            if (nextToolIndex > 0) finishReason = 'tool_calls';

                            if (wantsStream) {
                                // Finish chunk
                                controller.enqueue(encoder.encode(
                                    `data: ${JSON.stringify({
                                        id: chatId, object: 'chat.completion.chunk', created, model,
                                        choices: [{ index: 0, delta: {}, finish_reason: finishReason }],
                                        ...(Object.keys(collectedUsage).length > 0 ? { usage: collectedUsage } : {}),
                                    })}\n\n`
                                ));
                                controller.enqueue(encoder.encode('data: [DONE]\n\n'));
                            }
                            break;
                        }

                        case 'error': {
                            const err = parsed['error'] ?? parsed; // allow both { error: ... } and flat
                            const errorPayload = buildCodexErrorPayload(
                                err,
                                'Codex upstream error',
                                'codex_error',
                                'upstream_error',
                                errorContext,
                            );
                            terminalErrorPayload = errorPayload;
                            applyCodexTerminalErrorSideEffects(errorPayload);
                            if (wantsStream) {
                                controller.enqueue(encoder.encode(
                                    `data: ${JSON.stringify({ error: errorPayload })}\n\n`
                                ));
                                controller.enqueue(encoder.encode('data: [DONE]\n\n'));
                                controller.close();
                                return;
                            }
                            break;
                        }

                        // Ignore other event types (response.created, response.in_progress,
                        // response.output_text.done, response.output_item.done, etc.)
                    }
                }

                // For non-streaming: emit the full chat completion response
                if (!wantsStream) {
                    if (terminalErrorPayload) {
                        controller.enqueue(encoder.encode(JSON.stringify({ error: terminalErrorPayload })));
                        controller.close();
                        return;
                    }

                    const message: Record<string, unknown> = {
                        role: 'assistant',
                        content: collectedText.length > 0 ? collectedText : null,
                    };
                    if (collectedReasoning.length > 0) {
                        message['reasoning_content'] = collectedReasoning;
                    }
                    if (collectedToolCalls.length > 0) {
                        message['tool_calls'] = collectedToolCalls;
                    }

                    const responseJson = JSON.stringify({
                        id: chatId,
                        object: 'chat.completion',
                        created,
                        model,
                        choices: [{
                            index: 0,
                            message,
                            finish_reason: collectedToolCalls.length > 0 ? 'tool_calls' : finishReason,
                        }],
                        usage: collectedUsage,
                    });

                    controller.enqueue(encoder.encode(responseJson));
                }

                controller.close();
            } catch (err) {
                controller.error(err);
            }
        },
    });
}

// ── Main Request Handler ───────────────────────────────────────────

/**
 * Parse the structured Codex error payload when the upstream body is JSON.
 */
function parseCodexError(errorBody: string): Record<string, unknown> | null {
    try {
        const parsed = JSON.parse(errorBody);
        if (typeof parsed !== 'object' || parsed === null) return null;
        const nestedError = (parsed as Record<string, unknown>)['error'] ?? parsed;
        return typeof nestedError === 'object' && nestedError !== null
            ? nestedError as Record<string, unknown>
            : null;
    } catch {
        return null;
    }
}

/**
 * Normalize upstream Codex errors into the existing ClawRoute envelope.
 */
function buildCodexErrorBody(status: number, errorBody: string, errorContext: CodexErrorContext = {}): string {
    const parsedError = parseCodexError(errorBody);
    const upstreamMessage = typeof parsedError?.['message'] === 'string'
        ? parsedError['message']
        : errorBody.trim();
    const message = upstreamMessage
        ? `Codex API error (${status}): ${upstreamMessage}`
        : `Codex API error (${status})`;
    const error = buildCodexErrorPayload(
        parsedError ?? undefined,
        message,
        `codex_${status}`,
        'upstream_error',
        errorContext,
    );
    error['message'] = formatCodexErrorMessage(
        message,
        asNonEmptyString(error['code']),
        typeof error['slot'] === 'number' ? error['slot'] : undefined,
    );
    if (typeof parsedError?.['resets_at'] === 'number') {
        error['resets_at'] = parsedError['resets_at'];
    }
    if (typeof parsedError?.['resets_in_seconds'] === 'number') {
        error['resets_in_seconds'] = parsedError['resets_in_seconds'];
    }
    return JSON.stringify({ error });
}

/**
 * Mark the current slot as rate-limited and extract resets_at from error body.
 */
function markSlotRateLimited(slotIndex: number, errorBody: string): void {
    const slot = authSlots[slotIndex];
    if (!slot) return;

    // Try to extract resets_at from the Codex error JSON
    let resetsAt = 0;
    const parsedError = parseCodexError(errorBody);
    if (typeof parsedError?.['resets_at'] === 'number') {
        resetsAt = epochToMillis(parsedError['resets_at']);
    } else if (typeof parsedError?.['resets_in_seconds'] === 'number') {
        resetsAt = Date.now() + parsedError['resets_in_seconds'] * 1000;
    }

    // Fallback: 15 minute cooldown if no resets_at found
    slot.rateLimitedUntil = resetsAt > 0 ? resetsAt : Date.now() + 15 * 60_000;
    const cooldownMin = Math.ceil((slot.rateLimitedUntil - Date.now()) / 60_000);
    console.log(
        `[codex-rotation] Slot ${slotIndex} (${slot.path}) marked rate-limited for ${cooldownMin}m`,
    );
}

function epochToMillis(value: number): number {
    if (!Number.isFinite(value) || value <= 0) return 0;
    return value > 10_000_000_000 ? value : value * 1000;
}

export function clearCodexSlotRateLimit(slotIndex: number, accountId?: string | null): void {
    const slot = authSlots[slotIndex];
    if (!slot || slot.rateLimitedUntil <= 0) return;
    if (accountId) {
        const slotAccountId = slot.auth?.accountId ?? getSlotAccountId(slot);
        if (slotAccountId && slotAccountId !== accountId) return;
    }
    slot.rateLimitedUntil = 0;
}

function getCodexUpstreamType(errorBody: string): string | null {
    const parsedError = parseCodexError(errorBody);
    if (typeof parsedError?.['upstream_type'] === 'string') return parsedError['upstream_type'];
    return typeof parsedError?.['type'] === 'string' ? parsedError['type'] : null;
}

function invalidateSlotAuth(slotIndex: number): void {
    const slot = authSlots[slotIndex];
    if (!slot) return;
    slot.auth = null;
    slot.authFileFingerprint = null;
    slot.lastLoadAttempt = 0;
}

function shouldRetryCodexError(status: number, errorBody: string): boolean {
    if (status === 401) return true;
    if (status === 500) return true;
    if (status === 502) {
        const parsedError = parseCodexError(errorBody);
        const code = asNonEmptyString(parsedError?.['code']);
        const type = asNonEmptyString(parsedError?.['type']);
        const message = asNonEmptyString(parsedError?.['message']) ?? '';
        return code === 'codex_error'
            || code === 'server_error'
            || type === 'server_error'
            || type === 'upstream_error'
            || /aborted|timeout|timed out|fetch failed/i.test(message);
    }
    return status === 429 && getCodexUpstreamType(errorBody) === 'usage_limit_reached';
}

function remainingPercent(usedPercent: number | null | undefined): number | null {
    return typeof usedPercent === 'number' && Number.isFinite(usedPercent)
        ? Math.max(0, 100 - usedPercent)
        : null;
}

function coldMigrationDecisionId(input: {
    sessionKey: string;
    previousAccountKey: string;
    targetAccountKey: string;
    targetSlotIndex: number;
}): string {
    return createHash('sha256')
        .update([
            'codex-cold-migration',
            input.sessionKey,
            input.previousAccountKey,
            input.targetAccountKey,
            String(input.targetSlotIndex),
        ].join('\0'))
        .digest('hex')
        .slice(0, 16);
}

function coldMigrationBlockedResponse(decisionId: string, estimatedFiveHourPercent: number): Response {
    return new Response(
        JSON.stringify({
            error: {
                message: `Codex cold prompt-cache migration blocked (${estimatedFiveHourPercent.toFixed(1)}% estimated 5h impact). Approve decision ${decisionId} in /dashboard-codex to continue this conversation.`,
                type: 'policy_blocked',
                code: 'codex_cold_migration_blocked',
                retryable: false,
                decision_id: decisionId,
            },
        }),
        {
            status: 403,
            headers: {
                'Content-Type': 'application/json',
                'X-ClawRoute-Policy-Block': 'codex_cold_migration',
                'X-ClawRoute-Retryable': 'false',
            },
        },
    );
}

function cacheMissBreakerBlockedResponse(block: NonNullable<ReturnType<typeof getCodexCacheBreakerBlock>>): Response {
    return new Response(
        JSON.stringify({
            error: {
                message: 'Codex prompt cache miss breaker blocked this request before spending more quota.',
                type: 'policy_block',
                code: 'codex_cache_miss_breaker_blocked',
                policy: CODEX_CACHE_BREAKER_POLICY,
                breaker_id: block.id,
                model: block.key.actualModel,
                slot_index: block.key.slotIndex,
                account_key: block.key.accountKey,
                prompt_cache_key_hash: block.key.promptCacheKeyHash,
                tool_schema_fingerprint: block.key.toolSchemaFingerprint,
                consecutive_misses: block.consecutiveMisses,
                recent: block.recent,
                recommendations: [
                    'Compact or restart the Hermes session to rebuild a stable prompt prefix.',
                    'Approve this breaker only if continuing on the same cache lease is intentional.',
                    'Switch model/session only after checking cache and quota impact.',
                ],
            },
        }),
        {
            status: 403,
            headers: {
                'Content-Type': 'application/json',
                'X-ClawRoute-Policy-Block': CODEX_CACHE_BREAKER_POLICY,
                'X-ClawRoute-Retryable': 'false',
                'X-ClawRoute-Breaker-Id': block.id,
                'X-ClawRoute-Breaker-Reason': block.blockReason ?? '',
                'X-ClawRoute-Cache-Key-Hash': block.key.promptCacheKeyHash,
                'X-ClawRoute-Tool-Schema-Fingerprint': block.key.toolSchemaFingerprint,
            },
        },
    );
}

function evaluateColdMigration(input: {
    request: Record<string, unknown>;
    affinityKey: string | null;
    selection: BalanceLoaderSelection;
}): { blockedResponse: Response | null; approvedDecisionId: string | null } {
    const previousAccountKey = input.selection.preferredAccountKey;
    const targetAccountKey = input.selection.selectedAccountKey;
    const targetSlotIndex = input.selection.selectedSlotIndex;
    if (!input.affinityKey || !previousAccountKey || !targetAccountKey || targetSlotIndex === null) {
        return { blockedResponse: null, approvedDecisionId: null };
    }
    if (previousAccountKey === targetAccountKey) return { blockedResponse: null, approvedDecisionId: null };

    const calibration = getLiveQuotaCalibration().fiveHour;
    const quotaPctPerMillion = calibration?.quotaPctPerMillionTotalTokens ?? 0;
    if (!Number.isFinite(quotaPctPerMillion) || quotaPctPerMillion <= 0) {
        return { blockedResponse: null, approvedDecisionId: null };
    }

    const estimatedInputTokens = estimateCodexRequestInputTokens(input.request);
    const estimatedFiveHourPercent = (estimatedInputTokens / 1_000_000) * quotaPctPerMillion;
    const threshold = getEffectiveCodexBalancerSettings().settings.coldMigrationFiveHourThresholdPercent;
    if (estimatedFiveHourPercent < threshold) return { blockedResponse: null, approvedDecisionId: null };

    const id = coldMigrationDecisionId({
        sessionKey: input.affinityKey,
        previousAccountKey,
        targetAccountKey,
        targetSlotIndex,
    });
    if (hasApprovedCodexColdMigrationDecision({
        id,
        sessionKey: input.affinityKey,
        targetAccountKey,
        targetSlotIndex,
    })) {
        return { blockedResponse: null, approvedDecisionId: id };
    }

    upsertCodexColdMigrationDecision({
        id,
        expiresAt: new Date(Date.now() + getColdMigrationDecisionTtlMs()).toISOString(),
        sessionKey: input.affinityKey,
        previousAccountKey,
        previousSlotIndex: input.selection.preferredSlotIndex ?? null,
        targetAccountKey,
        targetSlotIndex,
        estimatedInputTokens,
        estimatedFiveHourPercent,
        thresholdFiveHourPercent: threshold,
        previousFiveHourUsedPercent: input.selection.preferredFiveHourUsedPercent ?? null,
        previousFiveHourRemainingPercent: remainingPercent(input.selection.preferredFiveHourUsedPercent),
        targetFiveHourUsedPercent: input.selection.fiveHourUsedPercent ?? null,
        targetFiveHourRemainingPercent: remainingPercent(input.selection.fiveHourUsedPercent),
        targetWeeklyUsedPercent: input.selection.weeklyUsedPercent ?? null,
        targetWeeklyRemainingPercent: remainingPercent(input.selection.weeklyUsedPercent),
    });
    return { blockedResponse: coldMigrationBlockedResponse(id, estimatedFiveHourPercent), approvedDecisionId: null };
}

function getEarliestRateLimitInfo(): { resetsAt: number; resetsInSeconds: number } | null {
    const now = Date.now();
    const futureResets = authSlots.map(slot => slot.rateLimitedUntil).filter(resetAt => resetAt > now);
    if (futureResets.length === 0) return null;
    const earliestReset = Math.min(...futureResets);
    return {
        resetsAt: Math.ceil(earliestReset / 1000),
        resetsInSeconds: Math.ceil((earliestReset - now) / 1000),
    };
}

async function readStreamToString(stream: ReadableStream<Uint8Array>): Promise<string> {
    const reader = stream.getReader();
    const chunks: Uint8Array[] = [];
    let totalLength = 0;

    while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        if (!value) continue;
        chunks.push(value);
        totalLength += value.length;
    }

    return Buffer.concat(chunks, totalLength).toString('utf-8');
}

/**
 * Execute a single Codex request with the given auth.
 * Returns the Response (success or error).
 */
async function executeCodexCall(
    auth: CodexAuth,
    body: Record<string, unknown>,
    modelName: string,
    wantsStream: boolean,
    proxyAgent: ProxyAgent | null,
    errorContext: CodexErrorContext = {},
): Promise<Response> {
    const url = `${CODEX_BASE_URL}/responses`;
    const headers: Record<string, string> = {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${auth.accessToken}`,
        'OpenAI-Beta': 'responses=experimental',
    };
    if (auth.accountId) headers['chatgpt-account-id'] = auth.accountId;
    const fetchOptions: FetchInitWithDispatcher = {
        method: 'POST',
        headers,
        body: JSON.stringify(body),
    };
    if (proxyAgent) fetchOptions.dispatcher = proxyAgent;

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), getCodexRequestTimeoutMs());
    fetchOptions.signal = controller.signal;

    try {
        const upstream = proxyAgent
            ? await fetchWithProxyAgent(url, fetchOptions)
            : await fetch(url, fetchOptions as RequestInit);
        clearTimeout(timeoutId);

        if (!upstream.ok) {
            const errorBody = await upstream.text();
            return new Response(
                buildCodexErrorBody(upstream.status, errorBody, errorContext),
                { status: upstream.status, headers: { 'Content-Type': 'application/json' } },
            );
        }

        if (!upstream.body) {
            return new Response(
                JSON.stringify({ error: { message: 'No response body from Codex', type: 'server_error' } }),
                { status: 502, headers: { 'Content-Type': 'application/json' } },
            );
        }

        const transformedBody = codexResponseToStream(upstream.body, modelName, wantsStream, errorContext);

        if (wantsStream) {
            return new Response(transformedBody, {
                status: 200,
                headers: {
                    'Content-Type': 'text/event-stream',
                    'Cache-Control': 'no-cache',
                    'Connection': 'keep-alive',
                },
            });
        } else {
            const jsonBody = await readStreamToString(transformedBody);

            return new Response(jsonBody, {
                status: getCodexResponseStatus(jsonBody),
                headers: { 'Content-Type': 'application/json' },
            });
        }
    } catch (err) {
        clearTimeout(timeoutId);
        const message = err instanceof Error ? err.message : 'Codex request failed';
        return new Response(
            JSON.stringify({
                error: buildCodexErrorPayload(
                    message,
                    'Codex request failed',
                    'codex_error',
                    'server_error',
                    errorContext,
                ),
            }),
            { status: 502, headers: { 'Content-Type': 'application/json' } },
        );
    }
}

/**
 * Execute a request through the Codex ChatGPT subscription endpoint.
 *
 * This replaces the normal makeProviderRequest flow for codex/ models.
 * Returns a Response that looks like a standard OpenAI Chat Completions
 * response (either streaming SSE or JSON), so the rest of ClawRoute's
 * executor pipeline (pipeStream, usage tracking) works unchanged.
 *
 * On retryable auth/server/rate-limit errors, advances to the next auth slot and retries
 * before returning the error to the caller.
 */
export async function makeCodexRequest(
    request: Record<string, unknown>,
    modelId: string,
    proxyAgent: ProxyAgent | null,
    executionContext: RequestExecutionContext = { sessionId: null },
): Promise<Response> {
    // 1. Extract model name (strip codex/ prefix)
    const modelName = modelId.includes('/') ? modelId.split('/').slice(1).join('/') : modelId;
    const sessionId = executionContext.sessionId ?? extractSessionIdFromRequest(request);
    const promptCacheKey = resolvePromptCacheKey(request, executionContext);
    const cacheBreakerPromptCacheKey = resolveCacheBreakerPromptCacheKey(request, executionContext);
    const affinityKey = promptCacheKey ?? sessionId;
    const balanceLoaderMode = getBalanceLoaderMode();

    // 2. Build the Responses API request body
    const body = buildCodexRequestBody(request, modelName, promptCacheKey);
    const wantsStream = request['stream'] === true;

    // 3. Try each available slot (current first, then rotate on 429)
    let lastErrorResponse: Response | null = null;
    const triedSlotIndexes = new Set<number>();
    const excludedAccountKeys = new Set<string>();
    let preferredSlotIndex: number | undefined;
    let preferredAccountKey: string | null = null;
    let approvedColdMigrationDecisionId: string | null = null;
    const configuredSlotCount = process.env['OPENAI_CODEX_TOKEN']
        ? 1
        : (authSlots.length > 0 ? authSlots.length : resolveAuthPaths().length);
    const slotCount = Math.max(configuredSlotCount, 1); // at least 1 attempt

    for (let attempt = 0; attempt < slotCount; attempt++) {
        preferredSlotIndex = undefined;
        preferredAccountKey = null;
        approvedColdMigrationDecisionId = null;
        const disabledSlotIndexes = balanceLoaderMode === 'off'
            ? new Set<number>()
            : getDisabledCodexSlotIndexes();
        const unavailableSlotIndexes = new Set([
            ...triedSlotIndexes,
            ...disabledSlotIndexes,
        ]);

        if (balanceLoaderMode !== 'off' && !process.env['OPENAI_CODEX_TOKEN']) {
            try {
                ensureCodexSlots();
                const now = Date.now();
                let selection = await getBalanceLoaderSelection({
                    now,
                    sessionId: affinityKey,
                    excludedSlotIndexes: unavailableSlotIndexes,
                    excludedAccountKeys,
                });
                if (balanceLoaderMode === 'on') {
                    selection = applyCacheLease(selection, now, affinityKey);
                }
                if (balanceLoaderMode === 'on') {
                    const coldMigration = evaluateColdMigration({ request, affinityKey, selection });
                    if (coldMigration.blockedResponse) return coldMigration.blockedResponse;
                    approvedColdMigrationDecisionId = coldMigration.approvedDecisionId;
                }
                preferredAccountKey = selection.selectedAccountKey;
                if (balanceLoaderMode === 'shadow') {
                    logCodexScheduleShadowDecision(selection, getLegacyFirstEligibleSlot({
                        now,
                        excludedSlotIndexes: unavailableSlotIndexes,
                        excludedAccountKeys,
                    }));
                }
                if (balanceLoaderMode === 'on' && selection.fallbackReason === null && selection.selectedSlotIndex !== null) {
                    preferredSlotIndex = selection.selectedSlotIndex;
                }
                setLastCodexBalancerDecision({
                    timestamp: new Date(now).toISOString(),
                    slotIndex: selection.selectedSlotIndex,
                    accountKey: selection.selectedAccountKey,
                    mode: balanceLoaderMode,
                    reason: selection.decisionReason ?? null,
                    fallbackReason: selection.fallbackReason,
                    weeklyUsedPercent: selection.weeklyUsedPercent ?? null,
                    weeklyResetAt: selection.weeklyResetAt ?? null,
                    requiredBurnRate: selection.requiredBurnRate ?? null,
                });
            } catch {
                // Legacy rotation remains the compatibility fallback.
            }
        }

        const result = await getActiveCodexAuth(proxyAgent, unavailableSlotIndexes, preferredSlotIndex, excludedAccountKeys);
        if (!result.ok) {
            if (result.reason === 'all_rate_limited') {
                const resetInfo = getEarliestRateLimitInfo();
                return lastErrorResponse ?? new Response(
                    JSON.stringify({
                        error: {
                            message: 'All Codex auth slots are currently rate-limited. Try again later.',
                            type: 'upstream_error',
                            code: 'codex_429',
                            ...(resetInfo ? {
                                resets_at: resetInfo.resetsAt,
                                resets_in_seconds: resetInfo.resetsInSeconds,
                            } : {}),
                        },
                    }),
                    { status: 429, headers: { 'Content-Type': 'application/json' } },
                );
            }
            return lastErrorResponse ?? new Response(
                JSON.stringify({
                    error: {
                        message: 'Codex OAuth credentials not found. Run `codex login` or set OPENAI_CODEX_AUTH_PATH.',
                        type: 'auth_error',
                        code: 'codex_auth_missing',
                    },
                }),
                { status: 401, headers: { 'Content-Type': 'application/json' } },
            );
        }

        const slotUsed = currentSlotIndex;
        const accountKeyUsed = result.auth.accountId
            ? hashAccountKey(result.auth.accountId)
            : preferredAccountKey;
        executionContext.codexSelection = {
            slotIndex: slotUsed,
            accountKey: accountKeyUsed,
        };
        const promptCacheKeyHash = hashPromptCacheKey(cacheBreakerPromptCacheKey);
        if (accountKeyUsed && promptCacheKeyHash) {
            const block = getCodexCacheBreakerBlock({
                promptCacheKeyHash,
                actualModel: modelId,
                accountKey: accountKeyUsed,
                slotIndex: slotUsed,
                toolSchemaFingerprint: buildToolSchemaFingerprint(request['tools'] ?? []),
            });
            if (block) {
                return cacheMissBreakerBlockedResponse(block);
            }
        }
        const lease = claimSelectionLease(accountKeyUsed, slotUsed);
        const response = await executeCodexCall(
            result.auth,
            body,
            modelName,
            wantsStream,
            proxyAgent,
            {
                slot: slotUsed,
                path: result.auth.sourcePath ?? authSlots[slotUsed]?.path,
            },
        );
        releaseCodexAuth();

        // Success or non-retriable error → return immediately
        if (response.status !== 401 && response.status !== 429 && response.status !== 500 && response.status !== 502) {
            const cacheLease = affinityKey ? activeCacheLeases.get(getLeaseMapKey(affinityKey)) : null;
            if (response.ok && cacheLease?.slotIndex === slotUsed) {
                cacheLease.lastUsedAt = Date.now();
            }
            if (response.ok && approvedColdMigrationDecisionId) {
                consumeCodexColdMigrationDecision(approvedColdMigrationDecisionId);
            }
            releaseSelectionLease(lease, affinityKey, response.ok);
            return response;
        }

        // Retryable auth/rate-limit/server errors → advance to the next slot and retry
        const errBody = await response.text();
        invalidateCodexCacheLeaseForSlot(slotUsed);
        releaseSelectionLease(lease, affinityKey, false);
        if (!shouldRetryCodexError(response.status, errBody)) {
            return new Response(
                errBody,
                {
                    status: response.status,
                    headers: {
                        'Content-Type': response.headers.get('Content-Type') ?? 'application/json',
                    },
                },
            );
        }
        triedSlotIndexes.add(slotUsed);
        if (response.status === 401) {
            invalidateSlotAuth(slotUsed);
            console.warn(
                `[codex-rotation] Slot ${slotUsed} returned 401 auth error, trying next slot...`
                + (attempt + 1 < slotCount ? ` (attempt ${attempt + 1}/${slotCount})` : ' (no more slots)'),
            );
        } else if (response.status === 429) {
            console.warn(
                `[codex-rotation] Slot ${slotUsed} returned 429, rotating...`
                + (attempt + 1 < slotCount ? ` (attempt ${attempt + 1}/${slotCount})` : ' (no more slots)'),
            );
            markSlotRateLimited(slotUsed, errBody);
            if (accountKeyUsed) excludedAccountKeys.add(accountKeyUsed);
        } else {
            // Transient server error, may be account-specific; try next slot without rate-limit penalty.
            console.warn(
                `[codex-rotation] Slot ${slotUsed} returned ${response.status}, trying next slot...`
                + (attempt + 1 < slotCount ? ` (attempt ${attempt + 1}/${slotCount})` : ' (no more slots)'),
            );
            advanceSlot();
        }

        // Preserve last error response to return if all slots exhausted
        lastErrorResponse = new Response(
            errBody,
            {
                status: response.status,
                headers: {
                    'Content-Type': response.headers.get('Content-Type') ?? 'application/json',
                },
            },
        );
    }

    // All slots exhausted
    return lastErrorResponse ?? new Response(
        JSON.stringify({
            error: {
                message: 'All Codex auth slots failed (rate-limited or server error)',
                type: 'upstream_error',
                code: 'codex_all_failed',
            },
        }),
        { status: 502, headers: { 'Content-Type': 'application/json' } },
    );
}

export async function makeCodexImageRequest(
    request: ImageGenerationRequest | ImageEditRequest,
    proxyAgent: ProxyAgent | null,
): Promise<Response> {
    let lastErrorResponse: Response | null = null;
    const triedSlotIndexes = new Set<number>();
    const disabledSlotIndexes = getBalanceLoaderMode() === 'off'
        ? new Set<number>()
        : getDisabledCodexSlotIndexes();
    const slotCount = Math.max(
        process.env['OPENAI_CODEX_TOKEN'] ? 1 : (authSlots.length > 0 ? authSlots.length : resolveAuthPaths().length),
        1,
    );

    for (let attempt = 0; attempt < slotCount; attempt++) {
        const result = await getActiveCodexAuth(
            proxyAgent,
            new Set([...triedSlotIndexes, ...disabledSlotIndexes]),
        );
        if (!result.ok) {
            if (result.reason === 'all_rate_limited') {
                const resetInfo = getEarliestRateLimitInfo();
                return lastErrorResponse ?? new Response(
                    JSON.stringify({
                        error: {
                            message: 'All Codex auth slots are currently rate-limited. Try again later.',
                            type: 'upstream_error',
                            code: 'codex_429',
                            ...(resetInfo ? {
                                resets_at: resetInfo.resetsAt,
                                resets_in_seconds: resetInfo.resetsInSeconds,
                            } : {}),
                        },
                    }),
                    { status: 429, headers: { 'Content-Type': 'application/json' } },
                );
            }

            return lastErrorResponse ?? new Response(
                JSON.stringify({
                    error: {
                        message: 'Codex OAuth credentials not found. Run `codex login` or set OPENAI_CODEX_AUTH_PATH.',
                        type: 'auth_error',
                        code: 'codex_auth_missing',
                    },
                }),
                { status: 401, headers: { 'Content-Type': 'application/json' } },
            );
        }

        const slotUsed = currentSlotIndex;
        const response = await executeCodexImageCall(
            result.auth,
            request,
            proxyAgent,
            {
                slot: slotUsed,
                path: result.auth.sourcePath ?? authSlots[slotUsed]?.path,
            },
        );
        releaseCodexAuth();

        if (response.status !== 401 && response.status !== 429 && response.status !== 500 && response.status !== 502) {
            return response;
        }

        const errBody = await response.text();
        if (!shouldRetryCodexError(response.status, errBody)) {
            return new Response(errBody, {
                status: response.status,
                headers: {
                    'Content-Type': response.headers.get('Content-Type') ?? 'application/json',
                },
            });
        }

        triedSlotIndexes.add(slotUsed);
        if (response.status === 401) {
            invalidateSlotAuth(slotUsed);
        } else if (response.status === 429) {
            markSlotRateLimited(slotUsed, errBody);
        } else {
            advanceSlot();
        }

        lastErrorResponse = new Response(errBody, {
            status: response.status,
            headers: {
                'Content-Type': response.headers.get('Content-Type') ?? 'application/json',
            },
        });
    }

    return lastErrorResponse ?? new Response(
        JSON.stringify({
            error: {
                message: 'All Codex auth slots failed for image generation',
                type: 'upstream_error',
                code: 'codex_image_exhausted',
            },
        }),
        { status: 502, headers: { 'Content-Type': 'application/json' } },
    );
}
