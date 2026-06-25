import { createHash, randomUUID } from 'node:crypto';

export const CODEX_CACHE_BREAKER_POLICY = 'codex_cache_miss_breaker';

type CacheBreakerKey = {
    promptCacheKeyHash: string;
    actualModel: string;
    accountKey: string;
    slotIndex: number;
    toolSchemaFingerprint: string;
};

type CacheBreakerOutcome = {
    timestamp: string;
    requestId: string | null;
    turnId: string | null;
    inputTokens: number;
    cachedInputTokens: number;
    outputTokens: number;
    cachedRatio: number;
    expectedHit: boolean;
    lowCache: boolean;
    phase: string | null;
    comparison: string | null;
};

type CacheBreakerRecord = {
    id: string;
    key: CacheBreakerKey;
    startedAt: string;
    updatedAt: string;
    blockedAt: string | null;
    approvalExpiresAt: string | null;
    consecutiveMisses: number;
    outcomes: CacheBreakerOutcome[];
};

export type CodexCacheBreakerOutcomeInput = CacheBreakerKey & {
    requestId?: string | null;
    turnId?: string | null;
    inputTokens: number;
    cachedInputTokens: number;
    outputTokens: number;
    phase?: string | null;
    comparison?: string | null;
};

export type CodexCacheBreakerBlock = {
    id: string;
    policy: typeof CODEX_CACHE_BREAKER_POLICY;
    key: CacheBreakerKey;
    blockedAt: string;
    approvalExpiresAt: string | null;
    consecutiveMisses: number;
    recent: CacheBreakerOutcome[];
    settings: CodexCacheBreakerSettings;
};

export type CodexCacheBreakerSettings = {
    enabled: boolean;
    minInputTokens: number;
    lowCacheRatio: number;
    consecutiveMisses: number;
    windowMisses: number;
    windowRequests: number;
    approvalTtlMinutes: number;
    maxApprovalTtlMinutes: number;
};

const records = new Map<string, CacheBreakerRecord>();
const maxApprovalTtlMinutes = 120;

function nowIso(): string {
    return new Date().toISOString();
}

function parseBool(value: string | undefined, fallback: boolean): boolean {
    if (value === undefined) return fallback;
    const normalized = value.trim().toLowerCase();
    if (['1', 'true', 'yes', 'on'].includes(normalized)) return true;
    if (['0', 'false', 'no', 'off'].includes(normalized)) return false;
    return fallback;
}

function parseNumber(value: string | undefined, fallback: number, min: number, max: number): number {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return fallback;
    return Math.min(max, Math.max(min, parsed));
}

function parseInteger(value: string | undefined, fallback: number, min: number, max: number): number {
    return Math.round(parseNumber(value, fallback, min, max));
}

export function getCodexCacheBreakerSettings(): CodexCacheBreakerSettings {
    return {
        enabled: parseBool(process.env['CODEX_CACHE_BREAKER_ENABLED'], true),
        minInputTokens: parseInteger(process.env['CODEX_CACHE_BREAKER_MIN_INPUT_TOKENS'], 20_000, 1, 10_000_000),
        lowCacheRatio: parseNumber(process.env['CODEX_CACHE_BREAKER_LOW_CACHE_RATIO'], 0.20, 0, 1),
        consecutiveMisses: parseInteger(process.env['CODEX_CACHE_BREAKER_CONSECUTIVE_MISSES'], 2, 1, 100),
        windowMisses: parseInteger(process.env['CODEX_CACHE_BREAKER_WINDOW_MISSES'], 3, 1, 100),
        windowRequests: parseInteger(process.env['CODEX_CACHE_BREAKER_WINDOW_REQUESTS'], 5, 1, 100),
        approvalTtlMinutes: parseInteger(process.env['CODEX_CACHE_BREAKER_APPROVAL_TTL_MINUTES'], 15, 1, maxApprovalTtlMinutes),
        maxApprovalTtlMinutes,
    };
}

export function hashPromptCacheKey(promptCacheKey: string | null | undefined): string | null {
    const normalized = promptCacheKey?.trim();
    if (!normalized) return null;
    return createHash('sha256').update(normalized).digest('hex').slice(0, 12);
}

function keyId(key: CacheBreakerKey): string {
    return [
        key.promptCacheKeyHash,
        key.actualModel,
        key.accountKey,
        String(key.slotIndex),
        key.toolSchemaFingerprint,
    ].join('\0');
}

function serializeRecord(record: CacheBreakerRecord): CodexCacheBreakerBlock {
    return {
        id: record.id,
        policy: CODEX_CACHE_BREAKER_POLICY,
        key: record.key,
        blockedAt: record.blockedAt ?? record.updatedAt,
        approvalExpiresAt: record.approvalExpiresAt,
        consecutiveMisses: record.consecutiveMisses,
        recent: record.outcomes.slice(-getCodexCacheBreakerSettings().windowRequests),
        settings: getCodexCacheBreakerSettings(),
    };
}

function isApprovalActive(record: CacheBreakerRecord, nowMs: number): boolean {
    if (!record.approvalExpiresAt) return false;
    return Date.parse(record.approvalExpiresAt) > nowMs;
}

function shouldEvaluate(input: CodexCacheBreakerOutcomeInput, settings: CodexCacheBreakerSettings): boolean {
    if (!settings.enabled) return false;
    if (input.inputTokens < settings.minInputTokens) return false;
    return input.comparison === 'prefix' || input.comparison === 'retry';
}

function isLowCache(input: CodexCacheBreakerOutcomeInput, settings: CodexCacheBreakerSettings): boolean {
    const ratio = input.inputTokens > 0 ? input.cachedInputTokens / input.inputTokens : 0;
    return ratio < settings.lowCacheRatio;
}

export function recordCodexCacheBreakerOutcome(input: CodexCacheBreakerOutcomeInput): CodexCacheBreakerBlock | null {
    const settings = getCodexCacheBreakerSettings();
    if (!settings.enabled) return null;

    const id = keyId(input);
    const timestamp = nowIso();
    const record = records.get(id) ?? {
        id: randomUUID(),
        key: {
            promptCacheKeyHash: input.promptCacheKeyHash,
            actualModel: input.actualModel,
            accountKey: input.accountKey,
            slotIndex: input.slotIndex,
            toolSchemaFingerprint: input.toolSchemaFingerprint,
        },
        startedAt: timestamp,
        updatedAt: timestamp,
        blockedAt: null,
        approvalExpiresAt: null,
        consecutiveMisses: 0,
        outcomes: [],
    };

    const expectedHit = shouldEvaluate(input, settings);
    const lowCache = expectedHit && isLowCache(input, settings);
    const cachedRatio = input.inputTokens > 0 ? input.cachedInputTokens / input.inputTokens : 0;
    record.outcomes.push({
        timestamp,
        requestId: input.requestId ?? null,
        turnId: input.turnId ?? null,
        inputTokens: input.inputTokens,
        cachedInputTokens: input.cachedInputTokens,
        outputTokens: input.outputTokens,
        cachedRatio,
        expectedHit,
        lowCache,
        phase: input.phase ?? null,
        comparison: input.comparison ?? null,
    });
    record.outcomes = record.outcomes.slice(-Math.max(settings.windowRequests, settings.consecutiveMisses, 1));
    record.consecutiveMisses = lowCache ? record.consecutiveMisses + 1 : 0;
    record.updatedAt = timestamp;

    const recent = record.outcomes.slice(-settings.windowRequests);
    const recentMisses = recent.filter((outcome) => outcome.lowCache).length;
    if (lowCache
        && (record.consecutiveMisses >= settings.consecutiveMisses || recentMisses >= settings.windowMisses)) {
        record.blockedAt ??= timestamp;
    }

    records.set(id, record);
    return record.blockedAt ? serializeRecord(record) : null;
}

export function getCodexCacheBreakerBlock(key: CacheBreakerKey): CodexCacheBreakerBlock | null {
    const settings = getCodexCacheBreakerSettings();
    if (!settings.enabled) return null;
    const record = records.get(keyId(key));
    if (!record?.blockedAt) return null;
    if (isApprovalActive(record, Date.now())) return null;
    return serializeRecord(record);
}

export function getCodexCacheBreakerSnapshot() {
    const nowMs = Date.now();
    return {
        settings: getCodexCacheBreakerSettings(),
        breakers: [...records.values()].map((record) => ({
            ...serializeRecord(record),
            startedAt: record.startedAt,
            updatedAt: record.updatedAt,
            status: record.blockedAt
                ? isApprovalActive(record, nowMs) ? 'approved' : 'blocked'
                : 'watching',
        })),
    };
}

export function approveCodexCacheBreaker(id: string, ttlMinutes?: number): CodexCacheBreakerBlock | null {
    const settings = getCodexCacheBreakerSettings();
    const ttl = ttlMinutes === undefined
        ? settings.approvalTtlMinutes
        : parseNumber(String(ttlMinutes), settings.approvalTtlMinutes, 1, settings.maxApprovalTtlMinutes);
    for (const record of records.values()) {
        if (record.id !== id) continue;
        record.approvalExpiresAt = new Date(Date.now() + ttl * 60_000).toISOString();
        record.updatedAt = nowIso();
        return serializeRecord(record);
    }
    return null;
}

export function clearCodexCacheBreaker(id: string): CodexCacheBreakerBlock | null {
    for (const [key, record] of records.entries()) {
        if (record.id !== id) continue;
        records.delete(key);
        return serializeRecord(record);
    }
    return null;
}

export function resetCodexCacheBreakerState(): void {
    records.clear();
}
