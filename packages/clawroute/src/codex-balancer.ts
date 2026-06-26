import {
    clearCodexBalancerOverrides,
    getCodexActivationCheckpoints,
    getCodexAccountCapacityMultiplier,
    getCodexBalancerAudit,
    getCodexBalancerSettings,
    getCodexBalancerSlotOverrides,
    getLastCodexBalancerDecision,
    logCodexBalancerAudit,
    setCodexBalancerSettings,
    setCodexActivationCheckpoint,
    setCodexBalancerSlotOverride,
} from './logger.js';
import {
    CodexAccountScheduleRow,
    CodexActivationCheckpoint,
    CodexBalancerSettings,
    CodexBalancerSlotOverride,
    CodexBalancerSlotState,
    CodexBalancerState,
    CodexUsageAccountRow,
    CodexUsageSelectorSlotIdentity,
} from './types.js';

const WEEKDAY_NAMES = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
const DEFAULT_EARLY_ACTIVATION_PERCENT = 70;
const DEFAULT_COLD_MIGRATION_FIVE_HOUR_THRESHOLD_PERCENT = 7;
const FRESH_USAGE_MAX_AGE_MS = 15 * 60_000;

function envMode(): CodexBalancerSettings['mode'] {
    const value = (process.env['CODEX_BALANCE_LOADER_MODE'] ?? 'off').trim().toLowerCase();
    return value === 'on' || value === 'shadow' ? value : 'off';
}

function envBoolean(name: string, fallback: boolean): boolean {
    const value = process.env[name];
    if (value === undefined) return fallback;
    return !['0', 'false', 'off', 'no'].includes(value.trim().toLowerCase());
}

function envPercent(): number {
    const value = Number(process.env['CODEX_EARLY_ACTIVATION_WEEKLY_PERCENT'] ?? DEFAULT_EARLY_ACTIVATION_PERCENT);
    return Number.isFinite(value) && value >= 1 && value <= 100
        ? value
        : DEFAULT_EARLY_ACTIVATION_PERCENT;
}

function envColdMigrationThresholdPercent(): number {
    const value = Number(process.env['CODEX_COLD_MIGRATION_FIVE_HOUR_THRESHOLD_PERCENT']
        ?? DEFAULT_COLD_MIGRATION_FIVE_HOUR_THRESHOLD_PERCENT);
    return Number.isFinite(value) && value >= 0 && value <= 100
        ? value
        : DEFAULT_COLD_MIGRATION_FIVE_HOUR_THRESHOLD_PERCENT;
}

export function getCodexBalancerDefaults(): CodexBalancerSettings {
    return {
        mode: envMode(),
        earlyActivationEnabled: envBoolean('CODEX_EARLY_ACTIVATION_ENABLED', true),
        earlyActivationWeeklyPercent: envPercent(),
        coldMigrationFiveHourThresholdPercent: envColdMigrationThresholdPercent(),
    };
}

export function getEffectiveCodexBalancerSettings(): {
    settings: CodexBalancerSettings;
    defaults: CodexBalancerSettings;
    sources: CodexBalancerState['settingSources'];
} {
    const defaults = getCodexBalancerDefaults();
    const persisted = getCodexBalancerSettings() ?? {};
    return {
        defaults,
        settings: { ...defaults, ...persisted },
        sources: {
            mode: persisted.mode === undefined ? 'environment' : 'persisted',
            earlyActivationEnabled: persisted.earlyActivationEnabled === undefined ? 'environment' : 'persisted',
            earlyActivationWeeklyPercent: persisted.earlyActivationWeeklyPercent === undefined ? 'environment' : 'persisted',
            coldMigrationFiveHourThresholdPercent: persisted.coldMigrationFiveHourThresholdPercent === undefined ? 'environment' : 'persisted',
        },
    };
}

export function getDisabledCodexSlotIndexes(): Set<number> {
    return new Set(
        getCodexBalancerSlotOverrides()
            .filter((override) => !override.enabled)
            .map((override) => override.slotIndex),
    );
}

function isFreshWeekly(account: CodexUsageAccountRow | undefined, now: number): account is CodexUsageAccountRow {
    if (!account?.weekly || account.stale || account.source === 'cooldown') return false;
    const updatedAt = Date.parse(account.weekly.updatedAt || account.updatedAt || '');
    const resetAt = Date.parse(account.weekly.resetAt);
    return Number.isFinite(updatedAt)
        && Number.isFinite(resetAt)
        && resetAt > now
        && now - updatedAt <= FRESH_USAGE_MAX_AGE_MS;
}

function scheduledSlotCount(currentWeekday: number, startWeekday: number, slotCount: number): number {
    const elapsed = (currentWeekday - startWeekday + 7) % 7;
    return Math.min(slotCount, elapsed + 1);
}

function nextScheduledActivationAfter(resetAt: number, anchorWeekday: number): number {
    const resetDate = new Date(resetAt);
    const dayStart = Date.UTC(
        resetDate.getUTCFullYear(),
        resetDate.getUTCMonth(),
        resetDate.getUTCDate(),
    );
    const offsetDays = (anchorWeekday - resetDate.getUTCDay() + 7) % 7;
    let candidate = dayStart + offsetDays * 24 * 60 * 60_000;
    if (candidate < resetAt) candidate += 7 * 24 * 60 * 60_000;
    return candidate;
}

function checkpointMap(
    scheduleRows: CodexAccountScheduleRow[],
    checkpoints: CodexActivationCheckpoint[] = getCodexActivationCheckpoints(),
): Map<number, CodexActivationCheckpoint> {
    const accountKeys = new Map(scheduleRows.map((row) => [row.slotIndex, row.accountKey]));
    return new Map(
        checkpoints
            .filter((checkpoint) => accountKeys.get(checkpoint.slotIndex) === checkpoint.accountKey)
            .map((checkpoint) => [checkpoint.slotIndex, checkpoint]),
    );
}

function overrideMap(now: number): Map<number, CodexBalancerSlotOverride> {
    return new Map(getCodexBalancerSlotOverrides().map((override) => {
        if (override.manualActivationCycleResetAt
            && Date.parse(override.manualActivationCycleResetAt) <= now) {
            const cleared = { ...override, manualActivationCycleResetAt: null, updatedAt: new Date(now).toISOString() };
            setCodexBalancerSlotOverride(cleared);
            return [override.slotIndex, cleared];
        }
        return [override.slotIndex, override];
    }));
}

export function resolveCodexActivatedSlots(input: {
    now: number;
    startWeekday: number;
    slots: CodexUsageSelectorSlotIdentity[];
    scheduleRows: CodexAccountScheduleRow[];
    accounts: CodexUsageAccountRow[];
    checkpoints?: CodexActivationCheckpoint[];
}): {
    activatedSlotIndexes: Set<number>;
    enabledSlotIndexes: Set<number>;
    reasons: Map<number, CodexBalancerSlotState['activationReason']>;
} {
    const settings = getEffectiveCodexBalancerSettings().settings;
    const overrides = overrideMap(input.now);
    const checkpoints = checkpointMap(input.scheduleRows, input.checkpoints);
    const ordered = [...input.slots].sort((left, right) => left.slotIndex - right.slotIndex);
    const scheduledCount = scheduledSlotCount(new Date(input.now).getUTCDay(), input.startWeekday, ordered.length);
    const activated = new Set<number>();
    const enabled = new Set<number>();
    const reasons = new Map<number, CodexBalancerSlotState['activationReason']>();

    for (const [index, slot] of ordered.entries()) {
        const override = overrides.get(slot.slotIndex);
        const checkpoint = checkpoints.get(slot.slotIndex);
        const row = input.scheduleRows.find((candidate) => candidate.slotIndex === slot.slotIndex);
        if (override?.enabled !== false) enabled.add(slot.slotIndex);
        const expectedResetAt = Date.parse(checkpoint?.expectedWeeklyResetAt ?? '');
        const checkpointActive = Number.isFinite(expectedResetAt) && expectedResetAt > input.now;
        const scheduledAfterReset = Number.isFinite(expectedResetAt)
            && expectedResetAt <= input.now
            && input.now >= nextScheduledActivationAfter(
                expectedResetAt,
                row?.anchorWeekday ?? (input.startWeekday + index) % 7,
            );
        if (slot.slotIndex === 0 || checkpointActive || scheduledAfterReset || (!checkpoint && index < scheduledCount)) {
            activated.add(slot.slotIndex);
            reasons.set(slot.slotIndex, 'scheduled');
        }
        if (override?.manualActivationCycleResetAt
            && Date.parse(override.manualActivationCycleResetAt) > input.now) {
            activated.add(slot.slotIndex);
            reasons.set(slot.slotIndex, 'manual');
        }
    }

    if (settings.earlyActivationEnabled) {
        const accountsBySlot = new Map<number, CodexUsageAccountRow>();
        for (const account of input.accounts) {
            for (const slotIndex of account.slotIndexes) accountsBySlot.set(slotIndex, account);
        }
        const activeEnabled = ordered.filter((slot) => activated.has(slot.slotIndex) && enabled.has(slot.slotIndex));
        const ready = activeEnabled.length > 0 && activeEnabled.every((slot) => {
            const account = accountsBySlot.get(slot.slotIndex);
            return isFreshWeekly(account, input.now)
                && (account.weekly?.usedPercent ?? 0) >= settings.earlyActivationWeeklyPercent;
        });
        if (ready) {
            const next = ordered.find((slot) => enabled.has(slot.slotIndex) && !activated.has(slot.slotIndex));
            if (next) {
                activated.add(next.slotIndex);
                reasons.set(next.slotIndex, 'early');
            }
        }
    }

    for (const slot of ordered) {
        if (!reasons.has(slot.slotIndex)) reasons.set(slot.slotIndex, 'pending');
    }
    return { activatedSlotIndexes: activated, enabledSlotIndexes: enabled, reasons };
}

export function updateCodexBalancerSettings(input: Partial<CodexBalancerSettings>): void {
    const current = getEffectiveCodexBalancerSettings().settings;
    const next = { ...current, ...input };
    if (!['off', 'shadow', 'on'].includes(next.mode)) throw new Error('mode must be off, shadow, or on');
    if (!Number.isFinite(next.earlyActivationWeeklyPercent)
        || next.earlyActivationWeeklyPercent < 1
        || next.earlyActivationWeeklyPercent > 100) {
        throw new Error('earlyActivationWeeklyPercent must be between 1 and 100');
    }
    if (!Number.isFinite(next.coldMigrationFiveHourThresholdPercent)
        || next.coldMigrationFiveHourThresholdPercent < 0
        || next.coldMigrationFiveHourThresholdPercent > 100) {
        throw new Error('coldMigrationFiveHourThresholdPercent must be between 0 and 100');
    }
    setCodexBalancerSettings(input);
    logCodexBalancerAudit({
        action: 'settings_updated',
        slotIndex: null,
        previousValue: JSON.stringify(current),
        nextValue: JSON.stringify(next),
    });
}

export function updateCodexBalancerSlot(input: {
    slotIndex: number;
    enabled?: boolean;
    activateNow?: boolean;
    weeklyResetAt?: string | null;
}): void {
    if (!Number.isInteger(input.slotIndex) || input.slotIndex < 0) throw new Error('slotIndex must be a non-negative integer');
    const previous = getCodexBalancerSlotOverrides().find((row) => row.slotIndex === input.slotIndex);
    const now = new Date();
    const activationExpiry = input.activateNow
        ? input.weeklyResetAt && Date.parse(input.weeklyResetAt) > now.getTime()
            ? input.weeklyResetAt
            : new Date(now.getTime() + 7 * 24 * 60 * 60_000).toISOString()
        : input.activateNow === false ? null : previous?.manualActivationCycleResetAt ?? null;
    const next: CodexBalancerSlotOverride = {
        slotIndex: input.slotIndex,
        enabled: input.enabled ?? previous?.enabled ?? true,
        manualActivationCycleResetAt: activationExpiry,
        updatedAt: now.toISOString(),
    };
    setCodexBalancerSlotOverride(next);
    logCodexBalancerAudit({
        action: 'slot_updated',
        slotIndex: input.slotIndex,
        previousValue: previous ? JSON.stringify(previous) : null,
        nextValue: JSON.stringify(next),
    });
}

export function updateCodexExpectedWeeklyReset(input: {
    slotIndex: number;
    accountKey: string;
    expectedWeeklyResetAt: string | null;
}): void {
    if (!Number.isInteger(input.slotIndex) || input.slotIndex < 0) throw new Error('slotIndex must be a non-negative integer');
    const previous = getCodexActivationCheckpoints().find((row) => row.slotIndex === input.slotIndex);
    const now = new Date().toISOString();
    setCodexActivationCheckpoint({
        slotIndex: input.slotIndex,
        accountKey: input.accountKey,
        expectedWeeklyResetAt: input.expectedWeeklyResetAt,
        lastUsageCheckAt: previous?.accountKey === input.accountKey ? previous.lastUsageCheckAt : null,
        updatedAt: now,
    });
    logCodexBalancerAudit({
        action: 'expected_weekly_reset_updated',
        slotIndex: input.slotIndex,
        previousValue: previous ? JSON.stringify(previous) : null,
        nextValue: JSON.stringify({ expectedWeeklyResetAt: input.expectedWeeklyResetAt }),
    });
}

export function resetCodexBalancerToEnvironment(): void {
    const previous = {
        settings: getCodexBalancerSettings(),
        slots: getCodexBalancerSlotOverrides(),
    };
    clearCodexBalancerOverrides();
    logCodexBalancerAudit({
        action: 'reset_to_environment',
        slotIndex: null,
        previousValue: JSON.stringify(previous),
        nextValue: null,
    });
}

export function buildCodexBalancerState(input: {
    now: number;
    startWeekday: number;
    slots: CodexUsageSelectorSlotIdentity[];
    scheduleRows: CodexAccountScheduleRow[];
    accounts: CodexUsageAccountRow[];
}): CodexBalancerState {
    const effective = getEffectiveCodexBalancerSettings();
    const activation = resolveCodexActivatedSlots(input);
    const checkpoints = checkpointMap(input.scheduleRows);
    const accountsBySlot = new Map<number, CodexUsageAccountRow>();
    for (const account of input.accounts) {
        for (const slotIndex of account.slotIndexes) accountsBySlot.set(slotIndex, account);
    }
    const rowsBySlot = new Map(input.scheduleRows.map((row) => [row.slotIndex, row]));
    const slots = input.slots.map((slot): CodexBalancerSlotState => {
        const account = accountsBySlot.get(slot.slotIndex);
        const row = rowsBySlot.get(slot.slotIndex);
        const checkpoint = checkpoints.get(slot.slotIndex);
        const capacityMultiplier = getCodexAccountCapacityMultiplier(slot.accountKey);
        return {
            slotIndex: slot.slotIndex,
            path: slot.slotPath,
            accountKey: slot.accountKey,
            authAvailable: slot.authAvailable,
            authUnavailableReason: slot.authUnavailableReason,
            authRetryAt: slot.authRetryAt,
            enabled: activation.enabledSlotIndexes.has(slot.slotIndex),
            activated: activation.activatedSlotIndexes.has(slot.slotIndex),
            activationReason: activation.reasons.get(slot.slotIndex) ?? 'unknown',
            anchorWeekday: row?.anchorWeekday ?? (input.startWeekday + slot.slotIndex) % 7,
            scheduledDay: WEEKDAY_NAMES[row?.anchorWeekday ?? (input.startWeekday + slot.slotIndex) % 7] ?? 'Unknown',
            rateLimitedUntil: slot.rateLimitedUntil > input.now ? new Date(slot.rateLimitedUntil).toISOString() : null,
            telemetryFresh: isFreshWeekly(account, input.now),
            capacityMultiplier,
            fiveHourUsedPercent: account?.fiveHour?.usedPercent ?? null,
            fiveHourResetAt: account?.fiveHour?.resetAt ?? null,
            fiveHourUsedCapacityUnits: account?.fiveHour
                ? account.fiveHour.usedPercent * capacityMultiplier
                : null,
            fiveHourRemainingCapacityUnits: account?.fiveHour
                ? Math.max(0, 100 - account.fiveHour.usedPercent) * capacityMultiplier
                : null,
            weeklyUsedPercent: account?.weekly?.usedPercent ?? null,
            weeklyResetAt: account?.weekly?.resetAt ?? null,
            weeklyUsedCapacityUnits: account?.weekly
                ? account.weekly.usedPercent * capacityMultiplier
                : null,
            weeklyRemainingCapacityUnits: account?.weekly
                ? Math.max(0, 100 - account.weekly.usedPercent) * capacityMultiplier
                : null,
            expectedWeeklyResetAt: checkpoint?.expectedWeeklyResetAt ?? null,
            lastUsageCheckAt: checkpoint?.lastUsageCheckAt ?? null,
            exhausted: (account?.weekly?.usedPercent ?? 0) >= 100,
        };
    });
    return {
        settings: effective.settings,
        defaults: effective.defaults,
        settingSources: effective.sources,
        currentWeekdayUtc: new Date(input.now).getUTCDay(),
        slots,
        activeLease: null,
        lastDecision: getLastCodexBalancerDecision(),
        coldMigrationDecisions: [],
        recentAudit: getCodexBalancerAudit(),
    };
}
