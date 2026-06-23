import { createHash } from 'node:crypto';
import {
    clearCodexSlotRateLimit,
    CodexAuth,
    CodexAuthSlotSnapshot,
    getCodexAuthSlots,
    loadCodexUsageAuthSlot,
} from './codex-transport.js';
import { getProxyAgent } from './http-proxy.js';
import {
    getCodexResetCreditSnapshots,
    getCodexUsageSnapshots,
    setCodexActivationCheckpoint,
    upsertCodexResetCreditSnapshots,
    upsertCodexUsageSnapshots,
} from './logger.js';
import {
    CodexActivationCheckpoint,
    CodexResetCreditItem,
    CodexResetCreditsSnapshot,
    CodexUsageAccountRow,
    CodexUsageRawResponse,
    CodexUsageSelectorRequest,
    CodexUsageSelectorSnapshot,
    CodexUsageSnapshotRecord,
    CodexUsageSlotError,
    CodexUsageWindowSnapshot,
} from './types.js';

const USAGE_URL = 'https://chatgpt.com/backend-api/wham/usage';
const RESET_CREDITS_URL = 'https://chatgpt.com/backend-api/wham/rate-limit-reset-credits';
const FIVE_HOURS = 18_000;
const SEVEN_DAYS = 604_800;
const DEFAULT_TIMEOUT_MS = 10_000;
const DEFAULT_CACHE_TTL_MS = 15_000;
const DEFAULT_SELECTOR_PERSISTED_MAX_AGE_MS = 15 * 60_000;
const DEFAULT_SELECTOR_REFRESH_THROTTLE_MS = 60_000;

type ServiceSlot = {
    slotIndex: number;
    path?: string | null;
    accountId: string | null;
    rateLimitedUntil?: number;
    auth?: CodexAuth | null;
    authAvailable?: boolean;
    authUnavailableReason?: CodexAuthSlotSnapshot['authUnavailableReason'];
    authRetryAt?: string | null;
};
type CooldownState = { slotIndex: number; accountId: string; cooldownUntil: string };
type SnapshotResult = {
    status: number;
    partial: boolean;
    accounts: CodexUsageAccountRow[];
    slotErrors: CodexUsageSlotError[];
    resetCreditErrors: CodexUsageSlotError[];
    error?: { message: string };
};
type ServiceDeps = {
    now?: () => number;
    cacheTtlMs?: number;
    timeoutMs?: number;
    listSlots?: () => Promise<ServiceSlot[]> | ServiceSlot[];
    fetchUsage?: (slot: ServiceSlot) => Promise<CodexUsageRawResponse>;
    fetchResetCredits?: (slot: ServiceSlot) => Promise<unknown>;
    readLatestSnapshots?: () => Promise<CodexUsageSnapshotRecord[]> | CodexUsageSnapshotRecord[];
    readLatestResetCredits?: () => Promise<CodexResetCreditsSnapshot[]> | CodexResetCreditsSnapshot[];
    writeSnapshots?: (records: CodexUsageSnapshotRecord[]) => Promise<void> | void;
    writeResetCredits?: (records: CodexResetCreditsSnapshot[]) => Promise<void> | void;
    writeCheckpoint?: (checkpoint: CodexActivationCheckpoint) => Promise<void> | void;
    getCooldownState?: () => Promise<CooldownState[]> | CooldownState[];
    clearSlotCooldown?: (slotIndex: number, accountId?: string | null) => void;
};
type UsageSnapshotOptions = {
    slotIndexes?: readonly number[];
    force?: boolean;
};
type CachedAccount = { expiresAt: number; account: CodexUsageAccountRow };
type SlotBinding = { slotIndex: number; slotPath?: string | null };

function cleanSlotPaths(paths: Array<string | null | undefined>): string[] {
    return Array.from(new Set(paths.filter((path): path is string => Boolean(path && path.trim()))));
}

function getUsageWindows(payload: CodexUsageRawResponse): [CodexUsageRawResponse['primary_window'], CodexUsageRawResponse['secondary_window']] {
    return [
        payload.primary_window ?? payload.rate_limit?.primary_window,
        payload.secondary_window ?? payload.rate_limit?.secondary_window,
    ];
}

function hashAccountKey(accountId: string): string {
    return createHash('sha256').update(accountId).digest('hex').slice(0, 16);
}

function hashCreditKey(value: string): string {
    return createHash('sha256').update(value).digest('hex').slice(0, 16);
}

function objectValue(value: unknown): Record<string, unknown> | null {
    return value && typeof value === 'object' && !Array.isArray(value)
        ? value as Record<string, unknown>
        : null;
}

function firstValue(record: Record<string, unknown>, keys: string[]): unknown {
    for (const key of keys) {
        if (record[key] !== undefined && record[key] !== null) return record[key];
    }
    return undefined;
}

function stringOrNull(value: unknown): string | null {
    return typeof value === 'string' && value.trim() ? value.trim() : null;
}

function countOrNull(value: unknown): number | null {
    const numeric = Number(value);
    return Number.isInteger(numeric) && numeric >= 0 ? numeric : null;
}

function isoDateOrNull(value: unknown): string | null {
    if (typeof value === 'number' && Number.isFinite(value)) {
        const millis = value > 10_000_000_000 ? value : value * 1000;
        const date = new Date(millis);
        return Number.isNaN(date.getTime()) ? null : date.toISOString();
    }
    if (typeof value === 'string' && value.trim()) {
        const date = new Date(value);
        return Number.isNaN(date.getTime()) ? null : date.toISOString();
    }
    return null;
}

function getResetCreditArray(payload: unknown): unknown[] {
    const record = objectValue(payload);
    if (!record) return [];
    for (const key of ['rate_limit_reset_credits', 'reset_credits', 'credits', 'grants', 'items', 'data']) {
        const value = record[key];
        if (Array.isArray(value)) return value;
        const nested = objectValue(value);
        if (nested) {
            for (const nestedKey of ['credits', 'items', 'data']) {
                if (Array.isArray(nested[nestedKey])) return nested[nestedKey];
            }
        }
    }
    return [];
}

function extractResetCreditAvailableCount(payload: unknown): number | null {
    const record = objectValue(payload);
    if (!record) return null;
    const direct = countOrNull(firstValue(record, ['available_count', 'availableCount', 'available', 'remaining', 'count']));
    if (direct !== null) return direct;
    for (const key of ['rate_limit_reset_credits', 'reset_credits']) {
        const nested = objectValue(record[key]);
        const nestedCount = nested
            ? countOrNull(firstValue(nested, ['available_count', 'availableCount', 'available', 'remaining', 'count']))
            : null;
        if (nestedCount !== null) return nestedCount;
    }
    return null;
}

function creditStatus(input: {
    rawStatus: string | null;
    expiresAt: string | null;
    redeemedAt: string | null;
    fetchedAt: string;
}): string {
    const rawStatus = input.rawStatus?.toLowerCase();
    if (rawStatus === 'available' || rawStatus === 'expired' || rawStatus === 'redeemed') {
        return rawStatus;
    }
    if (input.redeemedAt) return 'redeemed';
    if (input.expiresAt && Date.parse(input.expiresAt) <= Date.parse(input.fetchedAt)) return 'expired';
    return 'available';
}

function normalizeResetCreditItem(value: unknown, index: number, fetchedAt: string): CodexResetCreditItem | null {
    const record = objectValue(value);
    if (!record) return null;
    const grantedAt = isoDateOrNull(firstValue(record, ['granted_at', 'grant_date', 'grantedAt', 'created_at', 'createdAt', 'created']));
    const expiresAt = isoDateOrNull(firstValue(record, ['expires_at', 'expiry_date', 'expiration_date', 'expiresAt', 'expires', 'valid_until', 'validUntil']));
    const redeemedAt = isoDateOrNull(firstValue(record, ['redeemed_at', 'consumed_at', 'used_at', 'redeemedAt', 'consumedAt', 'usedAt']));
    const rawId = stringOrNull(firstValue(record, ['id', 'credit_id', 'creditId', 'reset_credit_id', 'resetCreditId', 'grant_id', 'grantId']));
    const fingerprint = rawId ?? [
        grantedAt,
        expiresAt,
        redeemedAt,
        String(index),
    ].filter(Boolean).join(':');
    if (!fingerprint) return null;

    return {
        creditKey: hashCreditKey(fingerprint),
        status: creditStatus({
            rawStatus: stringOrNull(firstValue(record, ['status', 'state'])),
            expiresAt,
            redeemedAt,
            fetchedAt,
        }),
        resetType: null,
        title: null,
        grantedAt,
        expiresAt,
        redeemedAt,
    };
}

export function normalizeCodexResetCredits(input: {
    accountKey: string;
    slotIndex: number;
    fetchedAt: string;
    payload: unknown;
    fallbackAvailableCount?: number | null;
    source?: CodexResetCreditsSnapshot['source'];
}): CodexResetCreditsSnapshot | null {
    const credits = getResetCreditArray(input.payload)
        .map((entry, index) => normalizeResetCreditItem(entry, index, input.fetchedAt))
        .filter((entry): entry is CodexResetCreditItem => Boolean(entry));
    const payloadCount = extractResetCreditAvailableCount(input.payload);
    const availableCount = payloadCount ?? input.fallbackAvailableCount ?? (credits.length
        ? credits.filter((credit) => credit.status === 'available').length
        : null);
    if (availableCount === null && credits.length === 0) return null;

    return {
        accountKey: input.accountKey,
        slotIndex: input.slotIndex,
        availableCount,
        detailsAvailable: credits.length > 0,
        source: input.source ?? (credits.length > 0 ? 'live' : 'liveCountOnly'),
        updatedAt: input.fetchedAt,
        credits,
    };
}

function toWindowSnapshot(window: CodexUsageRawResponse['primary_window'], fetchedAt: string): CodexUsageWindowSnapshot | null {
    const resetAtEpoch = window?.resets_at ?? window?.reset_at;
    if (!window || typeof window.used_percent !== 'number' || typeof resetAtEpoch !== 'number') return null;
    if (window.limit_window_seconds !== FIVE_HOURS && window.limit_window_seconds !== SEVEN_DAYS) return null;
    return {
        window: window.limit_window_seconds === FIVE_HOURS ? 'fiveHour' : 'weekly',
        usedPercent: window.used_percent,
        resetAt: new Date(resetAtEpoch * 1000).toISOString(),
        windowMinutes: Math.round(window.limit_window_seconds / 60),
        updatedAt: fetchedAt,
    };
}

export function normalizeCodexUsageSnapshot(input: {
    slotIndex: number;
    slotPath?: string | null;
    fetchedAt: string;
    payload: CodexUsageRawResponse;
    accountId?: string | null;
}): CodexUsageAccountRow {
    const accountId = input.accountId ?? input.payload.account_id;
    if (!accountId) throw new Error('Codex usage payload missing account_id');

    const account: CodexUsageAccountRow = {
        accountKey: hashAccountKey(accountId),
        slotIndex: input.slotIndex,
        slotIndexes: [input.slotIndex],
        slotPaths: cleanSlotPaths([input.slotPath]),
        source: 'live',
        stale: false,
        cooldownUntil: null,
        lastFetchedAt: input.fetchedAt,
        updatedAt: input.fetchedAt,
        fiveHour: null,
        weekly: null,
        resetCredits: normalizeCodexResetCredits({
            accountKey: hashAccountKey(accountId),
            slotIndex: input.slotIndex,
            fetchedAt: input.fetchedAt,
            payload: input.payload.rate_limit_reset_credits ?? input.payload.reset_credits ?? null,
            fallbackAvailableCount: extractResetCreditAvailableCount(input.payload),
        }),
    };

    for (const window of getUsageWindows(input.payload).map((usageWindow) => toWindowSnapshot(usageWindow, input.fetchedAt))) {
        if (!window) continue;
        if (window.window === 'fiveHour') account.fiveHour = window;
        if (window.window === 'weekly') account.weekly = window;
    }

    return account;
}

export const normalizeCodexUsageRow = (payload: CodexUsageRawResponse, options: {
    slotIndex: number;
    slotPath?: string | null;
    fetchedAt: string;
    accountId?: string | null;
}) => normalizeCodexUsageSnapshot({ ...options, payload });

export function isCodexUsageAccountHardExhausted(account: CodexUsageAccountRow): boolean {
    return (account.weekly?.usedPercent ?? 0) >= 100;
}

function snapshotKey(accountKey: string, window: string): string {
    return `${accountKey}:${window}`;
}

function toSnapshotRecords(account: CodexUsageAccountRow): CodexUsageSnapshotRecord[] {
    return [account.fiveHour, account.weekly]
        .filter((window): window is CodexUsageWindowSnapshot => Boolean(window))
        .map((window) => ({
            accountKey: account.accountKey,
            slotIndex: account.slotIndex,
            window: window.window,
            usedPercent: window.usedPercent,
            resetAt: window.resetAt,
            windowMinutes: window.windowMinutes,
            updatedAt: window.updatedAt,
        }));
}

function snapshotChanged(live: CodexUsageSnapshotRecord, persisted?: CodexUsageSnapshotRecord): boolean {
    return !persisted
        || live.slotIndex !== persisted.slotIndex
        || live.usedPercent !== persisted.usedPercent
        || live.resetAt !== persisted.resetAt
        || live.windowMinutes !== persisted.windowMinutes;
}

function accountFromSnapshots(records: CodexUsageSnapshotRecord[], slotIndex: number, slotPath?: string | null): CodexUsageAccountRow | null {
    if (records.length === 0) return null;
    const account: CodexUsageAccountRow = {
        accountKey: records[0]!.accountKey,
        slotIndex,
        slotIndexes: [slotIndex],
        slotPaths: cleanSlotPaths([slotPath]),
        source: 'persisted',
        stale: true,
        cooldownUntil: null,
        lastFetchedAt: null,
        updatedAt: records[0]!.updatedAt ?? null,
        fiveHour: null,
        weekly: null,
        resetCredits: null,
    };

    for (const record of records) {
        const window = {
            window: record.window,
            usedPercent: record.usedPercent,
            resetAt: record.resetAt,
            windowMinutes: record.windowMinutes,
            updatedAt: record.updatedAt ?? '',
        };
        if (record.window === 'fiveHour') account.fiveHour = window;
        if (record.window === 'weekly') account.weekly = window;
    }

    return account;
}

function unexpiredSnapshots(records: CodexUsageSnapshotRecord[], now: number): CodexUsageSnapshotRecord[] {
    return records.filter((record) => Date.parse(record.resetAt) > now);
}

function freshUnexpiredSnapshots(records: CodexUsageSnapshotRecord[], now: number): CodexUsageSnapshotRecord[] {
    return unexpiredSnapshots(records, now).filter((record) => {
        const updatedAt = Date.parse(record.updatedAt ?? '');
        return Number.isFinite(updatedAt) && (now - updatedAt) <= DEFAULT_SELECTOR_PERSISTED_MAX_AGE_MS;
    });
}

function bindAccountToSlot(
    account: CodexUsageAccountRow,
    slot: SlotBinding,
    overrides: Partial<Pick<CodexUsageAccountRow, 'source' | 'stale' | 'cooldownUntil'>> = {},
): CodexUsageAccountRow {
    const slotIndexes = Array.from(new Set([...account.slotIndexes, slot.slotIndex])).sort((left, right) => left - right);
    return {
        ...account,
        ...overrides,
        slotIndex: slotIndexes[0] ?? slot.slotIndex,
        slotIndexes,
        slotPaths: cleanSlotPaths([...account.slotPaths, slot.slotPath]),
    };
}

function isSelectorPersistedStale(account: CodexUsageAccountRow, now: number, maxAgeMs: number): boolean {
    if (account.source !== 'persisted' || !account.updatedAt) return false;
    return (now - Date.parse(account.updatedAt)) > maxAgeMs;
}

function createSelectorCooldownAccount(
    accountKey: string,
    slotIndex: number,
    slotPath: string | null,
    cooldownUntil: number,
): CodexUsageAccountRow {
    return {
        accountKey,
        slotIndex,
        slotIndexes: [slotIndex],
        slotPaths: cleanSlotPaths([slotPath]),
        source: 'cooldown',
        stale: true,
        cooldownUntil: new Date(cooldownUntil).toISOString(),
        lastFetchedAt: null,
        updatedAt: null,
        fiveHour: null,
        weekly: null,
        resetCredits: null,
    };
}

const sourceRank: Record<CodexUsageAccountRow['source'], number> = {
    live: 0,
    cache: 1,
    persisted: 2,
    cooldown: 3,
};

function laterIso(...values: Array<string | null | undefined>): string | null {
    let latest: string | null = null;
    for (const value of values) {
        if (!value) continue;
        if (!latest || Date.parse(value) >= Date.parse(latest)) latest = value;
    }
    return latest;
}

function newerWindow(
    candidate: CodexUsageWindowSnapshot | null,
    current: CodexUsageWindowSnapshot | null,
): CodexUsageWindowSnapshot | null {
    if (!candidate) return current;
    if (!current) return candidate;
    return Date.parse(candidate.updatedAt || '') >= Date.parse(current.updatedAt || '') ? candidate : current;
}

function newerResetCredits(
    candidate: CodexResetCreditsSnapshot | null,
    current: CodexResetCreditsSnapshot | null,
): CodexResetCreditsSnapshot | null {
    if (!candidate) return current;
    if (!current) return candidate;
    return Date.parse(candidate.updatedAt || '') >= Date.parse(current.updatedAt || '') ? candidate : current;
}

function preferredSource(candidate: CodexUsageAccountRow, current: CodexUsageAccountRow): boolean {
    const candidateRank = sourceRank[candidate.source] ?? 9;
    const currentRank = sourceRank[current.source] ?? 9;
    return candidateRank < currentRank
        || (candidateRank === currentRank && Number(candidate.stale) < Number(current.stale));
}

function mergeAccountRows(rows: CodexUsageAccountRow[]): CodexUsageAccountRow[] {
    const grouped = new Map<string, CodexUsageAccountRow>();

    for (const row of rows) {
        const rowSlotIndexes = row.slotIndexes.length ? row.slotIndexes : [row.slotIndex];
        const current = grouped.get(row.accountKey);

        if (!current) {
            grouped.set(row.accountKey, {
                ...row,
                slotIndex: Math.min(...rowSlotIndexes),
                slotIndexes: [...rowSlotIndexes].sort((left, right) => left - right),
            });
            continue;
        }

        const slotIndexes = Array.from(new Set([...current.slotIndexes, ...rowSlotIndexes])).sort((left, right) => left - right);
        current.slotIndexes = slotIndexes;
        current.slotIndex = slotIndexes[0] ?? current.slotIndex;
        current.slotPaths = cleanSlotPaths([...current.slotPaths, ...row.slotPaths]);
        current.fiveHour = newerWindow(row.fiveHour, current.fiveHour);
        current.weekly = newerWindow(row.weekly, current.weekly);
        current.resetCredits = newerResetCredits(row.resetCredits, current.resetCredits);
        current.lastFetchedAt = laterIso(current.lastFetchedAt, row.lastFetchedAt);
        current.updatedAt = laterIso(current.updatedAt, row.updatedAt);
        current.cooldownUntil = laterIso(current.cooldownUntil, row.cooldownUntil);
        current.stale = current.stale && row.stale;
        if (preferredSource(row, current)) current.source = row.source;
    }

    return Array.from(grouped.values()).sort((left, right) => left.slotIndex - right.slotIndex);
}

async function defaultListSlots(): Promise<ServiceSlot[]> {
    return getCodexAuthSlots().map((slot) => ({
        slotIndex: slot.slotIndex,
        path: slot.path,
        accountId: null,
        rateLimitedUntil: slot.rateLimitedUntil,
        auth: null,
        authAvailable: slot.authAvailable,
        authUnavailableReason: slot.authUnavailableReason,
        authRetryAt: slot.authRetryAt,
    }));
}

function formatAuthUnavailableMessage(slot: ServiceSlot): string {
    const retry = slot.authRetryAt ? `; retry after ${slot.authRetryAt}` : '';
    switch (slot.authUnavailableReason) {
        case 'expired_refresh_failed':
            return `Codex auth refresh failed${retry}`;
        case 'expired':
            return 'Codex auth access token is expired';
        case 'unknown_account':
            return 'Codex auth account identity is unknown';
        case 'missing':
            return 'Codex auth missing';
        default:
            return 'Codex auth missing';
    }
}

async function ensureCodexUsageAuth(slot: ServiceSlot, dispatcher: ReturnType<typeof getProxyAgent>, timeoutMs: number): Promise<CodexAuth> {
    const auth = slot.auth ?? await loadCodexUsageAuthSlot({
        slotIndex: slot.slotIndex,
        path: slot.path ?? null,
        rateLimitedUntil: slot.rateLimitedUntil ?? 0,
        authAvailable: slot.authAvailable ?? true,
        authUnavailableReason: slot.authUnavailableReason ?? null,
        authRetryAt: slot.authRetryAt ?? null,
    } as CodexAuthSlotSnapshot, dispatcher, timeoutMs);
    if (!auth) {
        const latestSlot = getCodexAuthSlots().find((candidate) => candidate.slotIndex === slot.slotIndex);
        throw new Error(formatAuthUnavailableMessage({
            ...slot,
            authUnavailableReason: latestSlot?.authUnavailableReason ?? slot.authUnavailableReason,
            authRetryAt: latestSlot?.authRetryAt ?? slot.authRetryAt,
        }));
    }

    slot.auth = auth;
    slot.accountId = auth.accountId || slot.accountId || null;
    return auth;
}

async function defaultFetchUsage(slot: ServiceSlot, timeoutMs = DEFAULT_TIMEOUT_MS): Promise<CodexUsageRawResponse> {
    const dispatcher = getProxyAgent();
    const auth = await ensureCodexUsageAuth(slot, dispatcher, timeoutMs);
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
    const fetchOptions: RequestInit & { dispatcher?: unknown } = {
        method: 'GET',
        headers: {
            Authorization: `Bearer ${auth.accessToken}`,
            ...(auth.accountId ? { 'chatgpt-account-id': auth.accountId } : {}),
        },
        signal: controller.signal,
    };
    if (dispatcher) fetchOptions.dispatcher = dispatcher;

    try {
        const response = await fetch(USAGE_URL, fetchOptions as RequestInit);
        if (!response.ok) throw new Error(`Codex usage HTTP ${response.status}`);
        return await response.json() as CodexUsageRawResponse;
    } catch (error) {
        if (error instanceof DOMException && error.name === 'AbortError') {
            throw new Error(`codex usage timed out after ${timeoutMs}ms`);
        }
        throw error;
    } finally {
        clearTimeout(timeoutId);
    }
}

async function defaultFetchResetCredits(slot: ServiceSlot, timeoutMs = DEFAULT_TIMEOUT_MS): Promise<unknown> {
    const dispatcher = getProxyAgent();
    const auth = await ensureCodexUsageAuth(slot, dispatcher, timeoutMs);
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
    const fetchOptions: RequestInit & { dispatcher?: unknown } = {
        method: 'GET',
        headers: {
            Authorization: `Bearer ${auth.accessToken}`,
            ...(auth.accountId ? { 'ChatGPT-Account-ID': auth.accountId } : {}),
            'OpenAI-Beta': 'codex-1',
            originator: 'Codex Desktop',
        },
        signal: controller.signal,
    };
    if (dispatcher) fetchOptions.dispatcher = dispatcher;

    try {
        const response = await fetch(RESET_CREDITS_URL, fetchOptions as RequestInit);
        if (!response.ok) throw new Error(`Codex reset credits HTTP ${response.status}`);
        return await response.json() as unknown;
    } catch (error) {
        if (error instanceof DOMException && error.name === 'AbortError') {
            throw new Error(`codex reset credits timed out after ${timeoutMs}ms`);
        }
        throw error;
    } finally {
        clearTimeout(timeoutId);
    }
}

function withTimeout<T>(promise: Promise<T>, timeoutMs: number, message: string): Promise<T> {
    if (timeoutMs <= 0) return promise;

    return new Promise<T>((resolve, reject) => {
        const timeoutId = setTimeout(() => reject(new Error(message)), timeoutMs);
        promise
            .then((value) => resolve(value))
            .catch((error) => reject(error))
            .finally(() => clearTimeout(timeoutId));
    });
}

function defaultCooldownState(slots: ServiceSlot[]): CooldownState[] {
    return slots
        .filter((slot) => slot.accountId && (slot.rateLimitedUntil ?? 0) > Date.now())
        .map((slot) => ({
            slotIndex: slot.slotIndex,
            accountId: slot.accountId!,
            cooldownUntil: new Date(slot.rateLimitedUntil!).toISOString(),
        }));
}

export function createCodexUsageService(deps: ServiceDeps = {}) {
    let responseCache: { expiresAt: number; signature: string; result: SnapshotResult } | null = null;
    const slotCache = new Map<string, CachedAccount>();
    const tokenSlotIdentityCache = new Map<number, string>();
    let selectorRefreshStartedAt = 0;
    let selectorRefreshPromise: Promise<void> | null = null;

    async function getUsageSnapshot(options: UsageSnapshotOptions = {}): Promise<SnapshotResult> {
        const now = deps.now?.() ?? Date.now();
        const timeoutMs = deps.timeoutMs ?? DEFAULT_TIMEOUT_MS;
        const allSlots = await Promise.resolve((deps.listSlots ?? defaultListSlots)());
        const requestedSlotIndexes = options.slotIndexes ? new Set(options.slotIndexes) : null;
        const slots = requestedSlotIndexes
            ? allSlots.filter((slot) => requestedSlotIndexes.has(slot.slotIndex))
            : allSlots;
        const signature = JSON.stringify(slots.map((slot) => [slot.slotIndex, slot.path ?? null, slot.accountId, slot.rateLimitedUntil ?? 0]));
        if (!options.force && responseCache && responseCache.signature === signature && responseCache.expiresAt > now) {
            return responseCache.result;
        }

        const cacheTtlMs = deps.cacheTtlMs ?? DEFAULT_CACHE_TTL_MS;
        const persisted = await Promise.resolve((deps.readLatestSnapshots ?? getCodexUsageSnapshots)());
        const persistedResetCredits = await Promise.resolve((deps.readLatestResetCredits ?? getCodexResetCreditSnapshots)());
        const persistedByKey = new Map(persisted.map((record) => [snapshotKey(record.accountKey, record.window), record]));
        const persistedByAccountKey = new Map<string, CodexUsageSnapshotRecord[]>();
        for (const record of persisted) {
            persistedByAccountKey.set(record.accountKey, [...(persistedByAccountKey.get(record.accountKey) ?? []), record]);
        }
        const persistedResetByAccountKey = new Map(persistedResetCredits.map((record) => [record.accountKey, record]));

        const fetchUsage = deps.fetchUsage ?? ((slot: ServiceSlot) => defaultFetchUsage(slot, timeoutMs));
        const fetchResetCredits = deps.fetchResetCredits ?? ((slot: ServiceSlot) => defaultFetchResetCredits(slot, timeoutMs));
        const settled = await Promise.allSettled(slots.map(async (slot) => {
            const fetchedAt = new Date(now).toISOString();
            const payload = await withTimeout(
                Promise.resolve(fetchUsage(slot)),
                timeoutMs,
                `codex usage timed out after ${timeoutMs}ms`,
            );
            slot.accountId = slot.accountId || payload.account_id || null;

            const account = normalizeCodexUsageSnapshot({
                slotIndex: slot.slotIndex,
                slotPath: slot.path,
                fetchedAt,
                payload,
                accountId: slot.accountId,
            });
            let resetCreditError: CodexUsageSlotError | null = null;
            try {
                const resetPayload = await withTimeout(
                    Promise.resolve(fetchResetCredits(slot)),
                    timeoutMs,
                    `codex reset credits timed out after ${timeoutMs}ms`,
                );
                account.resetCredits = normalizeCodexResetCredits({
                    accountKey: account.accountKey,
                    slotIndex: slot.slotIndex,
                    fetchedAt,
                    payload: resetPayload,
                }) ?? account.resetCredits;
            } catch (error) {
                resetCreditError = {
                    slotIndex: slot.slotIndex,
                    message: error instanceof Error ? error.message : String(error),
                    source: account.resetCredits ? 'live' : persistedResetByAccountKey.has(account.accountKey) ? 'persisted' : 'none',
                };
                account.resetCredits = account.resetCredits ?? persistedResetByAccountKey.get(account.accountKey) ?? null;
            }

            return { account, resetCreditError };
        }));
        const cooldownMap = new Map((await Promise.resolve((deps.getCooldownState ?? (() => defaultCooldownState(slots)))())).map((item) => [item.slotIndex, item]));

        const accounts: CodexUsageAccountRow[] = [];
        const liveAccounts: CodexUsageAccountRow[] = [];
        const slotErrors: CodexUsageSlotError[] = [];
        const resetCreditErrors: CodexUsageSlotError[] = [];

        for (const [index, result] of settled.entries()) {
            const slot = slots[index]!;
            if (result.status === 'fulfilled') {
                const { account, resetCreditError } = result.value;
                accounts.push(account);
                liveAccounts.push(account);
                (deps.clearSlotCooldown ?? clearCodexSlotRateLimit)(slot.slotIndex, slot.accountId);
                if (resetCreditError) resetCreditErrors.push(resetCreditError);
                slotCache.set(account.accountKey, { account, expiresAt: now + cacheTtlMs });
                if (slot.path === null) tokenSlotIdentityCache.set(slot.slotIndex, account.accountKey);
                if (account.weekly) {
                    await Promise.resolve((deps.writeCheckpoint ?? setCodexActivationCheckpoint)({
                        slotIndex: slot.slotIndex,
                        accountKey: account.accountKey,
                        expectedWeeklyResetAt: account.weekly.resetAt,
                        lastUsageCheckAt: account.lastFetchedAt,
                        updatedAt: account.lastFetchedAt ?? new Date(now).toISOString(),
                    }));
                }
                continue;
            }

            const accountKey = slot.accountId
                ? hashAccountKey(slot.accountId)
                : slot.path === null
                    ? tokenSlotIdentityCache.get(slot.slotIndex) ?? null
                    : null;
            const cached = accountKey ? slotCache.get(accountKey) : null;
            const fallbackFromIdentity: CodexUsageAccountRow | null = cached && cached.expiresAt > now
                ? {
                    ...cached.account,
                    slotIndex: Math.min(...new Set([...(cached.account.slotIndexes ?? [cached.account.slotIndex]), slot.slotIndex])),
                    slotIndexes: Array.from(new Set([...(cached.account.slotIndexes ?? [cached.account.slotIndex]), slot.slotIndex])).sort((left, right) => left - right),
                    slotPaths: cleanSlotPaths([...(cached.account.slotPaths ?? []), slot.path]),
                    source: 'cache',
                    stale: false,
                }
                : accountKey
                    ? accountFromSnapshots(
                        freshUnexpiredSnapshots(persistedByAccountKey.get(accountKey) ?? [], now),
                        slot.slotIndex,
                        slot.path,
                    )
                    : null;
            const fallback: CodexUsageAccountRow | null = fallbackFromIdentity ?? (() => {
                    const cooldown = cooldownMap.get(slot.slotIndex);
                    if (!cooldown) return null;
                    return {
                        accountKey: hashAccountKey(cooldown.accountId),
                        slotIndex: slot.slotIndex,
                        slotIndexes: [slot.slotIndex],
                        slotPaths: cleanSlotPaths([slot.path]),
                        source: 'cooldown',
                        stale: true,
                        cooldownUntil: cooldown.cooldownUntil,
                        lastFetchedAt: null,
                        updatedAt: null,
                        fiveHour: null,
                        weekly: null,
                        resetCredits: null,
                    } satisfies CodexUsageAccountRow;
                })();

            slotErrors.push({
                slotIndex: slot.slotIndex,
                message: result.reason instanceof Error ? result.reason.message : String(result.reason),
                source: fallback?.source ?? 'none',
            });
            if (fallback && !fallback.resetCredits) {
                fallback.resetCredits = persistedResetByAccountKey.get(fallback.accountKey) ?? null;
            }
            if (fallback) accounts.push(fallback);
        }

        const changed = mergeAccountRows(liveAccounts)
            .flatMap((account) => toSnapshotRecords(account))
            .filter((record) => snapshotChanged(record, persistedByKey.get(snapshotKey(record.accountKey, record.window))));

        if (changed.length > 0) {
            await Promise.resolve((deps.writeSnapshots ?? upsertCodexUsageSnapshots)(changed));
        }
        const resetCreditRecords = mergeAccountRows(liveAccounts)
            .flatMap((account) => account.resetCredits && account.resetCredits.source !== 'persisted'
                ? [account.resetCredits]
                : []);
        if (resetCreditRecords.length > 0) {
            await Promise.resolve((deps.writeResetCredits ?? upsertCodexResetCreditSnapshots)(resetCreditRecords));
        }

        if (accounts.length === 0) {
            return {
                status: 502,
                partial: false,
                accounts: [],
                slotErrors,
                resetCreditErrors,
                error: { message: 'No Codex usage data available' },
            };
        }

        const result: SnapshotResult = {
            status: 200,
            partial: slotErrors.length > 0 || resetCreditErrors.length > 0,
            accounts: mergeAccountRows(accounts),
            slotErrors,
            resetCreditErrors,
        };
        responseCache = { expiresAt: now + cacheTtlMs, signature, result };
        return result;
    }

    function triggerSelectorRefresh(now: number, refreshThrottleMs: number): boolean {
        if (selectorRefreshPromise || (now - selectorRefreshStartedAt) < refreshThrottleMs) return false;
        selectorRefreshStartedAt = now;
        selectorRefreshPromise = Promise.resolve(getUsageSnapshot())
            .then(() => undefined)
            .catch(() => undefined)
            .finally(() => {
                selectorRefreshPromise = null;
            });
        return true;
    }

    async function getSelectorSnapshot(input: CodexUsageSelectorRequest): Promise<CodexUsageSelectorSnapshot> {
        const now = deps.now?.() ?? Date.now();
        const persisted = await Promise.resolve((deps.readLatestSnapshots ?? getCodexUsageSnapshots)());
        const persistedByAccountKey = new Map<string, CodexUsageSnapshotRecord[]>();
        const persistedBySlotIndex = new Map<number, CodexUsageSnapshotRecord[]>();
        const persistedMaxAgeMs = input.persistedMaxAgeMs ?? DEFAULT_SELECTOR_PERSISTED_MAX_AGE_MS;
        const refreshThrottleMs = input.refreshThrottleMs ?? DEFAULT_SELECTOR_REFRESH_THROTTLE_MS;

        for (const record of persisted) {
            persistedByAccountKey.set(record.accountKey, [...(persistedByAccountKey.get(record.accountKey) ?? []), record]);
            persistedBySlotIndex.set(record.slotIndex, [...(persistedBySlotIndex.get(record.slotIndex) ?? []), record]);
        }

        const accounts: CodexUsageAccountRow[] = [];
        const unknownAccountSlotIndexes: number[] = [];
        const missingUsageSlotIndexes: number[] = [];
        const staleAccountKeys = new Set<string>();

        for (const slot of input.slots) {
            if (!slot.accountKey) {
                unknownAccountSlotIndexes.push(slot.slotIndex);
                continue;
            }
            const slotAccountKey = slot.accountKey;

            const cached = slotCache.get(slotAccountKey);
            if (cached && cached.expiresAt > now) {
                accounts.push(bindAccountToSlot(cached.account, { slotIndex: slot.slotIndex, slotPath: slot.slotPath }, {
                    source: 'cache',
                    stale: false,
                }));
                continue;
            }

            const canonicalRecords = unexpiredSnapshots(persistedByAccountKey.get(slotAccountKey) ?? [], now);
            const slotRecords = persistedBySlotIndex.get(slot.slotIndex) ?? [];
            const fallbackRecords = canonicalRecords.length > 0
                ? canonicalRecords
                : unexpiredSnapshots(slotRecords, now);
            const persistedAccount = accountFromSnapshots(fallbackRecords, slot.slotIndex, slot.slotPath);
            if (persistedAccount) {
                persistedAccount.accountKey = slotAccountKey;
                if (canonicalRecords.length === 0 && fallbackRecords.length > 0) {
                    await Promise.resolve((deps.writeSnapshots ?? upsertCodexUsageSnapshots)(
                        fallbackRecords.map((record) => ({ ...record, accountKey: slotAccountKey })),
                    ));
                }
                if (isSelectorPersistedStale(persistedAccount, now, persistedMaxAgeMs)) {
                    staleAccountKeys.add(slotAccountKey);
                    accounts.push(persistedAccount);
                    continue;
                }
                accounts.push({
                    ...persistedAccount,
                    stale: false,
                });
                continue;
            }

            if (slot.rateLimitedUntil > now) {
                accounts.push(createSelectorCooldownAccount(slotAccountKey, slot.slotIndex, slot.slotPath, slot.rateLimitedUntil));
                continue;
            }

            missingUsageSlotIndexes.push(slot.slotIndex);
        }

        const triggeredBackgroundRefresh = input.allowBackgroundRefresh !== false
            && (staleAccountKeys.size > 0 || missingUsageSlotIndexes.length > 0)
            ? triggerSelectorRefresh(now, refreshThrottleMs)
            : false;

        return {
            slots: input.slots.map((slot) => ({ ...slot })),
            accounts: mergeAccountRows(accounts),
            unknownAccountSlotIndexes,
            missingUsageSlotIndexes,
            staleAccountKeys: [...staleAccountKeys].sort(),
            triggeredBackgroundRefresh,
        };
    }

    function reset(): void {
        responseCache = null;
        slotCache.clear();
        tokenSlotIdentityCache.clear();
        selectorRefreshStartedAt = 0;
        selectorRefreshPromise = null;
    }

    return { getUsageSnapshot, getSelectorSnapshot, reset };
}

const defaultService = createCodexUsageService();

export async function getCodexUsage(options: UsageSnapshotOptions = {}): Promise<{ status: number; body: Omit<SnapshotResult, 'status'> }> {
    const result = await defaultService.getUsageSnapshot(options);
    const { status, ...body } = result;
    return { status, body };
}

export async function getCodexUsageSelectorSnapshot(input: CodexUsageSelectorRequest): Promise<CodexUsageSelectorSnapshot> {
    return defaultService.getSelectorSnapshot(input);
}

export function resetCodexUsageState(): void {
    defaultService.reset();
}
