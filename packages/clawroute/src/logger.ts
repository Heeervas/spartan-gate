/**
 * ClawRoute Logger — SQLite Database Layer
 *
 * Handles all database operations using sql.js (WebAssembly SQLite).
 * Stores routing decisions, cost tracking, and payment acknowledgments.
 *
 * PRIVACY: Request previews are stored only when content logging is explicitly enabled.
 * System prompts and complete tool arguments/results are never stored.
 */

import initSqlJs, { Database } from 'sql.js';
import { readFileSync, writeFileSync, unlinkSync, existsSync, mkdirSync, renameSync } from 'fs';
import { join, dirname, resolve } from 'path';
import {
    ClawRouteConfig,
    CodexActivationCheckpoint,
    CodexAnalysisMixRow,
    CodexAnalysisResponse,
    CodexAnalysisSeriesRow,
    CodexAnalysisSummary,
    CodexApiPricingModel,
    CodexBloatConversationRow,
    CodexAccountScheduleRow,
    CodexBalancerAuditRow,
    CodexBalancerDecision,
    CodexBalancerSettings,
    CodexBalancerSlotOverride,
    CodexColdMigrationDecision,
    CodexColdMigrationDecisionStatus,
    CodexResetCreditsSnapshot,
    CodexToolSchemaGroup,
    CodexUsageSnapshotRecord,
    CodexUsageSnapshotHistoryRecord,
    CodexQuotaCalibrationRow,
    CodexSlotUsageEstimate,
    CodexUsageChartPoint,
    LogEntry,
    LiveRoutingResponse,
    LiveRoutingSession,
    LiveRoutingTurn,
    LiveQuotaCalibration,
    RecentDecision,
    RequestTrace,
    RequestTraceCandidate,
    RoutingTokenMetrics,
    TurnRequestsResponse,
    RoutingDailyRollupRecord,
    TaskTier,
} from './types.js';

// === Singleton State ===

let db: Database | null = null;
let dbPath: string = '';
let persistTimer: ReturnType<typeof setTimeout> | null = null;
let persistPending = false;

const PERSIST_DEBOUNCE_MS = 250;

const CODEX_API_PRICING_SOURCE = 'https://developers.openai.com/api/docs/pricing';
const CODEX_API_PRICING: CodexApiPricingModel[] = [
    { model: 'codex/gpt-5.5', inputUsdPerMillion: 5.00, cachedInputUsdPerMillion: 0.50, outputUsdPerMillion: 30.00 },
    { model: 'codex/gpt-5.4', inputUsdPerMillion: 2.50, cachedInputUsdPerMillion: 0.25, outputUsdPerMillion: 15.00 },
    { model: 'codex/gpt-5.4-mini', inputUsdPerMillion: 0.75, cachedInputUsdPerMillion: 0.075, outputUsdPerMillion: 4.50 },
];

function getApiPricingForModel(model: string): CodexApiPricingModel | null {
    return CODEX_API_PRICING.find((entry) => entry.model === model) ?? null;
}

function calculateApiCostUsd(model: string, inputTokens: number, cachedInputTokens: number, outputTokens: number): number {
    const pricing = getApiPricingForModel(model);
    if (!pricing) return 0;
    const cached = Math.max(0, Math.min(inputTokens, cachedInputTokens));
    const uncached = Math.max(0, inputTokens - cached);
    return (
        (uncached / 1_000_000) * pricing.inputUsdPerMillion
        + (cached / 1_000_000) * pricing.cachedInputUsdPerMillion
        + (outputTokens / 1_000_000) * pricing.outputUsdPerMillion
    );
}

function parseContextInfo(value: unknown): Record<string, unknown> | null {
    if (typeof value !== 'string' || value.trim() === '') return null;
    try {
        const parsed = JSON.parse(value) as unknown;
        return parsed && typeof parsed === 'object' && !Array.isArray(parsed)
            ? parsed as Record<string, unknown>
            : null;
    } catch {
        return null;
    }
}

function numberFromContext(context: Record<string, unknown> | null, key: string): number {
    const value = context?.[key];
    return Number.isFinite(Number(value)) ? Number(value) : 0;
}

function stringArrayFromContext(context: Record<string, unknown> | null, key: string): string[] {
    const value = context?.[key];
    return Array.isArray(value)
        ? value.filter((entry): entry is string => typeof entry === 'string')
        : [];
}

function toolSchemaGroupsFromContext(context: Record<string, unknown> | null): CodexToolSchemaGroup[] {
    const value = context?.['top_tool_schema_groups'];
    if (!Array.isArray(value)) return [];
    return value.flatMap((entry): CodexToolSchemaGroup[] => {
        if (!entry || typeof entry !== 'object') return [];
        const raw = entry as Record<string, unknown>;
        const key = typeof raw['key'] === 'string' ? raw['key'] : null;
        if (!key) return [];
        return [{
            key,
            tools: Number(raw['tools']) || 0,
            chars: Number(raw['chars']) || 0,
        }];
    });
}

const ROUTING_LOG_SCHEMA = `
    CREATE TABLE IF NOT EXISTS routing_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        original_model TEXT NOT NULL,
        routed_model TEXT NOT NULL,
        actual_model TEXT NOT NULL,
        tier TEXT NOT NULL,
        classification_reason TEXT NOT NULL DEFAULT '',
        confidence REAL NOT NULL DEFAULT 0,
        input_tokens INTEGER NOT NULL DEFAULT 0,
        cached_input_tokens INTEGER NOT NULL DEFAULT 0,
        output_tokens INTEGER NOT NULL DEFAULT 0,
        original_cost_usd REAL NOT NULL DEFAULT 0,
        actual_cost_usd REAL NOT NULL DEFAULT 0,
        savings_usd REAL NOT NULL DEFAULT 0,
        escalated INTEGER NOT NULL DEFAULT 0,
        escalation_chain TEXT NOT NULL DEFAULT '[]',
        response_time_ms INTEGER NOT NULL DEFAULT 0,
        had_tool_calls INTEGER NOT NULL DEFAULT 0,
        is_dry_run INTEGER NOT NULL DEFAULT 0,
        is_override INTEGER NOT NULL DEFAULT 0,
        session_id TEXT,
        error TEXT,
        prompt_preview TEXT,
        context_info TEXT,
        request_api_kind TEXT NOT NULL DEFAULT 'chat_completions',
        requested_reasoning_effort TEXT,
        selected_codex_slot_index INTEGER,
        selected_codex_account_key TEXT,
        request_id TEXT,
        turn_id TEXT
    )
`;

const ROUTING_LOG_INDEX = `
    CREATE INDEX IF NOT EXISTS idx_routing_log_timestamp
    ON routing_log (timestamp)
`;

const CODEX_USAGE_SCHEMA = `
    CREATE TABLE IF NOT EXISTS codex_usage_snapshots (
        account_key TEXT NOT NULL,
        slot_index INTEGER NOT NULL,
        window TEXT NOT NULL,
        used_percent REAL NOT NULL,
        reset_at TEXT NOT NULL,
        window_minutes INTEGER NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (account_key, window)
    )
`;

const CODEX_USAGE_INDEX = `
    CREATE INDEX IF NOT EXISTS idx_codex_usage_slot_index
    ON codex_usage_snapshots (slot_index)
`;

const CODEX_USAGE_HISTORY_SCHEMA = `
    CREATE TABLE IF NOT EXISTS codex_usage_snapshot_history (
        observed_at TEXT NOT NULL,
        account_key TEXT NOT NULL,
        slot_index INTEGER NOT NULL,
        window TEXT NOT NULL,
        used_percent REAL NOT NULL,
        reset_at TEXT NOT NULL,
        window_minutes INTEGER NOT NULL,
        PRIMARY KEY (observed_at, account_key, window)
    )
`;

const CODEX_USAGE_HISTORY_INDEX = `
    CREATE INDEX IF NOT EXISTS idx_codex_usage_history_observed
    ON codex_usage_snapshot_history (observed_at)
`;

const CODEX_RESET_CREDIT_SUMMARIES_SCHEMA = `
    CREATE TABLE IF NOT EXISTS codex_reset_credit_summaries (
        account_key TEXT PRIMARY KEY,
        slot_index INTEGER NOT NULL,
        available_count INTEGER,
        details_available INTEGER NOT NULL,
        updated_at TEXT NOT NULL
    )
`;

const CODEX_RESET_CREDIT_ITEMS_SCHEMA = `
    CREATE TABLE IF NOT EXISTS codex_reset_credit_items (
        account_key TEXT NOT NULL,
        credit_key TEXT NOT NULL,
        slot_index INTEGER NOT NULL,
        status TEXT NOT NULL,
        reset_type TEXT,
        title TEXT,
        granted_at TEXT,
        expires_at TEXT,
        redeemed_at TEXT,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (account_key, credit_key)
    )
`;

const CODEX_RESET_CREDIT_SLOT_INDEX = `
    CREATE INDEX IF NOT EXISTS idx_codex_reset_credit_slot_index
    ON codex_reset_credit_summaries (slot_index)
`;

const ROUTING_DAILY_ROLLUPS_SCHEMA = `
    CREATE TABLE IF NOT EXISTS routing_daily_rollups (
        day TEXT NOT NULL,
        actual_model TEXT NOT NULL,
        tier TEXT NOT NULL,
        request_api_kind TEXT NOT NULL,
        requested_reasoning_effort TEXT NOT NULL DEFAULT '',
        requests INTEGER NOT NULL DEFAULT 0,
        input_tokens INTEGER NOT NULL DEFAULT 0,
        cached_input_tokens INTEGER NOT NULL DEFAULT 0,
        output_tokens INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (day, actual_model, tier, request_api_kind, requested_reasoning_effort)
    )
`;

const ROUTING_DAILY_ROLLUPS_INDEX = `
    CREATE INDEX IF NOT EXISTS idx_routing_daily_rollups_day
    ON routing_daily_rollups (day)
`;

const CODEX_ACCOUNT_SCHEDULE_SCHEMA = `
    CREATE TABLE IF NOT EXISTS codex_account_schedule (
        account_key TEXT NOT NULL,
        slot_index INTEGER PRIMARY KEY,
        seed_order INTEGER NOT NULL UNIQUE,
        anchor_weekday INTEGER NOT NULL,
        lane_rank INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL
    )
`;

const CODEX_BALANCER_SETTINGS_SCHEMA = `
    CREATE TABLE IF NOT EXISTS codex_balancer_settings (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        mode TEXT,
        early_activation_enabled INTEGER,
        early_activation_weekly_percent REAL,
        cold_migration_five_hour_threshold_percent REAL,
        updated_at TEXT NOT NULL
    )
`;

const CODEX_BALANCER_SLOT_OVERRIDES_SCHEMA = `
    CREATE TABLE IF NOT EXISTS codex_balancer_slot_overrides (
        slot_index INTEGER PRIMARY KEY,
        enabled INTEGER NOT NULL DEFAULT 1,
        manual_activation_cycle_reset_at TEXT,
        updated_at TEXT NOT NULL
    )
`;

const CODEX_ACTIVATION_CHECKPOINT_SCHEMA = `
    CREATE TABLE IF NOT EXISTS codex_activation_checkpoints (
        slot_index INTEGER PRIMARY KEY,
        account_key TEXT NOT NULL,
        expected_weekly_reset_at TEXT,
        last_usage_check_at TEXT,
        updated_at TEXT NOT NULL
    )
`;

const CODEX_BALANCER_AUDIT_SCHEMA = `
    CREATE TABLE IF NOT EXISTS codex_balancer_audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        action TEXT NOT NULL,
        slot_index INTEGER,
        previous_value TEXT,
        next_value TEXT
    )
`;

const CODEX_BALANCER_DECISION_SCHEMA = `
    CREATE TABLE IF NOT EXISTS codex_balancer_decision (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        timestamp TEXT NOT NULL,
        slot_index INTEGER,
        account_key TEXT,
        mode TEXT NOT NULL,
        reason TEXT,
        fallback_reason TEXT,
        weekly_used_percent REAL,
        weekly_reset_at TEXT,
        required_burn_rate REAL
    )
`;

const CODEX_COLD_MIGRATION_DECISIONS_SCHEMA = `
    CREATE TABLE IF NOT EXISTS codex_cold_migration_decisions (
        id TEXT PRIMARY KEY,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        approved_at TEXT,
        dismissed_at TEXT,
        consumed_at TEXT,
        session_key TEXT NOT NULL,
        previous_account_key TEXT NOT NULL,
        previous_slot_index INTEGER,
        target_account_key TEXT NOT NULL,
        target_slot_index INTEGER NOT NULL,
        estimated_input_tokens INTEGER NOT NULL,
        estimated_five_hour_percent REAL NOT NULL,
        threshold_five_hour_percent REAL NOT NULL,
        previous_five_hour_used_percent REAL,
        previous_five_hour_remaining_percent REAL,
        target_five_hour_used_percent REAL,
        target_five_hour_remaining_percent REAL,
        target_weekly_used_percent REAL,
        target_weekly_remaining_percent REAL,
        reason TEXT NOT NULL
    )
`;

const CODEX_COLD_MIGRATION_DECISIONS_STATUS_INDEX = `
    CREATE INDEX IF NOT EXISTS idx_codex_cold_migration_status
    ON codex_cold_migration_decisions (status, expires_at)
`;

const CODEX_ACCOUNT_SCHEDULE_WEEKDAY_INDEX = `
    CREATE INDEX IF NOT EXISTS idx_codex_account_schedule_weekday
    ON codex_account_schedule (anchor_weekday, lane_rank)
`;

const OPTIONAL_COLUMNS = [
    'prompt_preview TEXT',
    'context_info TEXT',
    'cached_input_tokens INTEGER NOT NULL DEFAULT 0',
    "request_api_kind TEXT NOT NULL DEFAULT 'chat_completions'",
    'requested_reasoning_effort TEXT',
    'selected_codex_slot_index INTEGER',
    'selected_codex_account_key TEXT',
    'request_id TEXT',
    'turn_id TEXT',
];

const ROUTING_LOG_SESSION_INDEX = `
    CREATE INDEX IF NOT EXISTS idx_routing_log_session_id
    ON routing_log (session_id, id)
`;

const ROUTING_LOG_TURN_INDEX = `
    CREATE INDEX IF NOT EXISTS idx_routing_log_turn_id
    ON routing_log (turn_id, id)
`;

type SqlJsModule = Awaited<ReturnType<typeof initSqlJs>>;

function initializeSchema(database: Database): void {
    database.run(ROUTING_LOG_SCHEMA);
    database.run(CODEX_USAGE_SCHEMA);
    database.run(CODEX_USAGE_HISTORY_SCHEMA);
    database.run(CODEX_RESET_CREDIT_SUMMARIES_SCHEMA);
    database.run(CODEX_RESET_CREDIT_ITEMS_SCHEMA);
    database.run(ROUTING_DAILY_ROLLUPS_SCHEMA);
    database.run(CODEX_ACCOUNT_SCHEDULE_SCHEMA);
    database.run(CODEX_BALANCER_SETTINGS_SCHEMA);
    database.run(CODEX_BALANCER_SLOT_OVERRIDES_SCHEMA);
    database.run(CODEX_ACTIVATION_CHECKPOINT_SCHEMA);
    database.run(CODEX_BALANCER_AUDIT_SCHEMA);
    database.run(CODEX_BALANCER_DECISION_SCHEMA);
    database.run(CODEX_COLD_MIGRATION_DECISIONS_SCHEMA);

    for (const colDef of OPTIONAL_COLUMNS) {
        try {
            database.run(`ALTER TABLE routing_log ADD COLUMN ${colDef}`);
        } catch {
            // Column already exists — safe to ignore
        }
    }
    try {
        database.run('ALTER TABLE codex_account_schedule ADD COLUMN slot_index INTEGER NOT NULL DEFAULT -1');
    } catch {
        // Column already exists.
    }
    try {
        database.run('ALTER TABLE codex_balancer_settings ADD COLUMN cold_migration_five_hour_threshold_percent REAL');
    } catch {
        // Column already exists.
    }
    migrateCodexAccountScheduleToSlotPrimaryKey(database);
    database.run(`
        INSERT OR IGNORE INTO codex_activation_checkpoints (
            slot_index, account_key, expected_weekly_reset_at, last_usage_check_at, updated_at
        )
        SELECT slot_index, account_key, reset_at, updated_at, updated_at
        FROM codex_usage_snapshots
        WHERE window = 'weekly'
    `);

    database.run(ROUTING_LOG_INDEX);
    database.run(ROUTING_LOG_SESSION_INDEX);
    database.run(ROUTING_LOG_TURN_INDEX);
    database.run(CODEX_USAGE_INDEX);
    database.run(CODEX_USAGE_HISTORY_INDEX);
    database.run(CODEX_RESET_CREDIT_SLOT_INDEX);
    database.run(ROUTING_DAILY_ROLLUPS_INDEX);
    database.run(CODEX_ACCOUNT_SCHEDULE_WEEKDAY_INDEX);
    database.run(CODEX_COLD_MIGRATION_DECISIONS_STATUS_INDEX);
    rebuildRoutingDailyRollups(database);
}

function migrateCodexAccountScheduleToSlotPrimaryKey(database: Database): void {
    const tableInfo = database.exec('PRAGMA table_info(codex_account_schedule)')[0]?.values ?? [];
    const accountKeyColumn = tableInfo.find((row) => row[1] === 'account_key');
    const slotIndexColumn = tableInfo.find((row) => row[1] === 'slot_index');
    const accountKeyIsPrimary = Number(accountKeyColumn?.[5] ?? 0) > 0;
    const slotIndexIsPrimary = Number(slotIndexColumn?.[5] ?? 0) > 0;
    if (!accountKeyIsPrimary || slotIndexIsPrimary) return;

    database.run('BEGIN');
    try {
        database.run('ALTER TABLE codex_account_schedule RENAME TO codex_account_schedule_old');
        database.run(CODEX_ACCOUNT_SCHEDULE_SCHEMA);
        database.run(`
            INSERT OR REPLACE INTO codex_account_schedule (
                account_key, slot_index, seed_order, anchor_weekday, lane_rank, updated_at
            )
            SELECT
                account_key,
                CASE WHEN slot_index >= 0 THEN slot_index ELSE seed_order END AS slot_index,
                seed_order,
                anchor_weekday,
                lane_rank,
                updated_at
            FROM codex_account_schedule_old
            ORDER BY seed_order ASC
        `);
        database.run('DROP TABLE codex_account_schedule_old');
        database.run('COMMIT');
    } catch (error) {
        try {
            database.run('ROLLBACK');
        } catch {
            // Best effort cleanup.
        }
        throw error;
    }
}

function recoverCorruptDatabase(SQL: SqlJsModule, error: unknown): Database {
    const backupPath = `${dbPath}.corrupt-${Date.now()}`;

    try {
        renameSync(dbPath, backupPath);
    } catch (backupError) {
        throw new Error(
            `Failed to recover corrupted ClawRoute database at ${dbPath}. ` +
            `Original error: ${String(error)}. Backup error: ${String(backupError)}`
        );
    }

    console.warn(`Recovered corrupted ClawRoute database: moved ${dbPath} to ${backupPath}`);

    const freshDb = new SQL.Database();
    initializeSchema(freshDb);
    return freshDb;
}

// === Initialization ===

/**
 * Initialize the SQLite database.
 * Creates tables if they don't exist and loads existing data from disk.
 *
 * @param config - The ClawRoute configuration (uses logging.dbPath)
 */
export async function initDb(config: ClawRouteConfig): Promise<void> {
    const SQL = await initSqlJs();
    dbPath = config.logging.dbPath === ':memory:'
        ? ':memory:'
        : resolve(config.logging.dbPath);

    // Ensure directory exists
    const dir = dirname(dbPath);
    if (!existsSync(dir)) {
        mkdirSync(dir, { recursive: true });
    }

    // Verify the data directory is actually writable — a bind-mount may be
    // pointing at a deleted/stale/root-owned inode (common after force-rebuild
    // without force-recreate). Fail fast with a clear message rather than
    // silently logging EACCES on every request.
    if (dbPath !== ':memory:') {
        const probe = join(dir, '.write-probe');
        try {
            writeFileSync(probe, '');
            unlinkSync(probe);
        } catch (e) {
            throw new Error(
                `ClawRoute data directory is not writable: ${dir}\n` +
                `If running in Docker, recreate the container:\n` +
                `  docker compose up -d --force-recreate clawroute\n` +
                `Underlying error: ${e}`
            );
        }
    }

    const hasExistingDb = dbPath !== ':memory:' && existsSync(dbPath);

    try {
        db = hasExistingDb
            ? new SQL.Database(readFileSync(dbPath))
            : new SQL.Database();
        initializeSchema(db);
    } catch (error) {
        try {
            db?.close();
        } catch {
            // Best-effort cleanup for partially opened databases.
        }

        db = null;

        if (!hasExistingDb || dbPath === ':memory:') {
            throw error;
        }

        db = recoverCorruptDatabase(SQL, error);
    }

    // Persist initial state
    persistDb();
}

// === Database Access ===

/**
 * Get the database instance.
 * Returns null if database is not initialized.
 *
 * @returns The sql.js Database instance or null
 */
export function getDb(): Database | null {
    return db;
}

// === Persistence ===

/**
 * Write the in-memory database to disk.
 */
function persistDb(): void {
    if (!db || dbPath === ':memory:') return;

    try {
        const data = db.export();
        const buffer = Buffer.from(data);
        writeFileSync(dbPath, buffer);
    } catch (error) {
        console.warn('Failed to persist database:', error);
    }
}

function schedulePersistDb(): void {
    if (!db || dbPath === ':memory:') return;

    persistPending = true;
    if (persistTimer) return;

    persistTimer = setTimeout(() => {
        persistTimer = null;
        if (!persistPending) return;
        persistPending = false;
        persistDb();
    }, PERSIST_DEBOUNCE_MS);
}

function flushPendingPersist(): void {
    if (persistTimer) {
        clearTimeout(persistTimer);
        persistTimer = null;
    }
    if (!persistPending) return;
    persistPending = false;
    persistDb();
}

function normalizeReasoningEffort(value: unknown): string | null {
    return typeof value === 'string' && value.length > 0 ? value : null;
}

function normalizeApiKind(value: unknown): 'chat_completions' | 'responses' {
    return value === 'responses' ? 'responses' : 'chat_completions';
}

function upsertRoutingDailyRollup(database: Database, entry: LogEntry): void {
    const day = entry.timestamp.slice(0, 10);
    const apiKind = normalizeApiKind(entry.request_api_kind);
    const reasoningEffort = normalizeReasoningEffort(entry.requested_reasoning_effort) ?? '';
    database.run(
        `INSERT INTO routing_daily_rollups (
            day, actual_model, tier, request_api_kind, requested_reasoning_effort,
            requests, input_tokens, cached_input_tokens, output_tokens
        ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
        ON CONFLICT(day, actual_model, tier, request_api_kind, requested_reasoning_effort)
        DO UPDATE SET
            requests = requests + 1,
            input_tokens = input_tokens + excluded.input_tokens,
            cached_input_tokens = cached_input_tokens + excluded.cached_input_tokens,
            output_tokens = output_tokens + excluded.output_tokens`,
        [
            day,
            entry.actual_model,
            entry.tier,
            apiKind,
            reasoningEffort,
            entry.input_tokens,
            entry.cached_input_tokens ?? 0,
            entry.output_tokens,
        ],
    );
}

function rebuildRoutingDailyRollups(database: Database): void {
    try {
        database.run('DELETE FROM routing_daily_rollups');
        database.run(`
            INSERT INTO routing_daily_rollups (
                day, actual_model, tier, request_api_kind, requested_reasoning_effort,
                requests, input_tokens, cached_input_tokens, output_tokens
            )
            SELECT
                substr(timestamp, 1, 10) AS day,
                actual_model,
                tier,
                COALESCE(NULLIF(request_api_kind, ''), 'chat_completions') AS request_api_kind,
                COALESCE(requested_reasoning_effort, '') AS requested_reasoning_effort,
                COUNT(*) AS requests,
                COALESCE(SUM(input_tokens), 0) AS input_tokens,
                COALESCE(SUM(cached_input_tokens), 0) AS cached_input_tokens,
                COALESCE(SUM(output_tokens), 0) AS output_tokens
            FROM routing_log
            GROUP BY
                substr(timestamp, 1, 10),
                actual_model,
                tier,
                COALESCE(NULLIF(request_api_kind, ''), 'chat_completions'),
                COALESCE(requested_reasoning_effort, '')
        `);
    } catch (error) {
        console.warn('Failed to rebuild routing daily rollups:', error);
    }
}

// === Routing Log ===

/**
 * Log a routing decision to the database.
 *
 * @param entry - The log entry to insert
 */
export function logRouting(entry: LogEntry): void {
    if (!db) return;

    try {
        db.run(
            `INSERT INTO routing_log (
                timestamp, original_model, routed_model, actual_model,
                tier, classification_reason, confidence,
                input_tokens, cached_input_tokens, output_tokens,
                original_cost_usd, actual_cost_usd, savings_usd,
                escalated, escalation_chain, response_time_ms,
                had_tool_calls, is_dry_run, is_override,
                session_id, error,
                prompt_preview, context_info,
                request_api_kind, requested_reasoning_effort,
                selected_codex_slot_index, selected_codex_account_key,
                request_id, turn_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
            [
                entry.timestamp,
                entry.original_model,
                entry.routed_model,
                entry.actual_model,
                entry.tier,
                entry.classification_reason,
                entry.confidence,
                entry.input_tokens,
                entry.cached_input_tokens ?? 0,
                entry.output_tokens,
                entry.original_cost_usd,
                entry.actual_cost_usd,
                entry.savings_usd,
                entry.escalated ? 1 : 0,
                entry.escalation_chain,
                entry.response_time_ms,
                entry.had_tool_calls ? 1 : 0,
                entry.is_dry_run ? 1 : 0,
                entry.is_override ? 1 : 0,
                entry.session_id,
                entry.error,
                entry.prompt_preview ?? null,
                entry.context_info ?? null,
                normalizeApiKind(entry.request_api_kind),
                normalizeReasoningEffort(entry.requested_reasoning_effort),
                entry.selected_codex_slot_index ?? null,
                entry.selected_codex_account_key ?? null,
                entry.request_id ?? null,
                entry.turn_id ?? null,
            ]
        );
        upsertRoutingDailyRollup(db, entry);

        schedulePersistDb();
    } catch (error) {
        console.warn('Failed to log routing decision:', error);
    }
}

/**
 * Get recent routing decisions for display.
 *
 * @param limit - Maximum number of decisions to return (default 50)
 * @returns Array of recent decisions
 */
export function getRecentDecisions(limit: number = 50): RecentDecision[] {
    if (!db) return [];

    try {
        const stmt = db.prepare(
            `SELECT timestamp, tier, original_model, routed_model, actual_model,
                    savings_usd, escalated, classification_reason, confidence,
                    response_time_ms, input_tokens, cached_input_tokens, output_tokens, had_tool_calls,
                    is_dry_run, is_override, prompt_preview, context_info, error,
                    request_api_kind, requested_reasoning_effort,
                    selected_codex_slot_index, selected_codex_account_key,
                    request_id, session_id, turn_id
             FROM routing_log
             ORDER BY id DESC
             LIMIT ?`
        );
        stmt.bind([limit]);

        const decisions: RecentDecision[] = [];
        while (stmt.step()) {
            const row = stmt.getAsObject() as Record<string, unknown>;
            decisions.push(recentDecisionFromRow(row));
        }
        stmt.free();

        return decisions;
    } catch (error) {
        console.warn('Failed to get recent decisions:', error);
        return [];
    }
}

function recentDecisionFromRow(row: Record<string, unknown>): RecentDecision {
    const parsed = parseContextInfo(row['context_info']);
    const context: RecentDecision['context'] = parsed ? {
        messageCount: Number(parsed['msg_count']) || 0,
        hasSystem: Boolean(parsed['has_system']),
        toolCount: Number(parsed['tool_count']) || 0,
        lastRole: typeof parsed['last_role'] === 'string' ? parsed['last_role'] : null,
        cacheKeyPresent: Boolean(parsed['cache_key_present']),
        cacheKeyHash: typeof parsed['cache_key_hash'] === 'string' ? parsed['cache_key_hash'] : null,
        messageChars: numberFromContext(parsed, 'message_chars'),
        toolSchemaChars: numberFromContext(parsed, 'tool_schema_chars'),
        toolSchemaRoughTokens: numberFromContext(parsed, 'tool_schema_rough_tokens'),
        topToolSchemaGroups: toolSchemaGroupsFromContext(parsed),
        bloatAlerts: stringArrayFromContext(parsed, 'bloat_alerts'),
    } : null;
    return {
        timestamp: String(row['timestamp']),
        tier: row['tier'] as TaskTier,
        originalModel: String(row['original_model']),
        routedModel: String(row['routed_model']),
        actualModel: String(row['actual_model']),
        savingsUsd: Number(row['savings_usd']) || 0,
        escalated: Number(row['escalated']) === 1,
        reason: String(row['classification_reason'] ?? ''),
        confidence: Number(row['confidence']) || 0,
        responseTimeMs: Number(row['response_time_ms']) || 0,
        inputTokens: Number(row['input_tokens']) || 0,
        cachedInputTokens: Number(row['cached_input_tokens']) || 0,
        outputTokens: Number(row['output_tokens']) || 0,
        hadToolCalls: Number(row['had_tool_calls']) === 1,
        isDryRun: Number(row['is_dry_run']) === 1,
        isOverride: Number(row['is_override']) === 1,
        promptPreview: typeof row['prompt_preview'] === 'string' ? row['prompt_preview'] : null,
        context,
        error: typeof row['error'] === 'string' ? row['error'] : null,
        requestApiKind: normalizeApiKind(row['request_api_kind']),
        requestedReasoningEffort: normalizeReasoningEffort(row['requested_reasoning_effort']),
        selectedCodexSlotIndex: row['selected_codex_slot_index'] === null ? null : Number(row['selected_codex_slot_index']),
        selectedCodexAccountKey: typeof row['selected_codex_account_key'] === 'string' ? row['selected_codex_account_key'] : null,
        requestId: typeof row['request_id'] === 'string' ? row['request_id'] : null,
        sessionId: typeof row['session_id'] === 'string' ? row['session_id'] : null,
        turnId: typeof row['turn_id'] === 'string' ? row['turn_id'] : null,
        requestTrace: requestTraceFromContext(parsed),
    };
}

function requestTraceFromContext(context: Record<string, unknown> | null): RequestTrace | null {
    const value = context?.['request_trace'];
    if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
    const trace = value as Partial<RequestTrace>;
    return trace.version === 1 && typeof trace.requestFingerprint === 'string' && Array.isArray(trace.messageFingerprints)
        ? trace as RequestTrace
        : null;
}

export function getRecentTurnTraceCandidates(turnId: string, limit = 200): RequestTraceCandidate[] {
    if (!db || !turnId) return [];
    const safeLimit = Math.max(1, Math.min(200, Math.floor(limit)));
    try {
        const stmt = db.prepare(`
            SELECT request_id, context_info
            FROM routing_log
            WHERE turn_id = ? AND request_id IS NOT NULL
            ORDER BY id DESC
            LIMIT ?
        `);
        stmt.bind([turnId, safeLimit]);
        const candidates: RequestTraceCandidate[] = [];
        while (stmt.step()) {
            const row = stmt.getAsObject() as Record<string, unknown>;
            const context = parseContextInfo(row['context_info']);
            const trace = requestTraceFromContext(context);
            if (!trace || typeof row['request_id'] !== 'string') continue;
            candidates.push({
                requestId: row['request_id'],
                requestFingerprint: trace.requestFingerprint,
                messageFingerprints: trace.messageFingerprints.filter((value): value is string => typeof value === 'string'),
                messageCount: trace.messageFingerprints.length,
                toolSchemaFingerprint: typeof context?.['tool_schema_fingerprint'] === 'string'
                    ? context['tool_schema_fingerprint']
                    : null,
            });
        }
        stmt.free();
        return candidates;
    } catch (error) {
        console.warn('Failed to read recent turn trace candidates:', error);
        return [];
    }
}

function csvStrings(value: unknown): string[] {
    return typeof value === 'string' && value
        ? value.split(',').filter(Boolean)
        : [];
}

function routingMetricsFromRow(row: Record<string, unknown>): RoutingTokenMetrics {
    const requests = Number(row['requests']) || 0;
    const inputTokens = Number(row['input_tokens']) || 0;
    const cachedInputTokens = Math.min(inputTokens, Number(row['cached_input_tokens']) || 0);
    const outputTokens = Number(row['output_tokens']) || 0;
    const uncachedInputTokens = Math.max(0, inputTokens - cachedInputTokens);
    const codexTokens = Number(row['codex_tokens']) || 0;
    const attributedCodexTokens = Math.min(codexTokens, Number(row['attributed_codex_tokens']) || 0);
    return {
        requests,
        inputTokens,
        cachedInputTokens,
        uncachedInputTokens,
        outputTokens,
        uncachedPlusOutputTokens: uncachedInputTokens + outputTokens,
        cachedPercent: inputTokens > 0 ? (cachedInputTokens / inputTokens) * 100 : 0,
        averageResponseMs: Math.round(Number(row['average_response_ms']) || 0),
        toolCalls: Number(row['tool_calls']) || 0,
        toolResults: Number(row['tool_results']) || 0,
        codexTokens,
        attributedCodexTokens,
        quotaCoveragePercent: codexTokens > 0 ? (attributedCodexTokens / codexTokens) * 100 : 0,
    };
}

function liveQuotaCalibration(now: Date): LiveQuotaCalibration {
    const end = new Date(now.getTime() + 1_000).toISOString();
    const start = new Date(now.getTime() - 7 * 24 * 60 * 60 * 1_000).toISOString();
    const rows = readQuotaCalibration(start, end);
    return {
        source: 'calibrated_total_tokens',
        periodDays: 7,
        fiveHour: rows.find((row) => row.window === 'fiveHour') ?? null,
        weekly: rows.find((row) => row.window === 'weekly') ?? null,
        fiveHourBurstSensitive: true,
    };
}

const TRACE_TOOL_CALLS_SQL = `SUM(CASE
    WHEN json_valid(context_info) THEN COALESCE(json_extract(context_info, '$.request_trace.delta.toolCallCount'), 0)
    ELSE 0 END)`;
const TRACE_TOOL_RESULTS_SQL = `SUM(CASE
    WHEN json_valid(context_info) THEN COALESCE(json_extract(context_info, '$.request_trace.delta.toolResultCount'), 0)
    ELSE 0 END)`;

export function getLiveRoutingSessions(
    sessionLimit = 10,
    turnLimit = 20,
    retentionDays = 30,
    now = new Date(),
): LiveRoutingResponse {
    const quotaCalibration = liveQuotaCalibration(now);
    if (!db) return { sessions: [], hasMoreSessions: false, retentionDays, quotaCalibration };
    const safeSessionLimit = Math.max(1, Math.min(50, Math.floor(sessionLimit)));
    const safeTurnLimit = Math.max(1, Math.min(100, Math.floor(turnLimit)));
    try {
        const countStmt = db.prepare(`
            SELECT COUNT(DISTINCT session_id)
            FROM routing_log
            WHERE session_id IS NOT NULL AND turn_id IS NOT NULL
        `);
        const sessionCount = countStmt.step() ? Number(countStmt.get()[0]) || 0 : 0;
        countStmt.free();

        const sessionStmt = db.prepare(`
            SELECT session_id,
                   MIN(timestamp) AS first_seen_at, MAX(timestamp) AS last_seen_at,
                   COUNT(*) AS requests, COUNT(DISTINCT turn_id) AS turn_count,
                   COALESCE(SUM(input_tokens), 0) AS input_tokens,
                   COALESCE(SUM(cached_input_tokens), 0) AS cached_input_tokens,
                   COALESCE(SUM(output_tokens), 0) AS output_tokens,
                   COALESCE(SUM(CASE WHEN actual_model LIKE 'codex/%'
                       THEN input_tokens + output_tokens ELSE 0 END), 0) AS codex_tokens,
                   COALESCE(SUM(CASE WHEN actual_model LIKE 'codex/%' AND selected_codex_slot_index IS NOT NULL
                       THEN input_tokens + output_tokens ELSE 0 END), 0) AS attributed_codex_tokens,
                   COALESCE(AVG(response_time_ms), 0) AS average_response_ms,
                   GROUP_CONCAT(DISTINCT actual_model) AS models,
                   GROUP_CONCAT(DISTINCT tier) AS tiers,
                   ${TRACE_TOOL_CALLS_SQL} AS tool_calls,
                   ${TRACE_TOOL_RESULTS_SQL} AS tool_results,
                   MAX(id) AS last_id
            FROM routing_log
            WHERE session_id IS NOT NULL AND turn_id IS NOT NULL
            GROUP BY session_id
            ORDER BY last_id DESC
            LIMIT ?
        `);
        sessionStmt.bind([safeSessionLimit]);
        const sessions: LiveRoutingSession[] = [];
        while (sessionStmt.step()) {
            const sessionRow = sessionStmt.getAsObject() as Record<string, unknown>;
            const sessionId = String(sessionRow['session_id']);
            const turnStmt = db.prepare(`
                SELECT turn_id,
                       MIN(timestamp) AS first_seen_at, MAX(timestamp) AS last_seen_at,
                       COUNT(*) AS requests,
                       COALESCE(SUM(input_tokens), 0) AS input_tokens,
                       COALESCE(SUM(cached_input_tokens), 0) AS cached_input_tokens,
                       COALESCE(SUM(output_tokens), 0) AS output_tokens,
                       COALESCE(SUM(CASE WHEN actual_model LIKE 'codex/%'
                           THEN input_tokens + output_tokens ELSE 0 END), 0) AS codex_tokens,
                       COALESCE(SUM(CASE WHEN actual_model LIKE 'codex/%' AND selected_codex_slot_index IS NOT NULL
                           THEN input_tokens + output_tokens ELSE 0 END), 0) AS attributed_codex_tokens,
                       COALESCE(AVG(response_time_ms), 0) AS average_response_ms,
                       GROUP_CONCAT(DISTINCT actual_model) AS models,
                       GROUP_CONCAT(DISTINCT tier) AS tiers,
                       ${TRACE_TOOL_CALLS_SQL} AS tool_calls,
                       ${TRACE_TOOL_RESULTS_SQL} AS tool_results,
                       (SELECT prompt_preview FROM routing_log first
                        WHERE first.turn_id = routing_log.turn_id ORDER BY first.id ASC LIMIT 1) AS prompt_preview,
                       (SELECT tier FROM routing_log first
                        WHERE first.turn_id = routing_log.turn_id ORDER BY first.id ASC LIMIT 1) AS initial_tier,
                       (SELECT routed_model FROM routing_log first
                        WHERE first.turn_id = routing_log.turn_id ORDER BY first.id ASC LIMIT 1) AS initial_model,
                       MAX(id) AS last_id
                FROM routing_log
                WHERE session_id = ? AND turn_id IS NOT NULL
                GROUP BY turn_id
                ORDER BY last_id DESC
                LIMIT ?
            `);
            turnStmt.bind([sessionId, safeTurnLimit]);
            const turns: LiveRoutingTurn[] = [];
            while (turnStmt.step()) {
                const turnRow = turnStmt.getAsObject() as Record<string, unknown>;
                const firstSeenAt = String(turnRow['first_seen_at']);
                const lastSeenAt = String(turnRow['last_seen_at']);
                turns.push({
                    id: String(turnRow['turn_id']),
                    firstSeenAt,
                    lastSeenAt,
                    durationMs: Math.max(0, Date.parse(lastSeenAt) - Date.parse(firstSeenAt)),
                    promptPreview: typeof turnRow['prompt_preview'] === 'string' ? turnRow['prompt_preview'] : null,
                    initialTier: String(turnRow['initial_tier'] ?? ''),
                    initialModel: String(turnRow['initial_model'] ?? ''),
                    models: csvStrings(turnRow['models']),
                    tiers: csvStrings(turnRow['tiers']),
                    metrics: routingMetricsFromRow(turnRow),
                });
            }
            turnStmt.free();
            const firstSeenAt = String(sessionRow['first_seen_at']);
            const lastSeenAt = String(sessionRow['last_seen_at']);
            const turnCount = Number(sessionRow['turn_count']) || 0;
            sessions.push({
                id: sessionId,
                firstSeenAt,
                lastSeenAt,
                durationMs: Math.max(0, Date.parse(lastSeenAt) - Date.parse(firstSeenAt)),
                turnCount,
                models: csvStrings(sessionRow['models']),
                tiers: csvStrings(sessionRow['tiers']),
                metrics: routingMetricsFromRow(sessionRow),
                turns,
                hasMoreTurns: turnCount > turns.length,
            });
        }
        sessionStmt.free();
        return {
            sessions,
            hasMoreSessions: sessionCount > sessions.length,
            retentionDays,
            quotaCalibration,
        };
    } catch (error) {
        console.warn('Failed to read live routing sessions:', error);
        return { sessions: [], hasMoreSessions: false, retentionDays, quotaCalibration };
    }
}

export function getTurnRequests(turnId: string, limit = 200): TurnRequestsResponse {
    if (!db || !turnId) return { turnId, requests: [], truncated: false };
    const safeLimit = Math.max(1, Math.min(200, Math.floor(limit)));
    try {
        const stmt = db.prepare(`
            SELECT timestamp, tier, original_model, routed_model, actual_model,
                   savings_usd, escalated, classification_reason, confidence,
                   response_time_ms, input_tokens, cached_input_tokens, output_tokens, had_tool_calls,
                   is_dry_run, is_override, prompt_preview, context_info, error,
                   request_api_kind, requested_reasoning_effort,
                   selected_codex_slot_index, selected_codex_account_key,
                   request_id, session_id, turn_id
            FROM routing_log
            WHERE turn_id = ?
            ORDER BY id ASC
            LIMIT ?
        `);
        stmt.bind([turnId, safeLimit + 1]);
        const requests: RecentDecision[] = [];
        while (stmt.step()) requests.push(recentDecisionFromRow(stmt.getAsObject() as Record<string, unknown>));
        stmt.free();
        return {
            turnId,
            requests: requests.slice(0, safeLimit),
            truncated: requests.length > safeLimit,
        };
    } catch (error) {
        console.warn('Failed to read turn requests:', error);
        return { turnId, requests: [], truncated: false };
    }
}

export function getCodexPromptCacheUsage(periodHours: number = 24): {
    inputTokens: number;
    cachedInputTokens: number;
    cachedPercent: number;
    periodHours: number;
} {
    if (!db) return { inputTokens: 0, cachedInputTokens: 0, cachedPercent: 0, periodHours };
    const safeHours = Number.isFinite(periodHours) && periodHours > 0 ? periodHours : 24;
    try {
        const stmt = db.prepare(`
            SELECT
                COALESCE(SUM(input_tokens), 0),
                COALESCE(SUM(cached_input_tokens), 0)
            FROM routing_log
            WHERE actual_model LIKE 'codex/%'
              AND julianday(timestamp) >= julianday('now', ?)
        `);
        stmt.bind([`-${safeHours} hours`]);
        const row = stmt.step() ? stmt.get() : [0, 0];
        stmt.free();
        const inputTokens = Number(row[0]) || 0;
        const cachedInputTokens = Math.min(inputTokens, Number(row[1]) || 0);
        return {
            inputTokens,
            cachedInputTokens,
            cachedPercent: inputTokens > 0 ? (cachedInputTokens / inputTokens) * 100 : 0,
            periodHours: safeHours,
        };
    } catch (error) {
        console.warn('Failed to read Codex prompt cache usage:', error);
        return { inputTokens: 0, cachedInputTokens: 0, cachedPercent: 0, periodHours: safeHours };
    }
}

export function getCodexUsageSnapshots(): CodexUsageSnapshotRecord[] {
    if (!db) return [];

    try {
        const stmt = db.prepare(
            `SELECT account_key, slot_index, window, used_percent, reset_at, window_minutes, updated_at
             FROM codex_usage_snapshots
             ORDER BY updated_at DESC`
        );
        const snapshots: CodexUsageSnapshotRecord[] = [];

        while (stmt.step()) {
            const row = stmt.getAsObject() as Record<string, unknown>;
            snapshots.push({
                accountKey: row['account_key'] as string,
                slotIndex: Number(row['slot_index']),
                window: row['window'] as CodexUsageSnapshotRecord['window'],
                usedPercent: Number(row['used_percent']),
                resetAt: row['reset_at'] as string,
                windowMinutes: Number(row['window_minutes']),
                updatedAt: row['updated_at'] as string,
            });
        }

        stmt.free();
        return snapshots;
    } catch (error) {
        console.warn('Failed to load Codex usage snapshots:', error);
        return [];
    }
}

export function upsertCodexUsageSnapshots(records: CodexUsageSnapshotRecord[]): void {
    if (!db || records.length === 0) return;

    try {
        db.run('BEGIN');
        for (const record of records) {
            db.run(
                `DELETE FROM codex_usage_snapshots
                 WHERE slot_index = ? AND account_key <> ?`,
                [record.slotIndex, record.accountKey],
            );
            db.run(
                `INSERT INTO codex_usage_snapshots (
                    account_key, slot_index, window, used_percent, reset_at, window_minutes, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_key, window) DO UPDATE SET
                    slot_index = excluded.slot_index,
                    used_percent = excluded.used_percent,
                    reset_at = excluded.reset_at,
                    window_minutes = excluded.window_minutes,
                    updated_at = excluded.updated_at`,
                [
                    record.accountKey,
                    record.slotIndex,
                    record.window,
                    record.usedPercent,
                    record.resetAt,
                    record.windowMinutes,
                    record.updatedAt,
                ]
            );
            db.run(
                `INSERT OR IGNORE INTO codex_usage_snapshot_history (
                    observed_at, account_key, slot_index, window,
                    used_percent, reset_at, window_minutes
                ) VALUES (?, ?, ?, ?, ?, ?, ?)`,
                [
                    record.updatedAt,
                    record.accountKey,
                    record.slotIndex,
                    record.window,
                    record.usedPercent,
                    record.resetAt,
                    record.windowMinutes,
                ],
            );
        }
        db.run('COMMIT');
        schedulePersistDb();
    } catch (error) {
        try {
            db.run('ROLLBACK');
        } catch {
            // Best effort cleanup.
        }
        console.warn('Failed to persist Codex usage snapshots:', error);
    }
}

export function getCodexResetCreditSnapshots(): CodexResetCreditsSnapshot[] {
    if (!db) return [];

    try {
        const summaryStmt = db.prepare(
            `SELECT account_key, slot_index, available_count, details_available, updated_at
             FROM codex_reset_credit_summaries
             ORDER BY updated_at DESC`
        );
        const snapshots = new Map<string, CodexResetCreditsSnapshot>();

        while (summaryStmt.step()) {
            const row = summaryStmt.getAsObject() as Record<string, unknown>;
            const accountKey = String(row['account_key']);
            snapshots.set(accountKey, {
                accountKey,
                slotIndex: Number(row['slot_index']),
                availableCount: row['available_count'] === null || row['available_count'] === undefined
                    ? null
                    : Number(row['available_count']),
                detailsAvailable: Boolean(Number(row['details_available'])),
                source: 'persisted',
                updatedAt: String(row['updated_at']),
                credits: [],
            });
        }
        summaryStmt.free();

        const itemStmt = db.prepare(
            `SELECT account_key, credit_key, status,
                    granted_at, expires_at, redeemed_at
             FROM codex_reset_credit_items
             ORDER BY expires_at IS NULL, expires_at ASC, granted_at ASC`
        );
        while (itemStmt.step()) {
            const row = itemStmt.getAsObject() as Record<string, unknown>;
            const accountKey = String(row['account_key']);
            const snapshot = snapshots.get(accountKey);
            if (!snapshot) continue;
            snapshot.credits.push({
                creditKey: String(row['credit_key']),
                status: String(row['status'] ?? 'unknown'),
                resetType: null,
                title: null,
                grantedAt: row['granted_at'] === null || row['granted_at'] === undefined ? null : String(row['granted_at']),
                expiresAt: row['expires_at'] === null || row['expires_at'] === undefined ? null : String(row['expires_at']),
                redeemedAt: row['redeemed_at'] === null || row['redeemed_at'] === undefined ? null : String(row['redeemed_at']),
            });
        }
        itemStmt.free();

        return [...snapshots.values()];
    } catch (error) {
        console.warn('Failed to load Codex reset credit snapshots:', error);
        return [];
    }
}

export function upsertCodexResetCreditSnapshots(records: CodexResetCreditsSnapshot[]): void {
    if (!db || records.length === 0) return;

    try {
        db.run('BEGIN');
        for (const record of records) {
            const staleStmt = db.prepare(
                `SELECT account_key FROM codex_reset_credit_summaries
                 WHERE slot_index = ? AND account_key <> ?`
            );
            staleStmt.bind([record.slotIndex, record.accountKey]);
            while (staleStmt.step()) {
                const row = staleStmt.get();
                db.run('DELETE FROM codex_reset_credit_items WHERE account_key = ?', [String(row[0])]);
            }
            staleStmt.free();
            db.run(
                `DELETE FROM codex_reset_credit_summaries
                 WHERE slot_index = ? AND account_key <> ?`,
                [record.slotIndex, record.accountKey],
            );
            db.run(
                `INSERT INTO codex_reset_credit_summaries (
                    account_key, slot_index, available_count, details_available, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(account_key) DO UPDATE SET
                    slot_index = excluded.slot_index,
                    available_count = excluded.available_count,
                    details_available = excluded.details_available,
                    updated_at = excluded.updated_at`,
                [
                    record.accountKey,
                    record.slotIndex,
                    record.availableCount,
                    record.detailsAvailable ? 1 : 0,
                    record.updatedAt,
                ],
            );
            db.run('DELETE FROM codex_reset_credit_items WHERE account_key = ?', [record.accountKey]);
            for (const credit of record.credits) {
                db.run(
                    `INSERT INTO codex_reset_credit_items (
                        account_key, credit_key, slot_index, status, reset_type, title,
                        granted_at, expires_at, redeemed_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
                    [
                        record.accountKey,
                        credit.creditKey,
                        record.slotIndex,
                        credit.status,
                        null,
                        null,
                        credit.grantedAt,
                        credit.expiresAt,
                        credit.redeemedAt,
                        record.updatedAt,
                    ],
                );
            }
        }
        db.run('COMMIT');
        schedulePersistDb();
    } catch (error) {
        try {
            db.run('ROLLBACK');
        } catch {
            // Best effort cleanup.
        }
        console.warn('Failed to persist Codex reset credit snapshots:', error);
    }
}

function emptyAnalysisSummary(): CodexAnalysisSummary {
    return {
        requests: 0,
        inputTokens: 0,
        cachedInputTokens: 0,
        cachedPercent: 0,
        outputTokens: 0,
        apiCostUsd: 0,
    };
}

function readAnalysisSummary(start: string, end: string): CodexAnalysisSummary {
    if (!db) return emptyAnalysisSummary();
    const stmt = db.prepare(`
        SELECT
            COUNT(*) AS requests,
            COALESCE(SUM(input_tokens), 0) AS input_tokens,
            COALESCE(SUM(cached_input_tokens), 0) AS cached_input_tokens,
            COALESCE(SUM(output_tokens), 0) AS output_tokens,
            COALESCE(SUM(
                CASE actual_model
                    WHEN 'codex/gpt-5.5' THEN ((input_tokens - cached_input_tokens) * 5.00 + cached_input_tokens * 0.50 + output_tokens * 30.00) / 1000000.0
                    WHEN 'codex/gpt-5.4' THEN ((input_tokens - cached_input_tokens) * 2.50 + cached_input_tokens * 0.25 + output_tokens * 15.00) / 1000000.0
                    WHEN 'codex/gpt-5.4-mini' THEN ((input_tokens - cached_input_tokens) * 0.75 + cached_input_tokens * 0.075 + output_tokens * 4.50) / 1000000.0
                    ELSE 0
                END
            ), 0) AS api_cost_usd
        FROM routing_log
        WHERE timestamp >= ? AND timestamp < ?
    `);
    stmt.bind([start, end]);
    const row = stmt.step() ? stmt.get() : [0, 0, 0, 0, 0];
    stmt.free();
    const inputTokens = Number(row[1]) || 0;
    const cachedInputTokens = Number(row[2]) || 0;
    return {
        requests: Number(row[0]) || 0,
        inputTokens,
        cachedInputTokens,
        cachedPercent: inputTokens > 0 ? (cachedInputTokens / inputTokens) * 100 : 0,
        outputTokens: Number(row[3]) || 0,
        apiCostUsd: Number(row[4]) || 0,
    };
}

function readAnalysisDaily(start: string, end: string): CodexAnalysisSeriesRow[] {
    if (!db) return [];
    const stmt = db.prepare(`
        SELECT
            substr(timestamp, 1, 10) AS day,
            COUNT(*) AS requests,
            COALESCE(SUM(input_tokens), 0) AS input_tokens,
            COALESCE(SUM(cached_input_tokens), 0) AS cached_input_tokens,
            COALESCE(SUM(output_tokens), 0) AS output_tokens,
            COALESCE(SUM(
                CASE actual_model
                    WHEN 'codex/gpt-5.5' THEN ((input_tokens - cached_input_tokens) * 5.00 + cached_input_tokens * 0.50 + output_tokens * 30.00) / 1000000.0
                    WHEN 'codex/gpt-5.4' THEN ((input_tokens - cached_input_tokens) * 2.50 + cached_input_tokens * 0.25 + output_tokens * 15.00) / 1000000.0
                    WHEN 'codex/gpt-5.4-mini' THEN ((input_tokens - cached_input_tokens) * 0.75 + cached_input_tokens * 0.075 + output_tokens * 4.50) / 1000000.0
                    ELSE 0
                END
            ), 0) AS api_cost_usd
        FROM routing_log
        WHERE timestamp >= ? AND timestamp < ?
        GROUP BY substr(timestamp, 1, 10)
        ORDER BY day
    `);
    stmt.bind([start, end]);
    const rows: CodexAnalysisSeriesRow[] = [];
    while (stmt.step()) {
        const row = stmt.getAsObject() as Record<string, unknown>;
        const inputTokens = Number(row['input_tokens']) || 0;
        const cachedInputTokens = Number(row['cached_input_tokens']) || 0;
        rows.push({
            day: String(row['day']),
            requests: Number(row['requests']) || 0,
            inputTokens,
            cachedInputTokens,
            cachedPercent: inputTokens > 0 ? (cachedInputTokens / inputTokens) * 100 : 0,
            outputTokens: Number(row['output_tokens']) || 0,
            apiCostUsd: Number(row['api_cost_usd']) || 0,
        });
    }
    stmt.free();
    return rows;
}

function readAnalysisMix(column: string, start: string, end: string): CodexAnalysisMixRow[] {
    if (!db) return [];
    const allowed = new Set(['actual_model', 'tier', 'request_api_kind', 'requested_reasoning_effort']);
    if (!allowed.has(column)) return [];
    const stmt = db.prepare(`
        WITH scoped AS (
            SELECT ${column} AS key
            FROM routing_log
            WHERE timestamp >= ? AND timestamp < ?
        )
        SELECT
            key,
            COUNT(*) AS requests,
            ROUND(100.0 * COUNT(*) / NULLIF(SUM(COUNT(*)) OVER (), 0), 1) AS request_share_pct
        FROM scoped
        GROUP BY key
        ORDER BY requests DESC, key
    `);
    stmt.bind([start, end]);
    const rows: CodexAnalysisMixRow[] = [];
    while (stmt.step()) {
        const row = stmt.getAsObject() as Record<string, unknown>;
        rows.push({
            key: typeof row['key'] === 'string' && row['key'].length > 0 ? row['key'] : null,
            requests: Number(row['requests']) || 0,
            requestSharePercent: Number(row['request_share_pct']) || 0,
        });
    }
    stmt.free();
    return rows;
}

function readQuotaHistory(start: string, end: string): CodexUsageSnapshotHistoryRecord[] {
    if (!db) return [];
    const stmt = db.prepare(`
        SELECT observed_at, account_key, slot_index, window, used_percent, reset_at, window_minutes
        FROM codex_usage_snapshot_history
        WHERE observed_at >= ? AND observed_at < ?
        ORDER BY observed_at DESC, window, slot_index
    `);
    stmt.bind([start, end]);
    const rows: CodexUsageSnapshotHistoryRecord[] = [];
    while (stmt.step()) {
        const row = stmt.getAsObject() as Record<string, unknown>;
        rows.push({
            observedAt: String(row['observed_at']),
            accountKey: String(row['account_key']),
            slotIndex: Number(row['slot_index']),
            window: row['window'] as CodexUsageSnapshotRecord['window'],
            usedPercent: Number(row['used_percent']),
            resetAt: String(row['reset_at']),
            windowMinutes: Number(row['window_minutes']),
            updatedAt: String(row['observed_at']),
        });
    }
    stmt.free();
    return rows;
}

function readDailyRollups(startDay: string, endDay: string): RoutingDailyRollupRecord[] {
    if (!db) return [];
    const stmt = db.prepare(`
        SELECT
            day, actual_model, tier, request_api_kind, requested_reasoning_effort,
            requests, input_tokens, cached_input_tokens, output_tokens
        FROM routing_daily_rollups
        WHERE day >= ? AND day <= ?
        ORDER BY day, requests DESC
    `);
    stmt.bind([startDay, endDay]);
    const rows: RoutingDailyRollupRecord[] = [];
    while (stmt.step()) {
        const row = stmt.getAsObject() as Record<string, unknown>;
        const inputTokens = Number(row['input_tokens']) || 0;
        const cachedInputTokens = Number(row['cached_input_tokens']) || 0;
        rows.push({
            day: String(row['day']),
            actualModel: String(row['actual_model']),
            tier: String(row['tier']),
            requestApiKind: normalizeApiKind(row['request_api_kind']),
            requestedReasoningEffort: normalizeReasoningEffort(row['requested_reasoning_effort']),
            requests: Number(row['requests']) || 0,
            inputTokens,
            cachedInputTokens,
            cachedPercent: inputTokens > 0 ? (cachedInputTokens / inputTokens) * 100 : 0,
            outputTokens: Number(row['output_tokens']) || 0,
            apiCostUsd: calculateApiCostUsd(
                String(row['actual_model']),
                inputTokens,
                cachedInputTokens,
                Number(row['output_tokens']) || 0,
            ),
        });
    }
    stmt.free();
    return rows;
}

type SlotWindowRequestTotals = {
    slotIndex: number;
    window: 'fiveHour' | 'weekly';
    observedAt: string;
    resetAt: string;
    windowStart: string;
    windowMinutes: number;
    requests: number;
    inputTokens: number;
    cachedInputTokens: number;
    outputTokens: number;
    totalTokens: number;
    uncachedPlusOutputTokens: number;
    apiCostUsd: number;
    actualQuotaDelta: number;
};

function readSlotWindowRequestTotals(start: string, end: string): SlotWindowRequestTotals[] {
    if (!db) return [];
    const stmt = db.prepare(`
        WITH latest AS (
            SELECT
                slot_index,
                window,
                observed_at,
                reset_at,
                window_minutes,
                datetime(reset_at, '-' || window_minutes || ' minutes') AS window_start,
                used_percent,
                row_number() OVER (
                    PARTITION BY slot_index, window
                    ORDER BY observed_at DESC
                ) AS rn
            FROM codex_usage_snapshot_history
            WHERE observed_at >= ? AND observed_at < ?
        )
        SELECT
            latest.slot_index,
            latest.window,
            latest.observed_at,
            latest.reset_at,
            latest.window_start,
            latest.window_minutes,
            COALESCE(COUNT(r.id), 0) AS requests,
            COALESCE(SUM(r.input_tokens), 0) AS input_tokens,
            COALESCE(SUM(r.cached_input_tokens), 0) AS cached_input_tokens,
            COALESCE(SUM(r.output_tokens), 0) AS output_tokens,
            COALESCE(SUM(
                CASE r.actual_model
                    WHEN 'codex/gpt-5.5' THEN ((r.input_tokens - r.cached_input_tokens) * 5.00 + r.cached_input_tokens * 0.50 + r.output_tokens * 30.00) / 1000000.0
                    WHEN 'codex/gpt-5.4' THEN ((r.input_tokens - r.cached_input_tokens) * 2.50 + r.cached_input_tokens * 0.25 + r.output_tokens * 15.00) / 1000000.0
                    WHEN 'codex/gpt-5.4-mini' THEN ((r.input_tokens - r.cached_input_tokens) * 0.75 + r.cached_input_tokens * 0.075 + r.output_tokens * 4.50) / 1000000.0
                    ELSE 0
                END
            ), 0) AS api_cost_usd,
            latest.used_percent AS actual_quota_delta
        FROM latest
        LEFT JOIN routing_log r
            ON r.selected_codex_slot_index = latest.slot_index
           AND r.actual_model LIKE 'codex/%'
           AND datetime(r.timestamp) >= latest.window_start
           AND datetime(r.timestamp) <= datetime(latest.observed_at)
        WHERE latest.rn = 1
        GROUP BY
            latest.slot_index,
            latest.window,
            latest.observed_at,
            latest.reset_at,
            latest.window_start,
            latest.window_minutes,
            latest.used_percent
        ORDER BY latest.slot_index, latest.window
    `);
    stmt.bind([start, end]);
    const rows: SlotWindowRequestTotals[] = [];
    while (stmt.step()) {
        const row = stmt.getAsObject() as Record<string, unknown>;
        const inputTokens = Number(row['input_tokens']) || 0;
        const cachedInputTokens = Number(row['cached_input_tokens']) || 0;
        const outputTokens = Number(row['output_tokens']) || 0;
        rows.push({
            slotIndex: Number(row['slot_index']),
            window: row['window'] as 'fiveHour' | 'weekly',
            observedAt: String(row['observed_at']),
            resetAt: String(row['reset_at']),
            windowStart: String(row['window_start']),
            windowMinutes: Number(row['window_minutes']),
            requests: Number(row['requests']) || 0,
            inputTokens,
            cachedInputTokens,
            outputTokens,
            totalTokens: inputTokens + outputTokens,
            uncachedPlusOutputTokens: Math.max(0, inputTokens - cachedInputTokens) + outputTokens,
            apiCostUsd: Number(row['api_cost_usd']) || 0,
            actualQuotaDelta: Number(row['actual_quota_delta']) || 0,
        });
    }
    stmt.free();
    return rows;
}

function buildBloatConversationKey(row: Record<string, unknown>, context: Record<string, unknown> | null): string | null {
    const cacheKeyHash = typeof context?.['cache_key_hash'] === 'string' ? context['cache_key_hash'] : null;
    if (cacheKeyHash) return `cache:${cacheKeyHash}`;
    if (typeof row['session_id'] === 'string' && row['session_id']) return `session:${row['session_id']}`;
    if (typeof row['selected_codex_account_key'] === 'string' && row['selected_codex_account_key']) {
        return `account:${row['selected_codex_account_key']}`;
    }
    const slotIndex = Number(row['selected_codex_slot_index']);
    return Number.isFinite(slotIndex) ? `slot:${slotIndex}` : null;
}

function topToolSchemaGroups(groups: Map<string, CodexToolSchemaGroup>): CodexToolSchemaGroup[] {
    return [...groups.values()]
        .sort((a, b) => b.chars - a.chars)
        .slice(0, 5);
}

function readCodexBloatConversations(start: string, end: string): CodexBloatConversationRow[] {
    if (!db) return [];
    const stmt = db.prepare(`
        SELECT timestamp, actual_model, input_tokens, cached_input_tokens, output_tokens,
               session_id, context_info, selected_codex_slot_index, selected_codex_account_key
        FROM routing_log
        WHERE datetime(timestamp) >= datetime(?) AND datetime(timestamp) < datetime(?)
          AND actual_model LIKE 'codex/%'
        ORDER BY timestamp ASC
    `);
    stmt.bind([start, end]);
    const rows = new Map<string, CodexBloatConversationRow & {
        groupMap: Map<string, CodexToolSchemaGroup>;
        alertSet: Set<string>;
        slotSet: Set<number>;
        modelSet: Set<string>;
    }>();
    while (stmt.step()) {
        const row = stmt.getAsObject() as Record<string, unknown>;
        const context = parseContextInfo(row['context_info']);
        const key = buildBloatConversationKey(row, context) ?? 'unknown';
        const inputTokens = Number(row['input_tokens']) || 0;
        const cachedInputTokens = Number(row['cached_input_tokens']) || 0;
        const outputTokens = Number(row['output_tokens']) || 0;
        const timestamp = typeof row['timestamp'] === 'string' ? row['timestamp'] : null;
        const entry = rows.get(key) ?? {
            key,
            requests: 0,
            inputTokens: 0,
            cachedInputTokens: 0,
            outputTokens: 0,
            totalTokens: 0,
            cachedPercent: 0,
            firstSeenAt: timestamp,
            lastSeenAt: timestamp,
            maxMessageCount: 0,
            maxToolCount: 0,
            maxMessageChars: 0,
            maxToolSchemaChars: 0,
            maxToolSchemaRoughTokens: 0,
            topToolSchemaGroups: [],
            alerts: [],
            alertCount: 0,
            slots: [],
            models: [],
            groupMap: new Map<string, CodexToolSchemaGroup>(),
            alertSet: new Set<string>(),
            slotSet: new Set<number>(),
            modelSet: new Set<string>(),
        };
        entry.requests += 1;
        entry.inputTokens += inputTokens;
        entry.cachedInputTokens += cachedInputTokens;
        entry.outputTokens += outputTokens;
        entry.totalTokens = entry.inputTokens + entry.outputTokens;
        entry.cachedPercent = entry.inputTokens > 0
            ? (entry.cachedInputTokens / entry.inputTokens) * 100
            : 0;
        entry.firstSeenAt = entry.firstSeenAt && timestamp && entry.firstSeenAt < timestamp ? entry.firstSeenAt : timestamp ?? entry.firstSeenAt;
        entry.lastSeenAt = entry.lastSeenAt && timestamp && entry.lastSeenAt > timestamp ? entry.lastSeenAt : timestamp ?? entry.lastSeenAt;
        entry.maxMessageCount = Math.max(entry.maxMessageCount, numberFromContext(context, 'msg_count'));
        entry.maxToolCount = Math.max(entry.maxToolCount, numberFromContext(context, 'tool_count'));
        entry.maxMessageChars = Math.max(entry.maxMessageChars, numberFromContext(context, 'message_chars'));
        entry.maxToolSchemaChars = Math.max(entry.maxToolSchemaChars, numberFromContext(context, 'tool_schema_chars'));
        entry.maxToolSchemaRoughTokens = Math.max(entry.maxToolSchemaRoughTokens, numberFromContext(context, 'tool_schema_rough_tokens'));
        for (const group of toolSchemaGroupsFromContext(context)) {
            const current = entry.groupMap.get(group.key) ?? { key: group.key, tools: 0, chars: 0 };
            current.tools = Math.max(current.tools, group.tools);
            current.chars = Math.max(current.chars, group.chars);
            entry.groupMap.set(group.key, current);
        }
        for (const alert of stringArrayFromContext(context, 'bloat_alerts')) {
            entry.alertSet.add(alert);
        }
        const slotIndex = Number(row['selected_codex_slot_index']);
        if (Number.isFinite(slotIndex)) entry.slotSet.add(slotIndex);
        if (typeof row['actual_model'] === 'string') entry.modelSet.add(row['actual_model']);
        rows.set(key, entry);
    }
    stmt.free();
    return [...rows.values()]
        .map((row) => {
            const { groupMap, alertSet, slotSet, modelSet, ...publicRow } = row;
            return {
                ...publicRow,
                topToolSchemaGroups: topToolSchemaGroups(groupMap),
                alerts: [...alertSet].sort(),
                alertCount: alertSet.size,
                slots: [...slotSet].sort((a, b) => a - b),
                models: [...modelSet].sort(),
            };
        })
        .sort((a, b) => b.inputTokens - a.inputTokens)
        .slice(0, 20);
}

function readQuotaCalibration(start: string, end: string): CodexQuotaCalibrationRow[] {
    const totals = readSlotWindowRequestTotals(start, end)
        .filter((row) => row.requests > 0 && row.actualQuotaDelta > 0);
    const byWindow = new Map<'fiveHour' | 'weekly', SlotWindowRequestTotals>();
    for (const row of totals) {
        const current = byWindow.get(row.window) ?? {
            slotIndex: -1,
            window: row.window,
            observedAt: '',
            resetAt: '',
            windowStart: '',
            windowMinutes: 0,
            requests: 0,
            inputTokens: 0,
            cachedInputTokens: 0,
            outputTokens: 0,
            totalTokens: 0,
            uncachedPlusOutputTokens: 0,
            apiCostUsd: 0,
            actualQuotaDelta: 0,
        };
        current.requests += row.requests;
        current.inputTokens += row.inputTokens;
        current.cachedInputTokens += row.cachedInputTokens;
        current.outputTokens += row.outputTokens;
        current.totalTokens += row.totalTokens;
        current.uncachedPlusOutputTokens += row.uncachedPlusOutputTokens;
        current.apiCostUsd += row.apiCostUsd;
        current.actualQuotaDelta += row.actualQuotaDelta;
        byWindow.set(row.window, current);
    }
    return Array.from(byWindow.values()).map((row) => ({
        window: row.window,
        observedQuotaDelta: row.actualQuotaDelta,
        totalTokens: row.totalTokens,
        uncachedPlusOutputTokens: row.uncachedPlusOutputTokens,
        quotaPctPerMillionTotalTokens: row.totalTokens > 0
            ? (row.actualQuotaDelta * 1_000_000) / row.totalTokens
            : 0,
        quotaPctPerMillionUncachedPlusOutput: row.uncachedPlusOutputTokens > 0
            ? (row.actualQuotaDelta * 1_000_000) / row.uncachedPlusOutputTokens
            : 0,
    }));
}

function readSlotUsageEstimates(start: string, end: string): CodexSlotUsageEstimate[] {
    const calibration = new Map(
        readQuotaCalibration(start, end).map((row) => [row.window, row.quotaPctPerMillionTotalTokens]),
    );
    return readSlotWindowRequestTotals(start, end).map((row) => {
        const rate = calibration.get(row.window) ?? 0;
        const expectedQuotaDelta = (row.totalTokens / 1_000_000) * rate;
        return {
            slotIndex: row.slotIndex,
            window: row.window,
            requests: row.requests,
            inputTokens: row.inputTokens,
            cachedInputTokens: row.cachedInputTokens,
            outputTokens: row.outputTokens,
            totalTokens: row.totalTokens,
            uncachedPlusOutputTokens: row.uncachedPlusOutputTokens,
            actualQuotaDelta: row.actualQuotaDelta,
            expectedQuotaDelta,
            varianceQuotaDelta: row.actualQuotaDelta - expectedQuotaDelta,
            expectedSource: 'calibrated_total_tokens',
            apiCostUsd: row.apiCostUsd,
        };
    });
}

function readSlotUsageChart(start: string, end: string, bucketFormat: 'day' | 'week'): CodexUsageChartPoint[] {
    if (!db) return [];
    const weeklyRate = readQuotaCalibration(start, end)
        .find((row) => row.window === 'weekly')?.quotaPctPerMillionTotalTokens ?? 0;
    const fiveHourRate = readQuotaCalibration(start, end)
        .find((row) => row.window === 'fiveHour')?.quotaPctPerMillionTotalTokens ?? 0;
    const bucketExpr = bucketFormat === 'week'
        ? "strftime('%Y-W%W', i.observed_at)"
        : "substr(i.observed_at, 1, 10)";
    const routingBucketExpr = bucketFormat === 'week'
        ? "strftime('%Y-W%W', datetime(r.timestamp))"
        : "substr(r.timestamp, 1, 10)";
    const stmt = db.prepare(`
        WITH snapshots AS (
            SELECT
                slot_index,
                window,
                observed_at,
                ${bucketExpr} AS bucket,
                used_percent,
                row_number() OVER (
                    PARTITION BY slot_index, window, ${bucketExpr}
                    ORDER BY observed_at DESC
                ) AS rn
            FROM codex_usage_snapshot_history i
            WHERE observed_at >= ? AND observed_at < ?
        ),
        latest_bucket_snapshots AS (
            SELECT
                bucket,
                slot_index,
                window,
                used_percent
            FROM snapshots
            WHERE rn = 1
        ),
        request_buckets AS (
            SELECT
                ${routingBucketExpr} AS bucket,
                r.selected_codex_slot_index AS slot_index,
                COALESCE(COUNT(r.id), 0) AS requests,
                COALESCE(SUM(r.input_tokens + r.output_tokens), 0) AS total_tokens,
                COALESCE(SUM(
                    CASE r.actual_model
                        WHEN 'codex/gpt-5.5' THEN ((r.input_tokens - r.cached_input_tokens) * 5.00 + r.cached_input_tokens * 0.50 + r.output_tokens * 30.00) / 1000000.0
                        WHEN 'codex/gpt-5.4' THEN ((r.input_tokens - r.cached_input_tokens) * 2.50 + r.cached_input_tokens * 0.25 + r.output_tokens * 15.00) / 1000000.0
                        WHEN 'codex/gpt-5.4-mini' THEN ((r.input_tokens - r.cached_input_tokens) * 0.75 + r.cached_input_tokens * 0.075 + r.output_tokens * 4.50) / 1000000.0
                        ELSE 0
                    END
                ), 0) AS api_cost_usd,
                MAX(r.timestamp) AS last_request_at
            FROM routing_log r
            WHERE datetime(r.timestamp) >= datetime(?) AND datetime(r.timestamp) < datetime(?)
              AND r.actual_model LIKE 'codex/%'
              AND r.selected_codex_slot_index IS NOT NULL
            GROUP BY ${routingBucketExpr}, r.selected_codex_slot_index
        )
        SELECT
            rb.bucket,
            rb.slot_index,
            rb.requests,
            rb.total_tokens,
            rb.api_cost_usd,
            COALESCE(MAX(CASE WHEN lbs.window = 'weekly' THEN lbs.used_percent END), 0) AS weekly_actual,
            COALESCE(MAX(CASE WHEN lbs.window = 'fiveHour' THEN lbs.used_percent END), 0) AS five_hour_actual
        FROM request_buckets rb
        LEFT JOIN latest_bucket_snapshots lbs
            ON lbs.bucket = rb.bucket
           AND lbs.slot_index = rb.slot_index
        GROUP BY rb.bucket, rb.slot_index, rb.requests, rb.total_tokens, rb.api_cost_usd
        ORDER BY rb.bucket, rb.slot_index
    `);
    stmt.bind([start, end, start, end]);
    const rows: CodexUsageChartPoint[] = [];
    while (stmt.step()) {
        const row = stmt.getAsObject() as Record<string, unknown>;
        const totalTokens = Number(row['total_tokens']) || 0;
        rows.push({
            bucket: String(row['bucket']),
            slotIndex: Number(row['slot_index']),
            requests: Number(row['requests']) || 0,
            totalTokens,
            apiCostUsd: Number(row['api_cost_usd']) || 0,
            weeklyActualQuotaDelta: Number(row['weekly_actual']) || 0,
            weeklyExpectedQuotaDelta: (totalTokens / 1_000_000) * weeklyRate,
            fiveHourActualQuotaDelta: Number(row['five_hour_actual']) || 0,
            fiveHourExpectedQuotaDelta: (totalTokens / 1_000_000) * fiveHourRate,
        });
    }
    stmt.free();
    return rows;
}

function hasRows(sql: string, params: Array<string | number | null>): boolean {
    if (!db) return false;
    const stmt = db.prepare(sql);
    stmt.bind(params);
    const present = stmt.step() && Number(stmt.get()[0]) > 0;
    stmt.free();
    return present;
}

export function getCodexAnalysis(input: {
    periodKey: string;
    start: string;
    end: string;
}): CodexAnalysisResponse {
    const startDay = input.start.slice(0, 10);
    const endDay = input.end.slice(0, 10);
    const quotaHistory = readQuotaHistory(input.start, input.end);
    return {
        period: {
            key: input.periodKey,
            start: input.start,
            end: input.end,
        },
        summary: readAnalysisSummary(input.start, input.end),
        daily: readAnalysisDaily(input.start, input.end),
        modelMix: readAnalysisMix('actual_model', input.start, input.end),
        tierMix: readAnalysisMix('tier', input.start, input.end),
        apiKindMix: readAnalysisMix('request_api_kind', input.start, input.end),
        reasoningEffortMix: readAnalysisMix('requested_reasoning_effort', input.start, input.end),
        quotaSnapshots: getCodexUsageSnapshots(),
        quotaHistory,
        activationCheckpoints: getCodexActivationCheckpoints(),
        dailyRollups: readDailyRollups(startDay, endDay),
        quotaCalibration: readQuotaCalibration(input.start, input.end),
        slotUsageEstimates: readSlotUsageEstimates(input.start, input.end),
        dailySlotUsage: readSlotUsageChart(input.start, input.end, 'day'),
        weeklySlotUsage: readSlotUsageChart(input.start, input.end, 'week'),
        expensiveConversations: readCodexBloatConversations(input.start, input.end),
        apiPricing: {
            source: CODEX_API_PRICING_SOURCE,
            currency: 'USD',
            unit: 'per_1m_tokens',
            models: CODEX_API_PRICING,
        },
        flags: {
            hasQuotaHistory: quotaHistory.length > 0,
            hasSelectedCodexAttribution: hasRows(
                `SELECT COUNT(*) FROM routing_log
                 WHERE timestamp >= ? AND timestamp < ?
                   AND selected_codex_account_key IS NOT NULL`,
                [input.start, input.end],
            ),
            hasReasoningEffort: hasRows(
                `SELECT COUNT(*) FROM routing_log
                 WHERE timestamp >= ? AND timestamp < ?
                   AND requested_reasoning_effort IS NOT NULL`,
                [input.start, input.end],
            ),
        },
    };
}

function readCodexAccountScheduleRows(): CodexAccountScheduleRow[] {
    if (!db) return [];

    const stmt = db.prepare(
        `SELECT account_key, slot_index, seed_order, anchor_weekday, lane_rank, updated_at
         FROM codex_account_schedule
         ORDER BY seed_order ASC`
    );
    const rows: CodexAccountScheduleRow[] = [];

    try {
        while (stmt.step()) {
            const row = stmt.getAsObject() as Record<string, unknown>;
            rows.push({
                accountKey: row['account_key'] as string,
                slotIndex: Number(row['slot_index']),
                seedOrder: Number(row['seed_order']),
                anchorWeekday: Number(row['anchor_weekday']),
                laneRank: Number(row['lane_rank']),
                updatedAt: row['updated_at'] as string,
            });
        }
    } finally {
        stmt.free();
    }

    return rows;
}

export function getCodexAccountSchedule(): CodexAccountScheduleRow[] {
    try {
        return readCodexAccountScheduleRows();
    } catch (error) {
        console.warn('Failed to load Codex account schedule:', error);
        return [];
    }
}

export function seedCodexAccountSchedule(
    slots: readonly (string | { accountKey: string; slotIndex: number })[],
    startWeekday: number,
    options: { flush?: boolean } = {},
): CodexAccountScheduleRow[] {
    if (!db) return [];

    if (slots.every((slot) => typeof slot === 'string')) {
        const uniqueAccountKeys = Array.from(new Set(
            (slots as readonly string[]).filter((accountKey) => accountKey.length > 0),
        ));
        if (uniqueAccountKeys.length === 0) return getCodexAccountSchedule();
        try {
            const existing = readCodexAccountScheduleRows();
            const existingKeys = new Set(existing.map((row) => row.accountKey));
            let maxSeedOrder = existing.reduce((max, row) => Math.max(max, row.seedOrder), -1);
            const now = new Date().toISOString();
            let inserted = false;
            db.run('BEGIN');
            for (const accountKey of uniqueAccountKeys) {
                if (existingKeys.has(accountKey)) continue;
                maxSeedOrder += 1;
                db.run(
                    `INSERT INTO codex_account_schedule (
                        account_key, slot_index, seed_order, anchor_weekday, lane_rank, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)`,
                    [
                        accountKey,
                        maxSeedOrder,
                        maxSeedOrder,
                        (startWeekday + (maxSeedOrder % 7)) % 7,
                        Math.floor(maxSeedOrder / 7),
                        now,
                    ],
                );
                existingKeys.add(accountKey);
                inserted = true;
            }
            db.run('COMMIT');
            if (inserted) {
                if (options.flush) persistDb();
                else schedulePersistDb();
            }
            return readCodexAccountScheduleRows();
        } catch (error) {
            try {
                db.run('ROLLBACK');
            } catch {
                // Best effort cleanup.
            }
            console.warn('Failed to seed Codex account schedule:', error);
            return getCodexAccountSchedule();
        }
    }

    const normalized = slots
        .map((slot) => slot as { accountKey: string; slotIndex: number })
        .filter((slot) => slot.accountKey.length > 0 && slot.slotIndex >= 0);
    const uniqueSlots = Array.from(new Map(normalized.map((slot) => [slot.slotIndex, slot])).values())
        .sort((left, right) => left.slotIndex - right.slotIndex);
    if (uniqueSlots.length === 0) return getCodexAccountSchedule();

    try {
        const existing = readCodexAccountScheduleRows();
        const now = new Date().toISOString();
        const expected = uniqueSlots.map(({ accountKey, slotIndex }) => ({
            accountKey,
            slotIndex,
            seedOrder: slotIndex,
            anchorWeekday: (startWeekday + (slotIndex % 7)) % 7,
            laneRank: Math.floor(slotIndex / 7),
        }));
        const unchanged = expected.length === existing.length
            && expected.every((row, index) => {
                const current = existing[index];
                return current?.accountKey === row.accountKey
                    && current.slotIndex === row.slotIndex
                    && current.seedOrder === row.seedOrder
                    && current.anchorWeekday === row.anchorWeekday
                    && current.laneRank === row.laneRank;
            });
        if (unchanged) return existing;

        db.run('BEGIN');
        for (const row of expected) {
            const previous = existing.find((candidate) =>
                candidate.slotIndex === row.slotIndex
                || (candidate.slotIndex < 0 && candidate.seedOrder === row.slotIndex));
            if (previous && previous.accountKey !== row.accountKey) {
                db.run('DELETE FROM codex_usage_snapshots WHERE slot_index = ?', [row.slotIndex]);
                db.run('DELETE FROM codex_activation_checkpoints WHERE slot_index = ?', [row.slotIndex]);
                db.run('DELETE FROM codex_reset_credit_items WHERE slot_index = ?', [row.slotIndex]);
                db.run('DELETE FROM codex_reset_credit_summaries WHERE slot_index = ?', [row.slotIndex]);
            }
        }
        db.run('DELETE FROM codex_account_schedule');
        for (const row of expected) {
            db.run(
                `INSERT INTO codex_account_schedule (
                    account_key, slot_index, seed_order, anchor_weekday, lane_rank, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)`,
                [row.accountKey, row.slotIndex, row.seedOrder, row.anchorWeekday, row.laneRank, now],
            );
        }
        db.run('COMMIT');

        if (options.flush) {
            persistDb();
        } else {
            schedulePersistDb();
        }

        return readCodexAccountScheduleRows();
    } catch (error) {
        try {
            db.run('ROLLBACK');
        } catch {
            // Best effort cleanup.
        }
        console.warn('Failed to seed Codex account schedule:', error);
        return getCodexAccountSchedule();
    }
}

export function getCodexBalancerSettings(): Partial<CodexBalancerSettings> | null {
    if (!db) return null;
    const result = db.exec(
        `SELECT mode, early_activation_enabled, early_activation_weekly_percent,
                cold_migration_five_hour_threshold_percent
         FROM codex_balancer_settings WHERE id = 1`,
    )[0];
    const row = result?.values[0];
    if (!row) return null;
    return {
        ...(typeof row[0] === 'string' ? { mode: row[0] as CodexBalancerSettings['mode'] } : {}),
        ...(row[1] !== null ? { earlyActivationEnabled: Number(row[1]) === 1 } : {}),
        ...(row[2] !== null ? { earlyActivationWeeklyPercent: Number(row[2]) } : {}),
        ...(row[3] !== null ? { coldMigrationFiveHourThresholdPercent: Number(row[3]) } : {}),
    };
}

export function setCodexBalancerSettings(settings: Partial<CodexBalancerSettings> | null): void {
    if (!db) return;
    if (settings === null) {
        db.run('DELETE FROM codex_balancer_settings WHERE id = 1');
        schedulePersistDb();
        return;
    }
    const current = getCodexBalancerSettings() ?? {};
    const next = { ...current, ...settings };
    db.run(
        `INSERT INTO codex_balancer_settings (
            id, mode, early_activation_enabled, early_activation_weekly_percent,
            cold_migration_five_hour_threshold_percent, updated_at
        ) VALUES (1, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            mode = excluded.mode,
            early_activation_enabled = excluded.early_activation_enabled,
            early_activation_weekly_percent = excluded.early_activation_weekly_percent,
            cold_migration_five_hour_threshold_percent = excluded.cold_migration_five_hour_threshold_percent,
            updated_at = excluded.updated_at`,
        [
            next.mode ?? null,
            next.earlyActivationEnabled === undefined ? null : Number(next.earlyActivationEnabled),
            next.earlyActivationWeeklyPercent ?? null,
            next.coldMigrationFiveHourThresholdPercent ?? null,
            new Date().toISOString(),
        ],
    );
    schedulePersistDb();
}

export function getCodexBalancerSlotOverrides(): CodexBalancerSlotOverride[] {
    if (!db) return [];
    const result = db.exec(
        `SELECT slot_index, enabled, manual_activation_cycle_reset_at, updated_at
         FROM codex_balancer_slot_overrides ORDER BY slot_index`,
    )[0];
    return (result?.values ?? []).map((row) => ({
        slotIndex: Number(row[0]),
        enabled: Number(row[1]) === 1,
        manualActivationCycleResetAt: typeof row[2] === 'string' ? row[2] : null,
        updatedAt: String(row[3]),
    }));
}

export function setCodexBalancerSlotOverride(override: CodexBalancerSlotOverride): void {
    if (!db) return;
    db.run(
        `INSERT INTO codex_balancer_slot_overrides (
            slot_index, enabled, manual_activation_cycle_reset_at, updated_at
        ) VALUES (?, ?, ?, ?)
        ON CONFLICT(slot_index) DO UPDATE SET
            enabled = excluded.enabled,
            manual_activation_cycle_reset_at = excluded.manual_activation_cycle_reset_at,
            updated_at = excluded.updated_at`,
        [
            override.slotIndex,
            Number(override.enabled),
            override.manualActivationCycleResetAt,
            override.updatedAt,
        ],
    );
    schedulePersistDb();
}

export function getCodexActivationCheckpoints(): CodexActivationCheckpoint[] {
    if (!db) return [];
    const result = db.exec(
        `SELECT slot_index, account_key, expected_weekly_reset_at, last_usage_check_at, updated_at
         FROM codex_activation_checkpoints ORDER BY slot_index`,
    )[0];
    return (result?.values ?? []).map((row) => ({
        slotIndex: Number(row[0]),
        accountKey: String(row[1]),
        expectedWeeklyResetAt: typeof row[2] === 'string' ? row[2] : null,
        lastUsageCheckAt: typeof row[3] === 'string' ? row[3] : null,
        updatedAt: String(row[4]),
    }));
}

export function setCodexActivationCheckpoint(checkpoint: CodexActivationCheckpoint): void {
    if (!db) return;
    db.run(
        `INSERT INTO codex_activation_checkpoints (
            slot_index, account_key, expected_weekly_reset_at, last_usage_check_at, updated_at
        ) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(slot_index) DO UPDATE SET
            account_key = excluded.account_key,
            expected_weekly_reset_at = excluded.expected_weekly_reset_at,
            last_usage_check_at = excluded.last_usage_check_at,
            updated_at = excluded.updated_at`,
        [
            checkpoint.slotIndex,
            checkpoint.accountKey,
            checkpoint.expectedWeeklyResetAt,
            checkpoint.lastUsageCheckAt,
            checkpoint.updatedAt,
        ],
    );
    schedulePersistDb();
}

export function clearCodexBalancerOverrides(): void {
    if (!db) return;
    db.run('DELETE FROM codex_balancer_settings');
    db.run('DELETE FROM codex_balancer_slot_overrides');
    schedulePersistDb();
}

export function logCodexBalancerAudit(input: Omit<CodexBalancerAuditRow, 'id' | 'timestamp'>): void {
    if (!db) return;
    db.run(
        `INSERT INTO codex_balancer_audit (
            timestamp, action, slot_index, previous_value, next_value
        ) VALUES (?, ?, ?, ?, ?)`,
        [new Date().toISOString(), input.action, input.slotIndex, input.previousValue, input.nextValue],
    );
    schedulePersistDb();
}

export function getCodexBalancerAudit(limit = 20): CodexBalancerAuditRow[] {
    if (!db) return [];
    const stmt = db.prepare(
        `SELECT id, timestamp, action, slot_index, previous_value, next_value
         FROM codex_balancer_audit ORDER BY id DESC LIMIT ?`,
    );
    stmt.bind([limit]);
    const rows: CodexBalancerAuditRow[] = [];
    while (stmt.step()) {
        const row = stmt.getAsObject() as Record<string, unknown>;
        rows.push({
            id: Number(row['id']),
            timestamp: String(row['timestamp']),
            action: String(row['action']),
            slotIndex: row['slot_index'] === null ? null : Number(row['slot_index']),
            previousValue: typeof row['previous_value'] === 'string' ? row['previous_value'] : null,
            nextValue: typeof row['next_value'] === 'string' ? row['next_value'] : null,
        });
    }
    stmt.free();
    return rows;
}

export function setLastCodexBalancerDecision(decision: CodexBalancerDecision): void {
    if (!db) return;
    db.run(
        `INSERT INTO codex_balancer_decision (
            id, timestamp, slot_index, account_key, mode, reason, fallback_reason,
            weekly_used_percent, weekly_reset_at, required_burn_rate
        ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            timestamp = excluded.timestamp,
            slot_index = excluded.slot_index,
            account_key = excluded.account_key,
            mode = excluded.mode,
            reason = excluded.reason,
            fallback_reason = excluded.fallback_reason,
            weekly_used_percent = excluded.weekly_used_percent,
            weekly_reset_at = excluded.weekly_reset_at,
            required_burn_rate = excluded.required_burn_rate`,
        [
            decision.timestamp, decision.slotIndex, decision.accountKey, decision.mode,
            decision.reason, decision.fallbackReason, decision.weeklyUsedPercent,
            decision.weeklyResetAt, decision.requiredBurnRate,
        ],
    );
    schedulePersistDb();
}

export function getLastCodexBalancerDecision(): CodexBalancerDecision | null {
    if (!db) return null;
    const result = db.exec(
        `SELECT timestamp, slot_index, account_key, mode, reason, fallback_reason,
                weekly_used_percent, weekly_reset_at, required_burn_rate
         FROM codex_balancer_decision WHERE id = 1`,
    )[0];
    const row = result?.values[0];
    if (!row) return null;
    return {
        timestamp: String(row[0]),
        slotIndex: row[1] === null ? null : Number(row[1]),
        accountKey: typeof row[2] === 'string' ? row[2] : null,
        mode: row[3] as CodexBalancerDecision['mode'],
        reason: typeof row[4] === 'string' ? row[4] : null,
        fallbackReason: typeof row[5] === 'string' ? row[5] : null,
        weeklyUsedPercent: row[6] === null ? null : Number(row[6]),
        weeklyResetAt: typeof row[7] === 'string' ? row[7] : null,
        requiredBurnRate: row[8] === null ? null : Number(row[8]),
    };
}

function pruneExpiredCodexColdMigrationDecisions(now = new Date().toISOString()): void {
    if (!db) return;
    db.run('DELETE FROM codex_cold_migration_decisions WHERE expires_at < ?', [now]);
}

function coldMigrationDecisionFromRow(row: Record<string, unknown>): CodexColdMigrationDecision {
    return {
        id: String(row['id']),
        status: String(row['status']) as CodexColdMigrationDecisionStatus,
        createdAt: String(row['created_at']),
        updatedAt: String(row['updated_at']),
        expiresAt: String(row['expires_at']),
        approvedAt: typeof row['approved_at'] === 'string' ? row['approved_at'] : null,
        dismissedAt: typeof row['dismissed_at'] === 'string' ? row['dismissed_at'] : null,
        consumedAt: typeof row['consumed_at'] === 'string' ? row['consumed_at'] : null,
        sessionKey: String(row['session_key']),
        previousAccountKey: String(row['previous_account_key']),
        previousSlotIndex: row['previous_slot_index'] === null ? null : Number(row['previous_slot_index']),
        targetAccountKey: String(row['target_account_key']),
        targetSlotIndex: Number(row['target_slot_index']),
        estimatedInputTokens: Number(row['estimated_input_tokens']) || 0,
        estimatedFiveHourPercent: Number(row['estimated_five_hour_percent']) || 0,
        thresholdFiveHourPercent: Number(row['threshold_five_hour_percent']) || 0,
        previousFiveHourUsedPercent: row['previous_five_hour_used_percent'] === null ? null : Number(row['previous_five_hour_used_percent']),
        previousFiveHourRemainingPercent: row['previous_five_hour_remaining_percent'] === null ? null : Number(row['previous_five_hour_remaining_percent']),
        targetFiveHourUsedPercent: row['target_five_hour_used_percent'] === null ? null : Number(row['target_five_hour_used_percent']),
        targetFiveHourRemainingPercent: row['target_five_hour_remaining_percent'] === null ? null : Number(row['target_five_hour_remaining_percent']),
        targetWeeklyUsedPercent: row['target_weekly_used_percent'] === null ? null : Number(row['target_weekly_used_percent']),
        targetWeeklyRemainingPercent: row['target_weekly_remaining_percent'] === null ? null : Number(row['target_weekly_remaining_percent']),
        reason: 'cold_prompt_cache_migration',
    };
}

export function getCodexColdMigrationDecision(id: string): CodexColdMigrationDecision | null {
    if (!db || !id) return null;
    pruneExpiredCodexColdMigrationDecisions();
    const stmt = db.prepare('SELECT * FROM codex_cold_migration_decisions WHERE id = ?');
    stmt.bind([id]);
    const row = stmt.step() ? coldMigrationDecisionFromRow(stmt.getAsObject() as Record<string, unknown>) : null;
    stmt.free();
    return row;
}

export function getPendingCodexColdMigrationDecisions(limit = 20): CodexColdMigrationDecision[] {
    if (!db) return [];
    pruneExpiredCodexColdMigrationDecisions();
    const safeLimit = Math.max(1, Math.min(100, Math.floor(limit)));
    const stmt = db.prepare(`
        SELECT * FROM codex_cold_migration_decisions
        WHERE status = 'pending'
        ORDER BY created_at DESC
        LIMIT ?
    `);
    stmt.bind([safeLimit]);
    const rows: CodexColdMigrationDecision[] = [];
    while (stmt.step()) rows.push(coldMigrationDecisionFromRow(stmt.getAsObject() as Record<string, unknown>));
    stmt.free();
    return rows;
}

export function upsertCodexColdMigrationDecision(
    decision: Omit<CodexColdMigrationDecision, 'status' | 'createdAt' | 'updatedAt' | 'approvedAt' | 'dismissedAt' | 'consumedAt' | 'reason'>,
): CodexColdMigrationDecision | null {
    if (!db) return null;
    pruneExpiredCodexColdMigrationDecisions();
    const now = new Date().toISOString();
    const existing = getCodexColdMigrationDecision(decision.id);
    if (existing && existing.status !== 'pending') return existing;
    db.run(
        `INSERT INTO codex_cold_migration_decisions (
            id, status, created_at, updated_at, expires_at, approved_at, dismissed_at, consumed_at,
            session_key, previous_account_key, previous_slot_index, target_account_key, target_slot_index,
            estimated_input_tokens, estimated_five_hour_percent, threshold_five_hour_percent,
            previous_five_hour_used_percent, previous_five_hour_remaining_percent,
            target_five_hour_used_percent, target_five_hour_remaining_percent,
            target_weekly_used_percent, target_weekly_remaining_percent, reason
        ) VALUES (?, 'pending', ?, ?, ?, NULL, NULL, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'cold_prompt_cache_migration')
        ON CONFLICT(id) DO UPDATE SET
            status = 'pending',
            updated_at = excluded.updated_at,
            expires_at = excluded.expires_at,
            estimated_input_tokens = excluded.estimated_input_tokens,
            estimated_five_hour_percent = excluded.estimated_five_hour_percent,
            threshold_five_hour_percent = excluded.threshold_five_hour_percent,
            previous_five_hour_used_percent = excluded.previous_five_hour_used_percent,
            previous_five_hour_remaining_percent = excluded.previous_five_hour_remaining_percent,
            target_five_hour_used_percent = excluded.target_five_hour_used_percent,
            target_five_hour_remaining_percent = excluded.target_five_hour_remaining_percent,
            target_weekly_used_percent = excluded.target_weekly_used_percent,
            target_weekly_remaining_percent = excluded.target_weekly_remaining_percent`,
        [
            decision.id, now, now, decision.expiresAt,
            decision.sessionKey, decision.previousAccountKey, decision.previousSlotIndex,
            decision.targetAccountKey, decision.targetSlotIndex,
            decision.estimatedInputTokens, decision.estimatedFiveHourPercent, decision.thresholdFiveHourPercent,
            decision.previousFiveHourUsedPercent, decision.previousFiveHourRemainingPercent,
            decision.targetFiveHourUsedPercent, decision.targetFiveHourRemainingPercent,
            decision.targetWeeklyUsedPercent, decision.targetWeeklyRemainingPercent,
        ],
    );
    schedulePersistDb();
    return getCodexColdMigrationDecision(decision.id);
}

export function approveCodexColdMigrationDecision(id: string): CodexColdMigrationDecision | null {
    if (!db) return null;
    pruneExpiredCodexColdMigrationDecisions();
    const current = getCodexColdMigrationDecision(id);
    if (!current || current.status !== 'pending') return current;
    const now = new Date().toISOString();
    db.run(
        `UPDATE codex_cold_migration_decisions
         SET status = 'approved', approved_at = ?, updated_at = ?
         WHERE id = ? AND status = 'pending'`,
        [now, now, id],
    );
    schedulePersistDb();
    return getCodexColdMigrationDecision(id);
}

export function dismissCodexColdMigrationDecision(id: string): CodexColdMigrationDecision | null {
    if (!db) return null;
    pruneExpiredCodexColdMigrationDecisions();
    const current = getCodexColdMigrationDecision(id);
    if (!current || current.status !== 'pending') return current;
    const now = new Date().toISOString();
    db.run(
        `UPDATE codex_cold_migration_decisions
         SET status = 'dismissed', dismissed_at = ?, updated_at = ?
         WHERE id = ? AND status = 'pending'`,
        [now, now, id],
    );
    schedulePersistDb();
    return getCodexColdMigrationDecision(id);
}

export function hasApprovedCodexColdMigrationDecision(input: {
    id: string;
    sessionKey: string;
    targetAccountKey: string;
    targetSlotIndex: number;
}): boolean {
    const decision = getCodexColdMigrationDecision(input.id);
    return Boolean(decision
        && decision.status === 'approved'
        && decision.sessionKey === input.sessionKey
        && decision.targetAccountKey === input.targetAccountKey
        && decision.targetSlotIndex === input.targetSlotIndex);
}

export function consumeCodexColdMigrationDecision(id: string): void {
    if (!db || !id) return;
    const current = getCodexColdMigrationDecision(id);
    if (!current || current.status !== 'approved') return;
    const now = new Date().toISOString();
    db.run(
        `UPDATE codex_cold_migration_decisions
         SET status = 'consumed', consumed_at = ?, updated_at = ?
         WHERE id = ? AND status = 'approved'`,
        [now, now, id],
    );
    schedulePersistDb();
}

export function getLiveQuotaCalibration(now = new Date()): LiveQuotaCalibration {
    return liveQuotaCalibration(now);
}

// === Pruning ===

/**
 * Delete log entries older than the specified number of days.
 *
 * @param retentionDays - Number of days to retain
 * @returns Number of rows deleted
 */
export function pruneOldEntries(retentionDays: number): number {
    if (!db) return 0;

    try {
        const cutoff = new Date(
            Date.now() - retentionDays * 24 * 60 * 60 * 1000
        ).toISOString();

        db.run(`DELETE FROM routing_log WHERE timestamp < ?`, [cutoff]);

        // sql.js doesn't have a changes() API, so we track it via a count query
        let deleted = 0;
        const result = db.exec(`SELECT changes() as count`);
        const firstResult = result[0];
        if (firstResult) {
            const firstRow = firstResult.values[0];
            if (firstRow && firstRow[0] != null) {
                deleted = Number(firstRow[0]);
            }
        }

        if (deleted > 0) {
            rebuildRoutingDailyRollups(db);
            persistDb();
        }

        return deleted;
    } catch (error) {
        console.warn('Failed to prune old entries:', error);
        return 0;
    }
}

// === Shutdown ===

/**
 * Close the database and persist to disk.
 */
export function closeDb(): void {
    if (!db) return;

    try {
        flushPendingPersist();
        persistDb();
        db.close();
    } catch (error) {
        console.warn('Failed to close database:', error);
    } finally {
        if (persistTimer) {
            clearTimeout(persistTimer);
            persistTimer = null;
        }
        persistPending = false;
        db = null;
    }
}
