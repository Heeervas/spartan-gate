import { afterEach, describe, expect, it, vi } from 'vitest';
import { buildCodexBalancerState, resolveCodexActivatedSlots } from '../src/codex-balancer.js';

const MONDAY = Date.parse('2026-06-08T10:00:00.000Z');

function slot(slotIndex: number, accountKey: string) {
    return {
        slotIndex,
        slotPath: `/slot-${slotIndex}/auth.json`,
        accountKey,
        rateLimitedUntil: 0,
        authAvailable: true,
        authUnavailableReason: null,
        authRetryAt: null,
    };
}

function schedule(slotIndex: number, accountKey: string, anchorWeekday: number) {
    return {
        slotIndex,
        accountKey,
        seedOrder: slotIndex,
        anchorWeekday,
        laneRank: 0,
        updatedAt: new Date(MONDAY).toISOString(),
    };
}

function account(slotIndex: number, accountKey: string, weeklyUsedPercent: number, fiveHourUsedPercent = 20) {
    const updatedAt = new Date(MONDAY).toISOString();
    return {
        accountKey,
        slotIndex,
        slotIndexes: [slotIndex],
        slotPaths: [`/slot-${slotIndex}/auth.json`],
        source: 'live' as const,
        stale: false,
        cooldownUntil: null,
        lastFetchedAt: updatedAt,
        updatedAt,
        fiveHour: {
            window: 'fiveHour' as const,
            usedPercent: fiveHourUsedPercent,
            resetAt: new Date(MONDAY + 3 * 60 * 60_000).toISOString(),
            windowMinutes: 300,
            updatedAt,
        },
        weekly: {
            window: 'weekly' as const,
            usedPercent: weeklyUsedPercent,
            resetAt: new Date(MONDAY + 6 * 24 * 60 * 60_000).toISOString(),
            windowMinutes: 10_080,
            updatedAt,
        },
    };
}

afterEach(() => {
    vi.unstubAllEnvs();
});

describe('Codex activation scheduler', () => {
    it('keeps an expired account dormant until its next scheduled day while slot zero stays active', () => {
        const slots = [slot(0, 'acct-0'), slot(1, 'acct-1')];
        const scheduleRows = slots.map((item) => schedule(item.slotIndex, item.accountKey!, item.slotIndex + 1));
        const checkpoints = [
            {
                slotIndex: 0,
                accountKey: 'acct-0',
                expectedWeeklyResetAt: '2026-06-07T12:00:00.000Z',
                lastUsageCheckAt: '2026-05-31T12:00:00.000Z',
                updatedAt: '2026-05-31T12:00:00.000Z',
            },
            {
                slotIndex: 1,
                accountKey: 'acct-1',
                expectedWeeklyResetAt: '2026-06-07T12:00:00.000Z',
                lastUsageCheckAt: '2026-05-31T12:00:00.000Z',
                updatedAt: '2026-05-31T12:00:00.000Z',
            },
        ];

        const monday = resolveCodexActivatedSlots({
            now: MONDAY,
            startWeekday: 1,
            slots,
            scheduleRows,
            accounts: [],
            checkpoints,
        });
        const tuesday = resolveCodexActivatedSlots({
            now: Date.parse('2026-06-09T00:01:00.000Z'),
            startWeekday: 1,
            slots,
            scheduleRows,
            accounts: [],
            checkpoints,
        });

        expect([...monday.activatedSlotIndexes]).toEqual([0]);
        expect([...tuesday.activatedSlotIndexes]).toEqual([0, 1]);
    });

    it('keeps a slot active until its expected weekly reset', () => {
        const slots = [slot(0, 'acct-0'), slot(1, 'acct-1')];

        const result = resolveCodexActivatedSlots({
            now: MONDAY,
            startWeekday: 1,
            slots,
            scheduleRows: slots.map((item) => schedule(item.slotIndex, item.accountKey!, item.slotIndex + 1)),
            accounts: [],
            checkpoints: [{
                slotIndex: 1,
                accountKey: 'acct-1',
                expectedWeeklyResetAt: '2026-06-10T12:00:00.000Z',
                lastUsageCheckAt: '2026-06-03T12:00:00.000Z',
                updatedAt: '2026-06-03T12:00:00.000Z',
            }],
        });

        expect([...result.activatedSlotIndexes]).toEqual([0, 1]);
    });

    it('allows activation when reset and scheduled weekday begin at the same instant', () => {
        const slots = [slot(0, 'acct-0'), slot(1, 'acct-1')];
        const resetAt = '2026-06-09T00:00:00.000Z';

        const result = resolveCodexActivatedSlots({
            now: Date.parse(resetAt),
            startWeekday: 1,
            slots,
            scheduleRows: slots.map((item) => schedule(item.slotIndex, item.accountKey!, item.slotIndex + 1)),
            accounts: [],
            checkpoints: [{
                slotIndex: 1,
                accountKey: 'acct-1',
                expectedWeeklyResetAt: resetAt,
                lastUsageCheckAt: '2026-06-02T00:00:00.000Z',
                updatedAt: '2026-06-02T00:00:00.000Z',
            }],
        });

        expect([...result.activatedSlotIndexes]).toEqual([0, 1]);
    });

    it('activates only the Monday slot when the weekly threshold is not reached', () => {
        vi.stubEnv('CODEX_EARLY_ACTIVATION_ENABLED', 'true');
        vi.stubEnv('CODEX_EARLY_ACTIVATION_WEEKLY_PERCENT', '70');
        const slots = [slot(0, 'acct-0'), slot(1, 'acct-1'), slot(2, 'acct-2')];

        const result = resolveCodexActivatedSlots({
            now: MONDAY,
            startWeekday: 1,
            slots,
            scheduleRows: slots.map((item) => schedule(item.slotIndex, item.accountKey!, item.slotIndex + 1)),
            accounts: [account(0, 'acct-0', 69)],
        });

        expect([...result.activatedSlotIndexes]).toEqual([0]);
    });

    it('activates exactly the next slot when all active slots reach the threshold', () => {
        vi.stubEnv('CODEX_EARLY_ACTIVATION_ENABLED', 'true');
        vi.stubEnv('CODEX_EARLY_ACTIVATION_WEEKLY_PERCENT', '70');
        const slots = [slot(0, 'acct-0'), slot(1, 'acct-1'), slot(2, 'acct-2')];

        const result = resolveCodexActivatedSlots({
            now: MONDAY,
            startWeekday: 1,
            slots,
            scheduleRows: slots.map((item) => schedule(item.slotIndex, item.accountKey!, item.slotIndex + 1)),
            accounts: [account(0, 'acct-0', 70)],
        });

        expect([...result.activatedSlotIndexes]).toEqual([0, 1]);
        expect(result.reasons.get(1)).toBe('early');
        expect(result.activatedSlotIndexes.has(2)).toBe(false);
    });

    it('marks only weekly exhaustion as hard exhausted in balancer state', () => {
        vi.stubEnv('CODEX_EARLY_ACTIVATION_ENABLED', 'false');
        const slots = [slot(0, 'acct-5h'), slot(1, 'acct-weekly')];

        const state = buildCodexBalancerState({
            now: MONDAY,
            startWeekday: 1,
            slots,
            scheduleRows: slots.map((item) => schedule(item.slotIndex, item.accountKey!, item.slotIndex + 1)),
            accounts: [
                account(0, 'acct-5h', 20, 100),
                account(1, 'acct-weekly', 100, 20),
            ],
        });

        expect(state.slots).toEqual([
            expect.objectContaining({ slotIndex: 0, exhausted: false, fiveHourUsedPercent: 100, weeklyUsedPercent: 20 }),
            expect.objectContaining({ slotIndex: 1, exhausted: true, fiveHourUsedPercent: 20, weeklyUsedPercent: 100 }),
        ]);
    });
});
