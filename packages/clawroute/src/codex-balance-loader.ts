import {
    CodexBalanceLoaderAccountScore,
    CodexBalanceLoaderAffinityStatus,
    CodexBalanceLoaderDecisionReason,
    CodexBalanceLoaderRequest,
    CodexBalanceLoaderResult,
    CodexUsageAccountRow,
    CodexUsageSelectorSlotIdentity,
} from './types.js';

const DEFAULT_PERSISTED_MAX_AGE_MS = 15 * 60_000;
const FIVE_HOUR_MS = 18_000_000;
const WEEKLY_WINDOW_MS = 7 * 24 * 60 * 60_000;
const AFFINITY_SCORE_BAND = 0.05;
const AFFINITY_BOTTLENECK_FLOOR = 0.20;
const AFFINITY_SINGLE_WINDOW_FLOOR = 0.25;

type ScoredAccount = CodexBalanceLoaderAccountScore & { slotCandidates: CodexUsageSelectorSlotIdentity[] };
type ScheduledScoredAccount = ScoredAccount & {
    anchorWeekday: number;
    laneRank: number;
    seedOrder: number;
    telemetryFresh: boolean;
    spillover: boolean;
};
type LegacyBalanceLoaderSlot = {
    slotIndex: number;
    accountKey: string;
    pendingLeases?: number;
    lastSelectedAt?: string | null;
    rateLimitedUntil?: number;
};
type LegacyBalanceLoaderRequest = {
    now: number;
    provider: CodexBalanceLoaderRequest['provider'];
    accounts: CodexUsageAccountRow[];
    slots: LegacyBalanceLoaderSlot[];
    excludedSlotIndexes?: ReadonlySet<number> | readonly number[];
    excludedAccountKeys?: ReadonlySet<string> | readonly string[];
    sessionAffinity?: {
        sessionId: string | null;
        cacheEligible: boolean;
        preferredProvider: CodexBalanceLoaderRequest['provider'];
        preferredAccountKey: string;
    } | null;
    schedule?: CodexBalanceLoaderRequest['schedule'];
};

function toSet<T>(input?: ReadonlySet<T> | readonly T[]): Set<T> {
    return input instanceof Set ? new Set(input) : new Set(input ?? []);
}

function clamp(value: number): number {
    return Math.max(0, Math.min(1, value));
}

function getResidual(usedPercent?: number | null): number | null {
    return typeof usedPercent === 'number' ? clamp(1 - (usedPercent / 100)) : null;
}

function isPersistedStale(account: CodexUsageAccountRow, now: number, maxAgeMs: number): boolean {
    if (account.source !== 'persisted' || !account.updatedAt) return false;
    return (now - Date.parse(account.updatedAt)) > maxAgeMs;
}

function isHardExhausted(account: CodexUsageAccountRow): boolean {
    return (account.weekly?.usedPercent ?? 0) >= 100;
}

function isExcluded(
    slot: CodexUsageSelectorSlotIdentity,
    excludedSlots: Set<number>,
    excludedAccounts: Set<string>,
): boolean {
    return excludedSlots.has(slot.slotIndex) || (slot.accountKey ? excludedAccounts.has(slot.accountKey) : false);
}

function groupEligibleSlots(
    slots: CodexUsageSelectorSlotIdentity[],
    now: number,
    excludedSlots: Set<number>,
    excludedAccounts: Set<string>,
): Map<string, CodexUsageSelectorSlotIdentity[]> {
    const grouped = new Map<string, CodexUsageSelectorSlotIdentity[]>();
    for (const slot of slots) {
        if (!slot.accountKey || slot.rateLimitedUntil > now || isExcluded(slot, excludedSlots, excludedAccounts)) continue;
        grouped.set(slot.accountKey, [...(grouped.get(slot.accountKey) ?? []), slot]);
    }
    return grouped;
}

function getRelevantSlotIndexes(
    slots: CodexUsageSelectorSlotIdentity[],
    now: number,
    excludedSlots: Set<number>,
    excludedAccounts: Set<string>,
): number[] {
    return slots
        .filter((slot) => !isExcluded(slot, excludedSlots, excludedAccounts) && slot.rateLimitedUntil <= now)
        .map((slot) => slot.slotIndex);
}

function hasRelevantUnknownAccount(
    slots: CodexUsageSelectorSlotIdentity[],
    now: number,
    excludedSlots: Set<number>,
    excludedAccounts: Set<string>,
): boolean {
    return slots.some((slot) => !slot.accountKey && !isExcluded(slot, excludedSlots, excludedAccounts) && slot.rateLimitedUntil <= now);
}

function getScore(account: CodexUsageAccountRow, pendingLeases: number, now: number): number | null {
    const r5 = getResidual(account.fiveHour?.usedPercent);
    const rw = getResidual(account.weekly?.usedPercent);
    const penalty = account.source === 'persisted' ? 0.10 : 0;
    if (r5 === null && rw === null) return null;
    if (r5 !== null && rw !== null) {
        const resetAt = Date.parse(account.fiveHour?.resetAt ?? '');
        const t5 = clamp(Number.isFinite(resetAt) ? ((resetAt - now) / FIVE_HOUR_MS) : 1);
        const harmony = (2 * r5 * rw) / (r5 + rw + 0.01);
        return (0.65 * Math.min(r5, rw)) + (0.25 * harmony) + (0.10 * r5 * (1 - t5)) - (0.15 * pendingLeases) - penalty;
    }
    if (r5 !== null) return (0.70 * r5) - (0.15 * pendingLeases) - penalty;
    return (0.55 * rw!) - (0.15 * pendingLeases) - penalty;
}

function scoreAccounts(
    request: CodexBalanceLoaderRequest,
    eligibleSlots: Map<string, CodexUsageSelectorSlotIdentity[]>,
): ScoredAccount[] {
    const maxAgeMs = request.persistedMaxAgeMs ?? DEFAULT_PERSISTED_MAX_AGE_MS;
    const scores: ScoredAccount[] = [];

    for (const account of request.snapshot.accounts) {
        const slotCandidates = eligibleSlots.get(account.accountKey);
        if (!slotCandidates || isPersistedStale(account, request.now, maxAgeMs)) continue;
        const pendingLeases = request.pendingLeasesByAccountKey?.[account.accountKey] ?? 0;
        const baseScore = getScore(account, pendingLeases, request.now);
        if (account.source === 'cooldown' || baseScore === null) continue;
        scores.push({
            accountKey: account.accountKey,
            slotIndexes: slotCandidates.map((slot) => slot.slotIndex).sort((left, right) => left - right),
            baseScore,
            source: account.source,
            pendingLeases,
            bottleneckResidual: Math.min(getResidual(account.fiveHour?.usedPercent) ?? 1, getResidual(account.weekly?.usedPercent) ?? 1),
            fiveHourResidual: getResidual(account.fiveHour?.usedPercent),
            weeklyResidual: getResidual(account.weekly?.usedPercent),
            slotCandidates,
        });
    }

    return scores.sort((left, right) => right.baseScore - left.baseScore || left.slotIndexes[0]! - right.slotIndexes[0]!);
}

function hasHealthyAffinityCandidate(score: ScoredAccount): boolean {
    const residuals = [score.fiveHourResidual, score.weeklyResidual].filter((value): value is number => value !== null);
    if (residuals.length === 0) return false;
    if (residuals.length === 1) return (residuals[0] ?? 0) >= AFFINITY_SINGLE_WINDOW_FLOOR;
    return (score.bottleneckResidual ?? 0) >= AFFINITY_BOTTLENECK_FLOOR;
}

function chooseAffinityAccount(
    scores: ScoredAccount[],
    request: CodexBalanceLoaderRequest,
): { status: CodexBalanceLoaderAffinityStatus; winner: ScoredAccount } {
    const winner = scores[0]!;
    const affinity = request.affinity;
    if (!affinity?.sessionId || !affinity.preferred) return { status: 'not_requested', winner };
    if (!affinity.cacheEligible) return { status: 'cache_hint_missing', winner };
    if (affinity.preferred.provider !== request.provider) return { status: 'provider_mismatch', winner };
    if (winner.accountKey === affinity.preferred.accountKey) return { status: 'best_score', winner };

    const preferred = scores.find((score) => score.accountKey === affinity.preferred!.accountKey);
    if (!preferred) {
        const seen = request.snapshot.accounts.some((account) => account.accountKey === affinity.preferred!.accountKey);
        return { status: seen ? 'preferred_ineligible' : 'preferred_missing', winner };
    }
    if (!hasHealthyAffinityCandidate(preferred)) return { status: 'preferred_low_headroom', winner };
    if ((winner.baseScore - preferred.baseScore) > AFFINITY_SCORE_BAND) return { status: 'score_gap', winner };
    return { status: 'applied', winner: preferred };
}

function chooseSlot(
    slots: CodexUsageSelectorSlotIdentity[],
    slotPendingLeasesByIndex?: Readonly<Record<number, number>>,
    slotLastSelectedAtByIndex?: Readonly<Record<number, number>>,
): number {
    return [...slots]
        .sort((left, right) => {
            const leftPending = slotPendingLeasesByIndex?.[left.slotIndex] ?? 0;
            const rightPending = slotPendingLeasesByIndex?.[right.slotIndex] ?? 0;
            if (leftPending !== rightPending) return leftPending - rightPending;
            const leftSelectedAt = slotLastSelectedAtByIndex?.[left.slotIndex] ?? 0;
            const rightSelectedAt = slotLastSelectedAtByIndex?.[right.slotIndex] ?? 0;
            return leftSelectedAt - rightSelectedAt || left.slotIndex - right.slotIndex;
        })[0]!.slotIndex;
}

function emptyResult(
    fallbackReason: CodexBalanceLoaderResult['fallbackReason'],
): CodexBalanceLoaderResult {
    return { selection: null, fallbackReason, affinityStatus: 'not_requested', scores: [] };
}

function resultWithScheduleMetadata(
    fallbackReason: CodexBalanceLoaderResult['fallbackReason'],
    request: CodexBalanceLoaderRequest,
    activeWeekday: number | null,
    activeLaneTelemetryFresh: boolean,
): CodexBalanceLoaderResult {
    return {
        ...emptyResult(fallbackReason),
        currentWeekdayUtc: request.schedule?.currentWeekdayUtc,
        activeWeekday,
        activeLaneTelemetryFresh,
        decisionReason: 'legacy_unavailable',
    };
}

function resolveActiveWeekday(
    currentWeekdayUtc: number,
    populatedWeekdays: Set<number>,
): number | null {
    if (populatedWeekdays.size === 0) return null;
    for (let offset = 0; offset < 7; offset++) {
        const weekday = (currentWeekdayUtc - offset + 7) % 7;
        if (populatedWeekdays.has(weekday)) return weekday;
    }
    return null;
}

function forwardWeekdaySequence(activeWeekday: number, populatedWeekdays: Set<number>): number[] {
    const weekdays: number[] = [];
    for (let offset = 0; offset < 7; offset++) {
        const weekday = (activeWeekday + offset) % 7;
        if (populatedWeekdays.has(weekday)) weekdays.push(weekday);
    }
    return weekdays;
}

function accountHasFreshUsage(
    account: CodexUsageAccountRow | undefined,
    request: CodexBalanceLoaderRequest,
    maxAgeMs: number,
): account is CodexUsageAccountRow {
    if (!account || account.source === 'cooldown' || account.stale) return false;
    if (request.snapshot.staleAccountKeys.includes(account.accountKey)) return false;
    return !isPersistedStale(account, request.now, maxAgeMs);
}

function accountHasFreshWeeklyUsage(
    account: CodexUsageAccountRow | undefined,
    request: CodexBalanceLoaderRequest,
    maxAgeMs: number,
): account is CodexUsageAccountRow {
    if (!accountHasFreshUsage(account, request, maxAgeMs) || !account.weekly) return false;
    const updatedAt = Date.parse(account.weekly.updatedAt ?? account.updatedAt ?? '');
    const resetAt = Date.parse(account.weekly.resetAt);
    return Number.isFinite(updatedAt)
        && Number.isFinite(resetAt)
        && resetAt > request.now
        && (request.now - updatedAt) <= maxAgeMs;
}

function activeLaneTelemetryFresh(
    activeWeekday: number | null,
    request: CodexBalanceLoaderRequest,
    slotAccountKeys: Set<string>,
    accountsByKey: Map<string, CodexUsageAccountRow>,
    maxAgeMs: number,
): boolean {
    if (activeWeekday === null || !request.schedule) return false;
    const activeRows = request.schedule.rows
        .filter((row) => row.anchorWeekday === activeWeekday && slotAccountKeys.has(row.accountKey));
    return activeRows.length > 0
        && activeRows.every((row) => accountHasFreshWeeklyUsage(accountsByKey.get(row.accountKey), request, maxAgeMs));
}

function getOptimizedBurnScore(account: CodexUsageAccountRow, pendingLeases: number, now: number): number {
    const weeklyResidual = getResidual(account.weekly?.usedPercent) ?? 0;
    const weeklyResetAt = Date.parse(account.weekly?.resetAt ?? '');
    const timeRemainingFraction = clamp(Number.isFinite(weeklyResetAt)
        ? (weeklyResetAt - now) / WEEKLY_WINDOW_MS
        : 1);
    const requiredWeeklyBurnRate = weeklyResidual / Math.max(timeRemainingFraction, 1 / (7 * 24));
    return requiredWeeklyBurnRate - (0.15 * pendingLeases);
}

function selectOptimizedCodexBalanceSlot(
    request: CodexBalanceLoaderRequest,
    schedule: NonNullable<CodexBalanceLoaderRequest['schedule']>,
    rowsByAccountKey: Map<string, NonNullable<CodexBalanceLoaderRequest['schedule']>['rows'][number]>,
    accountsByKey: Map<string, CodexUsageAccountRow>,
    slotsByAccount: Map<string, CodexUsageSelectorSlotIdentity[]>,
    activeWeekday: number,
    activeTelemetryFresh: boolean,
): CodexBalanceLoaderResult | null {
    const scores: ScheduledScoredAccount[] = [];
    for (const [accountKey, slotCandidates] of slotsByAccount) {
        const row = rowsByAccountKey.get(accountKey);
        const account = accountsByKey.get(accountKey);
        if (!row || !account || isHardExhausted(account)) continue;
        const fiveHourResidual = getResidual(account.fiveHour?.usedPercent);
        if (fiveHourResidual !== null && fiveHourResidual < AFFINITY_SINGLE_WINDOW_FLOOR) continue;
        const pendingLeases = request.pendingLeasesByAccountKey?.[accountKey] ?? 0;
        scores.push({
            accountKey,
            slotIndexes: slotCandidates.map((slot) => slot.slotIndex).sort((left, right) => left - right),
            baseScore: getOptimizedBurnScore(account, pendingLeases, request.now),
            source: account.source,
            pendingLeases,
            bottleneckResidual: Math.min(
                getResidual(account.fiveHour?.usedPercent) ?? 1,
                getResidual(account.weekly?.usedPercent) ?? 1,
            ),
            fiveHourResidual: fiveHourResidual,
            weeklyResidual: getResidual(account.weekly?.usedPercent),
            slotCandidates,
            anchorWeekday: row.anchorWeekday,
            laneRank: row.laneRank,
            seedOrder: row.seedOrder,
            telemetryFresh: true,
            spillover: false,
        });
    }
    if (scores.length === 0) return null;
    scores.sort((left, right) => right.baseScore - left.baseScore
        || left.pendingLeases - right.pendingLeases
        || left.seedOrder - right.seedOrder
        || left.slotIndexes[0]! - right.slotIndexes[0]!);
    const affinity = chooseAffinityAccount(scores, request);
    const winner = affinity.winner as ScheduledScoredAccount;
    return {
        selection: {
            accountKey: winner.accountKey,
            slotIndex: chooseScheduledSlot(winner, request),
            slotIndexes: winner.slotIndexes,
            baseScore: winner.baseScore,
            anchorWeekday: winner.anchorWeekday,
            laneRank: winner.laneRank,
            spillover: false,
            telemetryFresh: true,
        },
        fallbackReason: null,
        affinityStatus: affinity.status,
        scores: scores.map(({ slotCandidates, anchorWeekday: _anchorWeekday, laneRank: _laneRank, seedOrder: _seedOrder, telemetryFresh: _telemetryFresh, spillover: _spillover, ...score }) => score),
        decisionReason: affinity.status === 'applied' ? 'optimized_affinity' : 'optimized_burn_rate',
        currentWeekdayUtc: schedule.currentWeekdayUtc,
        activeWeekday,
        activeLaneTelemetryFresh: activeTelemetryFresh,
    };
}

function chooseScheduledSlot(
    score: ScheduledScoredAccount,
    request: CodexBalanceLoaderRequest,
): number {
    return chooseSlot(score.slotCandidates, request.slotPendingLeasesByIndex, request.slotLastSelectedAtByIndex);
}

function scheduledDecisionReason(
    score: ScheduledScoredAccount,
    affinityStatus: CodexBalanceLoaderAffinityStatus,
): CodexBalanceLoaderDecisionReason {
    if (affinityStatus === 'applied') return score.spillover ? 'spillover_affinity' : 'scheduled_affinity';
    if (!score.telemetryFresh) return score.spillover ? 'spillover_non_usage' : 'scheduled_non_usage';
    return score.spillover ? 'spillover_usage_score' : 'scheduled_usage_score';
}

function chooseScheduledAffinityAccount(
    scores: ScheduledScoredAccount[],
    request: CodexBalanceLoaderRequest,
): { status: CodexBalanceLoaderAffinityStatus; winner: ScheduledScoredAccount } {
    const winner = scores[0]!;
    const affinity = request.affinity;
    if (!affinity?.sessionId || !affinity.preferred) return { status: 'not_requested', winner };
    if (!affinity.cacheEligible) return { status: 'cache_hint_missing', winner };
    if (affinity.preferred.provider !== request.provider) return { status: 'provider_mismatch', winner };
    if (winner.accountKey === affinity.preferred.accountKey) return { status: 'best_score', winner };

    const preferred = scores.find((score) => score.accountKey === affinity.preferred!.accountKey);
    if (!preferred) {
        const seen = request.snapshot.accounts.some((account) => account.accountKey === affinity.preferred!.accountKey)
            || request.schedule?.rows.some((row) => row.accountKey === affinity.preferred!.accountKey);
        return { status: seen ? 'preferred_ineligible' : 'preferred_missing', winner };
    }
    if (preferred.laneRank !== winner.laneRank || preferred.anchorWeekday !== winner.anchorWeekday) {
        return { status: 'preferred_ineligible', winner };
    }
    if (preferred.telemetryFresh && !hasHealthyAffinityCandidate(preferred)) {
        return { status: 'preferred_low_headroom', winner };
    }
    if (preferred.telemetryFresh && winner.telemetryFresh && (winner.baseScore - preferred.baseScore) > AFFINITY_SCORE_BAND) {
        return { status: 'score_gap', winner };
    }
    return { status: 'applied', winner: preferred };
}

function selectScheduledCodexBalanceSlot(
    request: CodexBalanceLoaderRequest,
    excludedSlots: Set<number>,
    excludedAccounts: Set<string>,
): CodexBalanceLoaderResult | null {
    const schedule = request.schedule;
    if (!schedule || schedule.rows.length === 0) return null;

    const maxAgeMs = request.persistedMaxAgeMs ?? DEFAULT_PERSISTED_MAX_AGE_MS;
    const accountsByKey = new Map(request.snapshot.accounts.map((account) => [account.accountKey, account]));
    const slotAccountKeys = new Set(
        request.snapshot.slots
            .map((slot) => slot.accountKey)
            .filter((accountKey): accountKey is string => Boolean(accountKey)),
    );
    const rowsByAccountKey = new Map(schedule.rows.map((row) => [row.accountKey, row]));
    const populatedWeekdays = new Set(
        schedule.rows
            .filter((row) => slotAccountKeys.has(row.accountKey))
            .map((row) => row.anchorWeekday),
    );
    const activeWeekday = resolveActiveWeekday(schedule.currentWeekdayUtc, populatedWeekdays);
    const activeTelemetryFresh = activeLaneTelemetryFresh(activeWeekday, request, slotAccountKeys, accountsByKey, maxAgeMs);
    if (activeWeekday === null) return resultWithScheduleMetadata('no_eligible_slot', request, null, false);

    const relevantUnknownAccount = hasRelevantUnknownAccount(request.snapshot.slots, request.now, excludedSlots, excludedAccounts);
    if (relevantUnknownAccount) {
        return resultWithScheduleMetadata('unknown_account', request, activeWeekday, activeTelemetryFresh);
    }

    const slotsByAccount = groupEligibleSlots(request.snapshot.slots, request.now, excludedSlots, excludedAccounts);
    const hasAnyCooldown = request.snapshot.slots.some((slot) => {
        if (!slot.accountKey || !rowsByAccountKey.has(slot.accountKey)) return false;
        return !isExcluded(slot, excludedSlots, excludedAccounts) && slot.rateLimitedUntil > request.now;
    });
    const weekdaySequence = forwardWeekdaySequence(activeWeekday, populatedWeekdays);
    const scores: ScheduledScoredAccount[] = [];
    const relevantAccountKeys = new Set(
        request.snapshot.slots
            .filter((slot) => slot.accountKey && !isExcluded(slot, excludedSlots, excludedAccounts))
            .map((slot) => slot.accountKey as string),
    );
    const relevantRows = schedule.rows.filter((row) => relevantAccountKeys.has(row.accountKey));
    const pendingActivationRows = relevantRows
        .filter((row) => !accountsByKey.has(row.accountKey)
            && !request.snapshot.staleAccountKeys.includes(row.accountKey))
        .sort((left, right) => left.seedOrder - right.seedOrder);
    for (const row of pendingActivationRows) {
        const slotCandidates = slotsByAccount.get(row.accountKey);
        if (!slotCandidates?.length) continue;
        return {
            selection: {
                accountKey: row.accountKey,
                slotIndex: chooseSlot(slotCandidates, request.slotPendingLeasesByIndex, request.slotLastSelectedAtByIndex),
                slotIndexes: slotCandidates.map((slot) => slot.slotIndex).sort((left, right) => left - right),
                baseScore: 0,
                anchorWeekday: row.anchorWeekday,
                laneRank: row.laneRank,
                spillover: row.anchorWeekday !== activeWeekday,
                telemetryFresh: false,
            },
            fallbackReason: null,
            affinityStatus: 'not_requested',
            scores: [],
            decisionReason: row.anchorWeekday === activeWeekday ? 'scheduled_non_usage' : 'spillover_non_usage',
            currentWeekdayUtc: schedule.currentWeekdayUtc,
            activeWeekday,
            activeLaneTelemetryFresh: activeTelemetryFresh,
        };
    }
    const allRelevantAccountsActivated = relevantRows.length > 0
        && relevantRows.every((row) => accountHasFreshWeeklyUsage(accountsByKey.get(row.accountKey), request, maxAgeMs));
    if (allRelevantAccountsActivated) {
        const optimized = selectOptimizedCodexBalanceSlot(
            request,
            schedule,
            rowsByAccountKey,
            accountsByKey,
            slotsByAccount,
            activeWeekday,
            activeTelemetryFresh,
        );
        if (optimized) return optimized;
    }

    for (const weekday of weekdaySequence) {
        const rows = schedule.rows
            .filter((row) => row.anchorWeekday === weekday && slotAccountKeys.has(row.accountKey))
            .sort((left, right) => left.laneRank - right.laneRank || left.seedOrder - right.seedOrder);

        const ranks = Array.from(new Set(rows.map((row) => row.laneRank))).sort((left, right) => left - right);
        for (const laneRank of ranks) {
            const rankScores: ScheduledScoredAccount[] = [];

            for (const row of rows.filter((candidate) => candidate.laneRank === laneRank)) {
                if (excludedAccounts.has(row.accountKey)) continue;
                const slotCandidates = slotsByAccount.get(row.accountKey);
                if (!slotCandidates || slotCandidates.length === 0) continue;

                const account = accountsByKey.get(row.accountKey);
                const telemetryFresh = accountHasFreshWeeklyUsage(account, request, maxAgeMs);
                if (account && isHardExhausted(account)) continue;

                const pendingLeases = request.pendingLeasesByAccountKey?.[row.accountKey] ?? 0;
                const baseScore = telemetryFresh ? (getScore(account, pendingLeases, request.now) ?? 0) : 0;
                if (telemetryFresh && account.source === 'cooldown') continue;

                rankScores.push({
                    accountKey: row.accountKey,
                    slotIndexes: slotCandidates.map((slot) => slot.slotIndex).sort((left, right) => left - right),
                    baseScore,
                    source: account?.source ?? 'persisted',
                    pendingLeases,
                    bottleneckResidual: account
                        ? Math.min(getResidual(account.fiveHour?.usedPercent) ?? 1, getResidual(account.weekly?.usedPercent) ?? 1)
                        : null,
                    fiveHourResidual: account ? getResidual(account.fiveHour?.usedPercent) : null,
                    weeklyResidual: account ? getResidual(account.weekly?.usedPercent) : null,
                    slotCandidates,
                    anchorWeekday: row.anchorWeekday,
                    laneRank: row.laneRank,
                    seedOrder: row.seedOrder,
                    telemetryFresh,
                    spillover: weekday !== activeWeekday,
                });
            }

            if (rankScores.length === 0) continue;
            rankScores.sort((left, right) => {
                if (left.telemetryFresh !== right.telemetryFresh) return Number(right.telemetryFresh) - Number(left.telemetryFresh);
                if (left.telemetryFresh && right.telemetryFresh && left.baseScore !== right.baseScore) return right.baseScore - left.baseScore;
                const leftPending = left.slotCandidates.reduce((sum, slot) => sum + (request.slotPendingLeasesByIndex?.[slot.slotIndex] ?? 0), 0);
                const rightPending = right.slotCandidates.reduce((sum, slot) => sum + (request.slotPendingLeasesByIndex?.[slot.slotIndex] ?? 0), 0);
                if (leftPending !== rightPending) return leftPending - rightPending;
                return left.seedOrder - right.seedOrder || left.slotIndexes[0]! - right.slotIndexes[0]!;
            });

            const affinity = chooseScheduledAffinityAccount(rankScores, request);
            const slotIndex = chooseScheduledSlot(affinity.winner, request);
            scores.push(...rankScores);
            return {
                selection: {
                    accountKey: affinity.winner.accountKey,
                    slotIndex,
                    slotIndexes: affinity.winner.slotIndexes,
                    baseScore: affinity.winner.baseScore,
                    anchorWeekday: affinity.winner.anchorWeekday,
                    laneRank: affinity.winner.laneRank,
                    spillover: affinity.winner.spillover,
                    telemetryFresh: affinity.winner.telemetryFresh,
                },
                fallbackReason: null,
                affinityStatus: affinity.status,
                scores: scores.map(({ slotCandidates, anchorWeekday: _anchorWeekday, laneRank: _laneRank, seedOrder: _seedOrder, telemetryFresh: _telemetryFresh, spillover: _spillover, ...score }) => score),
                decisionReason: scheduledDecisionReason(affinity.winner, affinity.status),
                currentWeekdayUtc: schedule.currentWeekdayUtc,
                activeWeekday,
                activeLaneTelemetryFresh: activeTelemetryFresh,
            };
        }
    }

    return resultWithScheduleMetadata(hasAnyCooldown ? 'cooldown_only' : 'no_eligible_slot', request, activeWeekday, activeTelemetryFresh);
}

export function selectCodexBalanceSlot(request: CodexBalanceLoaderRequest): CodexBalanceLoaderResult {
    const excludedSlots = toSet(request.excludedSlotIndexes);
    const excludedAccounts = toSet(request.excludedAccountKeys);
    const scheduledResult = selectScheduledCodexBalanceSlot(request, excludedSlots, excludedAccounts);
    if (scheduledResult) return scheduledResult;

    const relevantSlotIndexes = getRelevantSlotIndexes(request.snapshot.slots, request.now, excludedSlots, excludedAccounts);
    if (relevantSlotIndexes.length === 0) {
        const hasCooldown = request.snapshot.slots.some((slot) => !isExcluded(slot, excludedSlots, excludedAccounts) && slot.rateLimitedUntil > request.now);
        return emptyResult(hasCooldown ? 'cooldown_only' : 'no_eligible_slot');
    }
    if (hasRelevantUnknownAccount(request.snapshot.slots, request.now, excludedSlots, excludedAccounts)) return emptyResult('unknown_account');
    if (request.snapshot.missingUsageSlotIndexes.some((slotIndex) => relevantSlotIndexes.includes(slotIndex))) return emptyResult('missing_usage');

    const maxAgeMs = request.persistedMaxAgeMs ?? DEFAULT_PERSISTED_MAX_AGE_MS;
    const eligibleSlots = groupEligibleSlots(request.snapshot.slots, request.now, excludedSlots, excludedAccounts);
    const hasStaleAccount = request.snapshot.staleAccountKeys.some((accountKey) => eligibleSlots.has(accountKey))
        || request.snapshot.accounts.some((account) => eligibleSlots.has(account.accountKey) && isPersistedStale(account, request.now, maxAgeMs));
    if (hasStaleAccount) return emptyResult('stale_usage');

    const scores = scoreAccounts(request, eligibleSlots);
    if (scores.length === 0) return emptyResult('missing_usage');

    const affinity = chooseAffinityAccount(scores, request);
    const slotIndex = chooseSlot(affinity.winner.slotCandidates, request.slotPendingLeasesByIndex, request.slotLastSelectedAtByIndex);
    return {
        selection: {
            accountKey: affinity.winner.accountKey,
            slotIndex,
            slotIndexes: affinity.winner.slotIndexes,
            baseScore: affinity.winner.baseScore,
        },
        fallbackReason: null,
        affinityStatus: affinity.status,
        scores: scores.map(({ slotCandidates, ...score }) => score),
    };
}

export function selectCodexBalanceCandidate(request: LegacyBalanceLoaderRequest): {
    fallbackReason: CodexBalanceLoaderResult['fallbackReason'];
    selectedAccountKey: string | null;
    selectedSlotIndex: number | null;
    affinityApplied: boolean;
    affinityStatus: CodexBalanceLoaderAffinityStatus;
    scores: CodexBalanceLoaderAccountScore[];
    decisionReason?: CodexBalanceLoaderResult['decisionReason'];
    currentWeekdayUtc?: number;
    activeWeekday?: number | null;
    activeLaneTelemetryFresh?: boolean;
} {
    const pendingLeasesByAccountKey = request.slots.reduce<Record<string, number>>((accumulator, slot) => {
        accumulator[slot.accountKey] = (accumulator[slot.accountKey] ?? 0) + (slot.pendingLeases ?? 0);
        return accumulator;
    }, {});
    const slotPendingLeasesByIndex = request.slots.reduce<Record<number, number>>((accumulator, slot) => {
        accumulator[slot.slotIndex] = slot.pendingLeases ?? 0;
        return accumulator;
    }, {});
    const slotLastSelectedAtByIndex = request.slots.reduce<Record<number, number>>((accumulator, slot) => {
        const parsed = slot.lastSelectedAt ? Date.parse(slot.lastSelectedAt) : 0;
        accumulator[slot.slotIndex] = Number.isFinite(parsed) ? parsed : 0;
        return accumulator;
    }, {});

    const result = selectCodexBalanceSlot({
        now: request.now,
        provider: request.provider,
        snapshot: {
            slots: request.slots.map((slot) => ({
                slotIndex: slot.slotIndex,
                slotPath: null,
                accountKey: slot.accountKey,
                rateLimitedUntil: slot.rateLimitedUntil ?? 0,
                authAvailable: true,
                authUnavailableReason: null,
                authRetryAt: null,
            })),
            accounts: request.accounts,
            unknownAccountSlotIndexes: [],
            missingUsageSlotIndexes: [],
            staleAccountKeys: [],
            triggeredBackgroundRefresh: false,
        },
        excludedSlotIndexes: request.excludedSlotIndexes,
        excludedAccountKeys: request.excludedAccountKeys,
        pendingLeasesByAccountKey,
        slotPendingLeasesByIndex,
        slotLastSelectedAtByIndex,
        affinity: request.sessionAffinity
            ? {
                sessionId: request.sessionAffinity.sessionId,
                cacheEligible: request.sessionAffinity.cacheEligible,
                preferred: request.sessionAffinity.preferredAccountKey
                    ? {
                        provider: request.sessionAffinity.preferredProvider,
                        accountKey: request.sessionAffinity.preferredAccountKey,
                    }
                    : null,
            }
            : null,
        schedule: request.schedule,
    });

    return {
        fallbackReason: result.fallbackReason,
        selectedAccountKey: result.selection?.accountKey ?? null,
        selectedSlotIndex: result.selection?.slotIndex ?? null,
        affinityApplied: result.affinityStatus === 'applied',
        affinityStatus: result.affinityStatus,
        scores: result.scores,
        decisionReason: result.decisionReason,
        currentWeekdayUtc: result.currentWeekdayUtc,
        activeWeekday: result.activeWeekday,
        activeLaneTelemetryFresh: result.activeLaneTelemetryFresh,
    };
}
