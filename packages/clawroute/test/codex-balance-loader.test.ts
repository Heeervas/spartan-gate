import { describe, expect, it } from 'vitest';

const NOW = Date.parse('2026-05-03T10:00:00.000Z');
const UPDATED_AT = new Date(NOW).toISOString();

async function loadSubject() {
    return import('../src/' + 'codex-balance-loader.js');
}

function futureIso(hoursFromNow: number): string {
    return new Date(NOW + hoursFromNow * 3_600_000).toISOString();
}

function makeWindow(window: 'fiveHour' | 'weekly', usedPercent: number, resetAt: string) {
    return {
        window,
        usedPercent,
        resetAt,
        updatedAt: UPDATED_AT,
        windowMinutes: window === 'fiveHour' ? 300 : 10_080,
    };
}

function makeAccount(accountKey: string, options: {
    slotIndexes: number[];
    fiveHour?: number | null;
    weekly?: number | null;
    fiveHourResetHours?: number;
    weeklyResetHours?: number;
    source?: 'live' | 'cache' | 'persisted';
    stale?: boolean;
    updatedAt?: string;
}) {
    const updatedAt = options.updatedAt ?? UPDATED_AT;
    return {
        accountKey,
        slotIndex: options.slotIndexes[0] ?? 0,
        slotIndexes: options.slotIndexes,
        slotPaths: options.slotIndexes.map((slotIndex) => `/.codex/auth-${slotIndex}.json`),
        source: options.source ?? 'live',
        stale: options.stale ?? false,
        cooldownUntil: null,
        lastFetchedAt: updatedAt,
        updatedAt,
        fiveHour: options.fiveHour === null
            ? null
            : {
                ...makeWindow('fiveHour', options.fiveHour ?? 30, futureIso(options.fiveHourResetHours ?? 1)),
                updatedAt,
            },
        weekly: options.weekly === null
            ? null
            : {
                ...makeWindow('weekly', options.weekly ?? 30, futureIso(options.weeklyResetHours ?? 72)),
                updatedAt,
            },
    };
}

function makeSlot(slotIndex: number, accountKey: string, overrides: Record<string, unknown> = {}) {
    return {
        slotIndex,
        accountKey,
        pendingLeases: 0,
        lastSelectedAt: new Date(NOW - slotIndex * 60_000).toISOString(),
        rateLimitedUntil: 0,
        ...overrides,
    };
}

function makeScheduleRow(accountKey: string, seedOrder: number, anchorWeekday: number, laneRank = 0) {
    return {
        accountKey,
        seedOrder,
        anchorWeekday,
        laneRank,
        updatedAt: UPDATED_AT,
    };
}

describe('Codex balance loader', () => {
    it('ranks balanced accounts above skewed ones and picks the least-loaded duplicate slot', async () => {
        const { selectCodexBalanceCandidate } = await loadSubject();
        const result = selectCodexBalanceCandidate({
            now: NOW,
            provider: 'codex',
            accounts: [
                makeAccount('acct-skewed-short', { slotIndexes: [0], fiveHour: 5, weekly: 80 }),
                makeAccount('acct-balanced', { slotIndexes: [1, 3], fiveHour: 30, weekly: 30 }),
                makeAccount('acct-skewed-weekly', { slotIndexes: [2], fiveHour: 80, weekly: 5 }),
            ],
            slots: [
                makeSlot(0, 'acct-skewed-short'),
                makeSlot(1, 'acct-balanced', { pendingLeases: 1, lastSelectedAt: new Date(NOW - 60_000).toISOString() }),
                makeSlot(2, 'acct-skewed-weekly'),
                makeSlot(3, 'acct-balanced', { pendingLeases: 0, lastSelectedAt: new Date(NOW - 600_000).toISOString() }),
            ],
            excludedSlotIndexes: new Set<number>(),
            sessionAffinity: null,
        });

        expect(result).toMatchObject({
            fallbackReason: null,
            selectedAccountKey: 'acct-balanced',
            selectedSlotIndex: 3,
            affinityApplied: false,
        });
    });

    it('lets remembered affinity win only inside the 0.05 score band', async () => {
        const { selectCodexBalanceCandidate } = await loadSubject();
        const result = selectCodexBalanceCandidate({
            now: NOW,
            provider: 'codex',
            accounts: [
                makeAccount('acct-preferred', { slotIndexes: [0], fiveHour: 35, weekly: 35, fiveHourResetHours: 1 }),
                makeAccount('acct-slightly-better', { slotIndexes: [1], fiveHour: 32, weekly: 34, fiveHourResetHours: 1 }),
            ],
            slots: [makeSlot(0, 'acct-preferred'), makeSlot(1, 'acct-slightly-better')],
            excludedSlotIndexes: new Set<number>(),
            sessionAffinity: {
                sessionId: 'session-1',
                cacheEligible: true,
                preferredProvider: 'codex',
                preferredAccountKey: 'acct-preferred',
            },
        });

        expect(result).toMatchObject({
            fallbackReason: null,
            selectedAccountKey: 'acct-preferred',
            selectedSlotIndex: 0,
            affinityApplied: true,
        });
    });

    it('drops remembered affinity when the preferred account falls below the safety floor', async () => {
        const { selectCodexBalanceCandidate } = await loadSubject();
        const result = selectCodexBalanceCandidate({
            now: NOW,
            provider: 'codex',
            accounts: [
                makeAccount('acct-exhausted', { slotIndexes: [0], fiveHour: 85, weekly: 81, fiveHourResetHours: 1 }),
                makeAccount('acct-healthy', { slotIndexes: [1], fiveHour: 42, weekly: 43, fiveHourResetHours: 1 }),
            ],
            slots: [makeSlot(0, 'acct-exhausted'), makeSlot(1, 'acct-healthy')],
            excludedSlotIndexes: new Set<number>(),
            sessionAffinity: {
                sessionId: 'session-2',
                cacheEligible: true,
                preferredProvider: 'codex',
                preferredAccountKey: 'acct-exhausted',
            },
        });

        expect(result).toMatchObject({
            fallbackReason: null,
            selectedAccountKey: 'acct-healthy',
            selectedSlotIndex: 1,
            affinityApplied: false,
        });
    });

    it('drops remembered affinity when lease pressure pushes another healthy account ahead', async () => {
        const { selectCodexBalanceCandidate } = await loadSubject();
        const result = selectCodexBalanceCandidate({
            now: NOW,
            provider: 'codex',
            accounts: [
                makeAccount('acct-preferred', { slotIndexes: [0], fiveHour: 34, weekly: 36, fiveHourResetHours: 1 }),
                makeAccount('acct-healthier', { slotIndexes: [1], fiveHour: 33, weekly: 35, fiveHourResetHours: 1 }),
            ],
            slots: [
                makeSlot(0, 'acct-preferred', { pendingLeases: 1 }),
                makeSlot(1, 'acct-healthier', { pendingLeases: 0 }),
            ],
            excludedSlotIndexes: new Set<number>(),
            sessionAffinity: {
                sessionId: 'session-3',
                cacheEligible: true,
                preferredProvider: 'codex',
                preferredAccountKey: 'acct-preferred',
            },
        });

        expect(result).toMatchObject({
            fallbackReason: null,
            selectedAccountKey: 'acct-healthier',
            selectedSlotIndex: 1,
            affinityApplied: false,
        });
    });

    it('treats a missing session like quota-only selection even when a preferred account is supplied', async () => {
        const { selectCodexBalanceCandidate } = await loadSubject();
        const result = selectCodexBalanceCandidate({
            now: NOW,
            provider: 'codex',
            accounts: [
                makeAccount('acct-preferred', { slotIndexes: [0], fiveHour: 36, weekly: 36, fiveHourResetHours: 1 }),
                makeAccount('acct-best', { slotIndexes: [1], fiveHour: 25, weekly: 26, fiveHourResetHours: 1 }),
            ],
            slots: [makeSlot(0, 'acct-preferred'), makeSlot(1, 'acct-best')],
            excludedSlotIndexes: new Set<number>(),
            sessionAffinity: {
                sessionId: null,
                cacheEligible: true,
                preferredProvider: 'codex',
                preferredAccountKey: 'acct-preferred',
            },
        });

        expect(result).toMatchObject({
            fallbackReason: null,
            selectedAccountKey: 'acct-best',
            selectedSlotIndex: 1,
            affinityApplied: false,
        });
    });

    it('uses alternate scheduled days only while an account still needs activation telemetry', async () => {
        const { selectCodexBalanceCandidate } = await loadSubject();
        const result = selectCodexBalanceCandidate({
            now: NOW,
            provider: 'codex',
            accounts: [
                makeAccount('acct-active', {
                    slotIndexes: [0],
                    fiveHour: 80,
                    weekly: 80,
                    weeklyResetHours: -1,
                }),
                makeAccount('acct-offday', { slotIndexes: [1], fiveHour: 5, weekly: 5 }),
            ],
            slots: [makeSlot(0, 'acct-active'), makeSlot(1, 'acct-offday')],
            excludedSlotIndexes: new Set<number>(),
            sessionAffinity: null,
            schedule: {
                currentWeekdayUtc: 1,
                rows: [
                    makeScheduleRow('acct-active', 0, 1),
                    makeScheduleRow('acct-offday', 1, 2),
                ],
            },
        });

        expect(result).toMatchObject({
            fallbackReason: null,
            selectedAccountKey: 'acct-active',
            selectedSlotIndex: 0,
            decisionReason: 'scheduled_non_usage',
            activeWeekday: 1,
        });
    });

    it('leaves the activation schedule once all relevant accounts have fresh weekly telemetry', async () => {
        const { selectCodexBalanceCandidate } = await loadSubject();
        const result = selectCodexBalanceCandidate({
            now: NOW,
            provider: 'codex',
            accounts: [
                makeAccount('acct-anchor', { slotIndexes: [0], fiveHour: 30, weekly: 10, weeklyResetHours: 144 }),
                makeAccount('acct-urgent', { slotIndexes: [1], fiveHour: 30, weekly: 75, weeklyResetHours: 24 }),
            ],
            slots: [makeSlot(0, 'acct-anchor'), makeSlot(1, 'acct-urgent')],
            excludedSlotIndexes: new Set<number>(),
            sessionAffinity: null,
            schedule: {
                currentWeekdayUtc: 1,
                rows: [
                    makeScheduleRow('acct-anchor', 0, 1),
                    makeScheduleRow('acct-urgent', 1, 2),
                ],
            },
        });

        expect(result).toMatchObject({
            fallbackReason: null,
            selectedAccountKey: 'acct-urgent',
            decisionReason: 'optimized_burn_rate',
        });
    });

    it('prioritizes an account on day 6 over one on day 1 of its weekly cycle', async () => {
        const { selectCodexBalanceCandidate } = await loadSubject();
        const result = selectCodexBalanceCandidate({
            now: NOW,
            provider: 'codex',
            accounts: [
                makeAccount('acct-day-1', { slotIndexes: [0], fiveHour: 20, weekly: 20, weeklyResetHours: 144 }),
                makeAccount('acct-day-6', { slotIndexes: [1], fiveHour: 20, weekly: 80, weeklyResetHours: 24 }),
            ],
            slots: [makeSlot(0, 'acct-day-1'), makeSlot(1, 'acct-day-6')],
            excludedSlotIndexes: new Set<number>(),
            sessionAffinity: null,
            schedule: {
                currentWeekdayUtc: 1,
                rows: [makeScheduleRow('acct-day-1', 0, 1), makeScheduleRow('acct-day-6', 1, 2)],
            },
        });

        expect(result.selectedAccountKey).toBe('acct-day-6');
    });

    it('prioritizes the account furthest behind its required weekly burn rate', async () => {
        const { selectCodexBalanceCandidate } = await loadSubject();
        const result = selectCodexBalanceCandidate({
            now: NOW,
            provider: 'codex',
            accounts: [
                makeAccount('acct-on-track', { slotIndexes: [0], fiveHour: 20, weekly: 60, weeklyResetHours: 72 }),
                makeAccount('acct-behind', { slotIndexes: [1], fiveHour: 20, weekly: 25, weeklyResetHours: 72 }),
            ],
            slots: [makeSlot(0, 'acct-on-track'), makeSlot(1, 'acct-behind')],
            excludedSlotIndexes: new Set<number>(),
            sessionAffinity: null,
            schedule: {
                currentWeekdayUtc: 1,
                rows: [makeScheduleRow('acct-on-track', 0, 1), makeScheduleRow('acct-behind', 1, 2)],
            },
        });

        expect(result.selectedAccountKey).toBe('acct-behind');
    });

    it('does not select an urgent weekly account when its five-hour window is the bottleneck', async () => {
        const { selectCodexBalanceCandidate } = await loadSubject();
        const result = selectCodexBalanceCandidate({
            now: NOW,
            provider: 'codex',
            accounts: [
                makeAccount('acct-weekly-urgent', {
                    slotIndexes: [0],
                    fiveHour: 99,
                    weekly: 99,
                    fiveHourResetHours: 4,
                    weeklyResetHours: 1,
                }),
                makeAccount('acct-runnable', {
                    slotIndexes: [1],
                    fiveHour: 20,
                    weekly: 60,
                    fiveHourResetHours: 4,
                    weeklyResetHours: 48,
                }),
            ],
            slots: [makeSlot(0, 'acct-weekly-urgent'), makeSlot(1, 'acct-runnable')],
            excludedSlotIndexes: new Set<number>(),
            sessionAffinity: null,
            schedule: {
                currentWeekdayUtc: 1,
                rows: [makeScheduleRow('acct-weekly-urgent', 0, 1), makeScheduleRow('acct-runnable', 1, 2)],
            },
        });

        expect(result.selectedAccountKey).toBe('acct-runnable');
    });

    it('returns to safe scheduled activation when weekly telemetry becomes stale', async () => {
        const { selectCodexBalanceCandidate } = await loadSubject();
        const staleUpdatedAt = new Date(NOW - 16 * 60_000).toISOString();
        const result = selectCodexBalanceCandidate({
            now: NOW,
            provider: 'codex',
            accounts: [
                makeAccount('acct-active-day', {
                    slotIndexes: [0],
                    fiveHour: 30,
                    weekly: 30,
                    updatedAt: staleUpdatedAt,
                }),
                makeAccount('acct-offday', { slotIndexes: [1], fiveHour: 10, weekly: 10 }),
            ],
            slots: [makeSlot(0, 'acct-active-day'), makeSlot(1, 'acct-offday')],
            excludedSlotIndexes: new Set<number>(),
            sessionAffinity: null,
            schedule: {
                currentWeekdayUtc: 1,
                rows: [
                    makeScheduleRow('acct-active-day', 0, 1),
                    makeScheduleRow('acct-offday', 1, 2),
                ],
            },
        });

        expect(result).toMatchObject({
            selectedAccountKey: 'acct-active-day',
            decisionReason: 'scheduled_non_usage',
            activeWeekday: 1,
        });
    });

    it('selects inside the active lane when usage telemetry is missing', async () => {
        const { selectCodexBalanceCandidate } = await loadSubject();
        const result = selectCodexBalanceCandidate({
            now: NOW,
            provider: 'codex',
            accounts: [
                makeAccount('acct-offday', { slotIndexes: [1], fiveHour: 5, weekly: 5 }),
            ],
            slots: [makeSlot(0, 'acct-active'), makeSlot(1, 'acct-offday')],
            excludedSlotIndexes: new Set<number>(),
            sessionAffinity: null,
            schedule: {
                currentWeekdayUtc: 1,
                rows: [
                    makeScheduleRow('acct-active', 0, 1),
                    makeScheduleRow('acct-offday', 1, 2),
                ],
            },
        });

        expect(result).toMatchObject({
            fallbackReason: null,
            selectedAccountKey: 'acct-active',
            selectedSlotIndex: 0,
            decisionReason: 'scheduled_non_usage',
            activeLaneTelemetryFresh: false,
        });
    });

    it('reports the active weekday without restricting optimized selection to that lane', async () => {
        const { selectCodexBalanceCandidate } = await loadSubject();
        const result = selectCodexBalanceCandidate({
            now: NOW,
            provider: 'codex',
            accounts: [
                makeAccount('acct-mon', { slotIndexes: [0], fiveHour: 10, weekly: 10 }),
                makeAccount('acct-wed', { slotIndexes: [1], fiveHour: 70, weekly: 70 }),
            ],
            slots: [makeSlot(0, 'acct-mon'), makeSlot(1, 'acct-wed')],
            excludedSlotIndexes: new Set<number>(),
            sessionAffinity: null,
            schedule: {
                currentWeekdayUtc: 6,
                rows: [
                    makeScheduleRow('acct-mon', 0, 1),
                    makeScheduleRow('acct-wed', 1, 3),
                ],
            },
        });

        expect(result).toMatchObject({
            fallbackReason: null,
            selectedAccountKey: 'acct-mon',
            activeWeekday: 3,
            decisionReason: 'optimized_burn_rate',
        });
    });

    it('keeps 5h-exhausted accounts eligible because only weekly exhaustion is hard exhaustion', async () => {
        const { selectCodexBalanceCandidate } = await loadSubject();
        const result = selectCodexBalanceCandidate({
            now: NOW,
            provider: 'codex',
            accounts: [
                makeAccount('acct-5h-exhausted', { slotIndexes: [0], fiveHour: 100, weekly: 20 }),
            ],
            slots: [makeSlot(0, 'acct-5h-exhausted')],
            excludedSlotIndexes: new Set<number>(),
            sessionAffinity: null,
        });

        expect(result).toMatchObject({
            fallbackReason: null,
            selectedAccountKey: 'acct-5h-exhausted',
            selectedSlotIndex: 0,
        });
    });

    it('ignores lane rank after activation and skips weekly-exhausted accounts', async () => {
        const { selectCodexBalanceCandidate } = await loadSubject();
        const result = selectCodexBalanceCandidate({
            now: NOW,
            provider: 'codex',
            accounts: [
                makeAccount('acct-rank-0', { slotIndexes: [0], fiveHour: 20, weekly: 100 }),
                makeAccount('acct-rank-1', { slotIndexes: [1], fiveHour: 60, weekly: 60 }),
                makeAccount('acct-next-day', { slotIndexes: [2], fiveHour: 1, weekly: 1 }),
            ],
            slots: [
                makeSlot(0, 'acct-rank-0'),
                makeSlot(1, 'acct-rank-1'),
                makeSlot(2, 'acct-next-day'),
            ],
            excludedSlotIndexes: new Set<number>(),
            sessionAffinity: null,
            schedule: {
                currentWeekdayUtc: 1,
                rows: [
                    makeScheduleRow('acct-rank-0', 0, 1, 0),
                    makeScheduleRow('acct-rank-1', 7, 1, 1),
                    makeScheduleRow('acct-next-day', 1, 2, 0),
                ],
            },
        });

        expect(result).toMatchObject({
            fallbackReason: null,
            selectedAccountKey: 'acct-next-day',
            selectedSlotIndex: 2,
            activeWeekday: 1,
            decisionReason: 'optimized_burn_rate',
        });
    });

    it('allows safe affinity across anchor days after every account is activated', async () => {
        const { selectCodexBalanceCandidate } = await loadSubject();
        const result = selectCodexBalanceCandidate({
            now: NOW,
            provider: 'codex',
            accounts: [
                makeAccount('acct-active', { slotIndexes: [0], fiveHour: 50, weekly: 50 }),
                makeAccount('acct-offday', { slotIndexes: [1], fiveHour: 50, weekly: 50 }),
            ],
            slots: [makeSlot(0, 'acct-active'), makeSlot(1, 'acct-offday')],
            excludedSlotIndexes: new Set<number>(),
            sessionAffinity: {
                sessionId: 'session-offday',
                cacheEligible: true,
                preferredProvider: 'codex',
                preferredAccountKey: 'acct-offday',
            },
            schedule: {
                currentWeekdayUtc: 1,
                rows: [
                    makeScheduleRow('acct-active', 0, 1),
                    makeScheduleRow('acct-offday', 1, 2),
                ],
            },
        });

        expect(result).toMatchObject({
            fallbackReason: null,
            selectedAccountKey: 'acct-offday',
            affinityApplied: true,
            affinityStatus: 'applied',
            decisionReason: 'optimized_affinity',
        });
    });
});
