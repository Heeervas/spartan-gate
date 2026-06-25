/**
 * ClawRoute Type Definitions
 *
 * All TypeScript types and interfaces for ClawRoute.
 * Every other module imports from this file.
 */

// === Task Classification ===

/**
 * Task complexity tiers for classification.
 * Used to determine which model should handle a request.
 */
export enum TaskTier {
    HEARTBEAT = 'heartbeat',
    SIMPLE = 'simple',
    MODERATE = 'moderate',
    COMPLEX = 'complex',
    FRONTIER_SONNET = 'frontier-sonnet',
    FRONTIER_OPUS   = 'frontier-opus',
}

/**
 * Numeric ordering for tier comparison and escalation.
 * Higher numbers = more capable (and expensive) models.
 */
export const TIER_ORDER: Record<TaskTier, number> = {
    [TaskTier.HEARTBEAT]: 0,
    [TaskTier.SIMPLE]: 1,
    [TaskTier.MODERATE]: 2,
    [TaskTier.COMPLEX]:         3,
    [TaskTier.FRONTIER_SONNET]: 4,
    [TaskTier.FRONTIER_OPUS]:   5,
};

/**
 * Result from the classifier.
 */
export interface ClassificationResult {
    /** The determined task tier */
    tier: TaskTier;
    /** Confidence score from 0.0 to 1.0 */
    confidence: number;
    /** Human-readable explanation */
    reason: string;
    /** List of classification rules that fired */
    signals: string[];
    /** Whether tool definitions were detected in the request */
    toolsDetected: boolean;
    /** Whether it's safe to retry if this model fails (no tool side-effects expected) */
    safeToRetry: boolean;
}

// === Model Registry ===

/**
 * Supported LLM providers.
 */
export type ProviderType = 'anthropic' | 'openai' | 'codex' | 'google' | 'deepseek' | 'openrouter' | 'ollama' | 'x-ai' | 'stepfun';

export const PROVIDER_TYPES: ProviderType[] = [
    'anthropic',
    'openai',
    'codex',
    'google',
    'deepseek',
    'openrouter',
    'ollama',
    'x-ai',
    'stepfun',
];

export function isProviderType(value: unknown): value is ProviderType {
    return typeof value === 'string' && PROVIDER_TYPES.includes(value as ProviderType);
}

/**
 * Model entry with cost and capability information.
 */
export interface ModelEntry {
    /** Unique model identifier, e.g., "anthropic/claude-sonnet-4-5" */
    id: string;
    /** The provider for this model */
    provider: ProviderType;
    /** Cost in USD per 1M input tokens */
    inputCostPer1M: number;
    /** Cost in USD per 1M output tokens */
    outputCostPer1M: number;
    /** Maximum context window in tokens */
    maxContext: number;
    /** Whether the model reliably handles function/tool calling */
    toolCapable: boolean;
    /** Whether the model supports images/multimodal input */
    multimodal: boolean;
    /** Whether this model is enabled for routing */
    enabled: boolean;
}

// === Routing ===

/**
 * The routing decision made for a request.
 */
export interface RoutingDecision {
    /** The model the user originally configured */
    originalModel: string;
    /** The model ClawRoute chose to use */
    routedModel: string;
    /** The classification tier */
    tier: TaskTier;
    /** Reason for this routing decision */
    reason: string;
    /** Classification confidence */
    confidence: number;
    /** If true, this is a dry-run (routed to original, just logging) */
    isDryRun: boolean;
    /** If true, user forced this model via override */
    isOverride: boolean;
    /** If true, ClawRoute is disabled or errored - passthrough mode */
    isPassthrough: boolean;
    /** Estimated savings in USD */
    estimatedSavingsUsd: number;
    /** Estimated prompt tokens from routing, reused by execution cost estimates */
    estimatedInputTokens?: number;
    /** Whether it's safe to retry on failure */
    safeToRetry: boolean;
}

// === Execution ===

/**
 * Result from executing a request through ClawRoute.
 */
export interface ExecutionResult {
    /** The HTTP response to send back to the client */
    response: Response;
    /** The routing decision that was made */
    routingDecision: RoutingDecision;
    /** The final model used (may differ if escalated) */
    actualModel: string;
    /** Whether the request was escalated to a higher-tier model */
    escalated: boolean;
    /** Chain of models tried, e.g., ["flash-lite", "sonnet"] */
    escalationChain: string[];
    /** Number of input tokens used */
    inputTokens: number;
    /** Number of output tokens generated */
    outputTokens: number;
    /** Number of input tokens served from the provider prompt cache */
    cachedInputTokens?: number;
    /** What it would have cost with the original model */
    originalCostUsd: number;
    /** What it actually cost */
    actualCostUsd: number;
    /** Amount saved */
    savingsUsd: number;
    /** Response time in milliseconds */
    responseTimeMs: number;
    /** Whether the response contained tool calls */
    hadToolCalls: boolean;
    /** Terminal error observed while draining a streaming response */
    streamError?: string | null;
    /** Selected Codex auth slot for the final upstream attempt */
    selectedCodexSlotIndex?: number | null;
    /** Stable hashed Codex account key for the final upstream attempt */
    selectedCodexAccountKey?: string | null;
    /** ClawRoute policy block metadata for preflight-denied requests */
    policyBlock?: {
        policy: string;
        breakerId?: string | null;
        blockReason?: string | null;
        promptCacheKeyHash?: string | null;
        toolSchemaFingerprint?: string | null;
        estimatedInputTokens?: number | null;
    };
    /**
     * For streaming responses: called by executor after stream completes
     * with final token counts already written back to this result object.
     * Server assigns this after executeRequest() returns.
     */
    logWhenDone?: () => void;
}

export interface RequestExecutionContext {
    sessionId: string | null;
    promptCacheKey?: string | null;
    codexSelection?: {
        slotIndex: number;
        accountKey: string | null;
    } | null;
}

// === Config ===

/**
 * Model configuration for a specific tier.
 */
export interface TierModelConfig {
    /** Primary model ID for this tier */
    primary: string;
    /** Fallback model ID if primary unavailable */
    fallback: string;
}

/**
 * File-backed routing snapshot that can be rebuilt without restarting the server.
 */
export interface RoutingSnapshot {
    /** Selected provider profile */
    providerProfile: string | null;
    /** Model used for savings comparison */
    baselineModel: string;
    /** Tier mappings used by routing */
    models: Record<TaskTier, TierModelConfig>;
    /** Per-model context overrides */
    contextOverrides?: Record<string, number>;
    /** Frozen model catalog for this snapshot */
    modelCatalog: ModelEntry[];
}

/**
 * Session-specific model override.
 */
export interface SessionOverride {
    /** Model to use for this session */
    model: string;
    /** Number of turns remaining (null = permanent) */
    remainingTurns: number | null;
    /** When the override was created */
    createdAt: string;
}

/**
 * Complete ClawRoute configuration.
 */
export interface ClawRouteConfig {
    /** Whether ClawRoute routing is enabled */
    enabled: boolean;
    /** Dry-run mode: classify + log, but use original model */
    dryRun: boolean;
    /** The fallback model to use for savings comparison if the request doesn't specify an original cost */
    baselineModel: string;
    /**
     * Provider profile to load from config/providers/<name>.json.
     * Sets all tier model mappings in one field.
     * Overridden by CLAWROUTE_PROVIDER env var.
     * Built-in profiles: openrouter | codex | anthropic | openai
     */
    providerProfile: string | null;
    /** Port to listen on */
    proxyPort: number;
    /** Host to bind to (always 127.0.0.1 by default) */
    proxyHost: string;
    /** Optional shared secret for authentication */
    authToken: string | null;

    /** Classification settings */
    classification: {
        /** If true, low confidence → escalate UP */
        conservativeMode: boolean;
        /** Minimum confidence threshold (below this → escalate) */
        minConfidence: number;
        /** If tools present → minimum COMPLEX tier */
        toolAwareRouting: boolean;
    };

    /** Escalation settings */
    escalation: {
        /** Whether to enable automatic escalation */
        enabled: boolean;
        /** Maximum retry attempts */
        maxRetries: number;
        /** Delay between retries in ms */
        retryDelayMs: number;
        /** CRITICAL: Only retry before streaming starts */
        onlyRetryBeforeStreaming: boolean;
        /** CRITICAL: Only retry if no tool calls in response */
        onlyRetryWithoutToolCalls: boolean;
        /** Final safety net: always use original model if all else fails */
        alwaysFallbackToOriginal: boolean;
    };

    /** Model mappings for each tier */
    models: Record<TaskTier, TierModelConfig>;

    /** Logging settings */
    logging: {
        /** Path to SQLite database */
        dbPath: string;
        /** DEFAULT FALSE: never log prompts unless opted in */
        logContent: boolean;
        /** DEFAULT FALSE: never log system prompts */
        logSystemPrompts: boolean;
        /** Debug mode: truncated logs to console */
        debugMode: boolean;
        /** Days to retain log entries */
        retentionDays: number;
    };

    /** Dashboard settings */
    dashboard: {
        /** Whether the dashboard is enabled */
        enabled: boolean;
    };

    /** Runtime overrides (not persisted) */
    overrides: {
        /** Force all traffic to this model */
        globalForceModel: string | null;
        /** Per-session overrides */
        sessions: Record<string, SessionOverride>;
    };

    /** API keys from environment (NEVER stored in config files) */
    apiKeys: Record<ProviderType, string>;

    /** Alerts configuration. */
    alerts: AlertsConfig;

    /** Per-model maxContext overrides — model ID → token limit */
    contextOverrides?: Record<string, number>;
}

// === LLM API Types (OpenAI-compatible) ===

/**
 * A content part for multimodal messages.
 */
export interface ContentPart {
    /** The type of content */
    type: 'text' | 'image_url';
    /** Text content (for type: 'text') */
    text?: string;
    /** Image URL content (for type: 'image_url') */
    image_url?: {
        url: string;
        detail?: 'auto' | 'low' | 'high';
    };
}

/**
 * A message in a chat completion request.
 */
export interface ChatMessage {
    /** The role of the message author */
    role: 'system' | 'user' | 'assistant' | 'tool';
    /** The content of the message - can be string or array for multimodal */
    content: string | null | ContentPart[];
    /** Tool calls made by the assistant */
    tool_calls?: ToolCall[];
    /** ID of the tool call this message is responding to */
    tool_call_id?: string;
    /** Additional properties */
    [key: string]: unknown;
}

/**
 * A tool/function definition.
 */
export interface ToolDefinition {
    /** The type of tool (always "function" for now) */
    type: 'function';
    /** Function details */
    function: {
        /** Name of the function */
        name: string;
        /** Description of what the function does */
        description?: string;
        /** JSON Schema for the function parameters */
        parameters?: object;
    };
}

/**
 * A tool call made by the model.
 */
export interface ToolCall {
    /** Unique ID for this tool call */
    id: string;
    /** Type of tool (always "function") */
    type: 'function';
    /** Function call details */
    function: {
        /** Name of the function to call */
        name: string;
        /** JSON string of arguments */
        arguments: string;
    };
}

/**
 * A chat completion request (OpenAI-compatible format).
 */
export interface ChatCompletionRequest {
    /** The model to use */
    model: string;
    /** The messages in the conversation */
    messages: ChatMessage[];
    /** Tool definitions */
    tools?: ToolDefinition[];
    /** How/whether to use tools */
    tool_choice?: string | object;
    /** Whether to stream the response */
    stream?: boolean;
    /** Sampling temperature */
    temperature?: number;
    /** Maximum tokens to generate */
    max_tokens?: number;
    /** Pass through any other fields */
    [key: string]: unknown;
}

/**
 * A chat completion response (OpenAI-compatible format).
 */
export interface ChatCompletionResponse {
    /** Unique ID for this completion */
    id: string;
    /** Object type */
    object: 'chat.completion';
    /** Timestamp of creation */
    created: number;
    /** Model used */
    model: string;
    /** Completion choices */
    choices: Array<{
        index: number;
        message: ChatMessage;
        finish_reason: string | null;
    }>;
    /** Token usage */
    usage?: {
        prompt_tokens: number;
        completion_tokens: number;
        total_tokens: number;
        prompt_tokens_details?: {
            cached_tokens?: number;
            [key: string]: unknown;
        };
    };
}

export interface ImageGenerationRequest {
    model: string;
    prompt: string;
    size?: string;
    quality?: string;
    n?: number;
    background?: string;
    output_format?: string;
    output_compression?: string | number;
    user?: string;
    [key: string]: unknown;
}

export interface ImageEditFile {
    file: File;
    fieldName: string;
}

export interface ImageEditRequest {
    model: string;
    prompt: string;
    images: ImageEditFile[];
    mask?: File;
    size?: string;
    quality?: string;
    n?: string | number;
    background?: string;
    moderation?: string;
    output_format?: string;
    output_compression?: string | number;
    user?: string;
    [key: string]: unknown;
}

export interface ImageGenerationResponse {
    created: number;
    data: Array<{
        b64_json?: string;
        url?: string;
        revised_prompt?: string;
        [key: string]: unknown;
    }>;
    [key: string]: unknown;
}

// === Stats ===

/**
 * Statistics for a time period.
 */
export interface PeriodStats {
    /** Total number of requests */
    requests: number;
    /** What it would have cost originally */
    originalCostUsd: number;
    /** What it actually cost */
    actualCostUsd: number;
    /** Amount saved */
    savingsUsd: number;
    /** Savings as a percentage */
    savingsPercent: number;
    /** Request count per tier */
    tierBreakdown: Record<TaskTier, number>;
    /** Number of escalations */
    escalations: number;
    /** Number of dry-run requests */
    dryRunRequests: number;
    /** Requests with response_time_ms > 3000 */
    slowRequests: number;
    /** 95th percentile response time in ms */
    p95ResponseMs: number;
    /** Requests that errored */
    errorCount: number;
}

/**
 * Complete stats response for the API.
 */
export interface StatsResponse {
    /** Stats for today */
    today: PeriodStats;
    /** Stats for this week */
    thisWeek: PeriodStats;
    /** Stats for this month */
    thisMonth: PeriodStats;
    /** All-time stats */
    allTime: PeriodStats;
    /** Recent routing decisions */
    recentDecisions: RecentDecision[];
    /** Current configuration summary */
    config: {
        enabled: boolean;
        dryRun: boolean;
        modelMap: Record<TaskTier, string>;
        activeOverrides: number;
    };
}

/**
 * A recent routing decision for display.
 */
export interface RecentDecision {
    requestId?: string | null;
    sessionId?: string | null;
    turnId?: string | null;
    /** When the decision was made */
    timestamp: string;
    /** The classification tier */
    tier: TaskTier;
    /** Original model requested */
    originalModel: string;
    /** Model actually used */
    routedModel: string;
    /** Provider model that ultimately answered */
    actualModel: string;
    /** Savings in USD */
    savingsUsd: number;
    /** Whether escalation occurred */
    escalated: boolean;
    /** Classification reason */
    reason: string;
    /** Classifier confidence */
    confidence: number;
    /** Response time in ms */
    responseTimeMs: number;
    /** Input token count */
    inputTokens: number;
    /** Input tokens served from the provider prompt cache */
    cachedInputTokens: number;
    /** Output token count */
    outputTokens: number;
    /** Whether tools were present in the response */
    hadToolCalls: boolean;
    /** Whether the route was evaluated without applying it */
    isDryRun: boolean;
    /** Whether an explicit override selected the model */
    isOverride: boolean;
    /** Sanitized last-user-message preview */
    promptPreview: string | null;
    /** Sanitized request-shape metadata */
    context: {
        messageCount: number;
        hasSystem: boolean;
        toolCount: number;
        lastRole: string | null;
        cacheKeyPresent?: boolean;
        cacheKeyHash?: string | null;
        messageChars?: number;
        toolSchemaChars?: number;
        toolSchemaRoughTokens?: number;
        topToolSchemaGroups?: CodexToolSchemaGroup[];
        bloatAlerts?: string[];
        policyBlock?: {
            policy: string;
            breakerId: string | null;
            blockReason: string | null;
            cacheKeyHash: string | null;
            toolSchemaFingerprint: string | null;
            estimatedInputTokens: number | null;
            source: string | null;
        };
    } | null;
    /** Execution or streaming error */
    error: string | null;
    /** API surface that accepted the request */
    requestApiKind?: 'chat_completions' | 'responses';
    /** Requested reasoning effort, when supplied */
    requestedReasoningEffort?: string | null;
    /** Selected Codex slot, when applicable */
    selectedCodexSlotIndex?: number | null;
    /** Stable hashed Codex account key, when applicable */
    selectedCodexAccountKey?: string | null;
    requestTrace?: RequestTrace | null;
}

export interface RequestTraceToolCall {
    name: string;
    argumentKeys: string[];
    argumentChars: number;
}

export interface RequestTraceDeltaItem {
    role: ChatMessage['role'] | 'developer';
    chars: number;
    preview?: string;
    toolCalls?: RequestTraceToolCall[];
}

export interface RequestTrace {
    version: 1;
    sessionSource: 'prompt_cache_key' | 'sender_id' | 'none';
    requestFingerprint: string;
    messageFingerprints: string[];
    parentRequestId: string | null;
    phase: 'user_input' | 'tool_results' | 'assistant_continuation' | 'retry' | 'history_rewrite';
    delta: {
        comparison: 'baseline' | 'prefix' | 'retry' | 'history_rewrite';
        addedMessageCount: number;
        removedMessageCount: number;
        addedChars: number;
        roleCounts: Record<string, number>;
        toolCallCount: number;
        toolResultCount: number;
        items: RequestTraceDeltaItem[];
        omittedItems: number;
        toolSchemas: {
            status: 'baseline' | 'unchanged' | 'changed';
            count: number;
            chars: number;
        };
    };
}

export interface RequestTraceCandidate {
    requestId: string;
    requestFingerprint: string;
    messageFingerprints: string[];
    messageCount: number;
    toolSchemaFingerprint: string | null;
}

export interface RoutingTokenMetrics {
    requests: number;
    inputTokens: number;
    cachedInputTokens: number;
    uncachedInputTokens: number;
    outputTokens: number;
    uncachedPlusOutputTokens: number;
    cachedPercent: number;
    averageResponseMs: number;
    toolCalls: number;
    toolResults: number;
    codexTokens: number;
    attributedCodexTokens: number;
    quotaCoveragePercent: number;
}

export interface LiveQuotaCalibration {
    source: 'calibrated_total_tokens';
    periodDays: 7;
    fiveHour: CodexQuotaCalibrationRow | null;
    weekly: CodexQuotaCalibrationRow | null;
    fiveHourBurstSensitive: true;
}

export interface LiveRoutingTurn {
    id: string;
    firstSeenAt: string;
    lastSeenAt: string;
    durationMs: number;
    promptPreview: string | null;
    initialTier: string;
    initialModel: string;
    models: string[];
    tiers: string[];
    metrics: RoutingTokenMetrics;
}

export interface LiveRoutingSession {
    id: string;
    firstSeenAt: string;
    lastSeenAt: string;
    durationMs: number;
    turnCount: number;
    models: string[];
    tiers: string[];
    metrics: RoutingTokenMetrics;
    turns: LiveRoutingTurn[];
    hasMoreTurns: boolean;
}

export interface LiveRoutingResponse {
    sessions: LiveRoutingSession[];
    hasMoreSessions: boolean;
    retentionDays: number;
    quotaCalibration: LiveQuotaCalibration;
}

export interface TurnRequestsResponse {
    turnId: string;
    requests: RecentDecision[];
    truncated: boolean;
}

// === Logging DB ===

/**
 * A log entry in the SQLite database.
 */
export interface LogEntry {
    request_id?: string | null;
    turn_id?: string | null;
    /** ISO timestamp */
    timestamp: string;
    /** Original model from request */
    original_model: string;
    /** Model ClawRoute chose */
    routed_model: string;
    /** Model actually used (may differ if escalated) */
    actual_model: string;
    /** Classification tier */
    tier: string;
    /** Why this classification was made */
    classification_reason: string;
    /** Classification confidence */
    confidence: number;
    /** Input token count */
    input_tokens: number;
    /** Input tokens served from provider cache */
    cached_input_tokens?: number;
    /** Output token count */
    output_tokens: number;
    /** What it would have cost */
    original_cost_usd: number;
    /** What it actually cost */
    actual_cost_usd: number;
    /** Savings */
    savings_usd: number;
    /** Whether escalation occurred */
    escalated: boolean;
    /** JSON array of models tried */
    escalation_chain: string;
    /** Response time in ms */
    response_time_ms: number;
    /** Whether response had tool calls */
    had_tool_calls: boolean;
    /** Whether this was a dry-run */
    is_dry_run: boolean;
    /** Whether an override was active */
    is_override: boolean;
    /** Session ID if present */
    session_id: string | null;
    /** Error message if any */
    error: string | null;
    /** Truncated last user prompt (max 300 chars, only when logContent enabled) */
    prompt_preview: string | null;
    /** JSON context info: request shape counters and non-content bloat telemetry */
    context_info: string | null;
    /** API surface that accepted the request */
    request_api_kind?: 'chat_completions' | 'responses';
    /** Requested reasoning effort, when supplied */
    requested_reasoning_effort?: string | null;
    /** Selected Codex slot, when applicable */
    selected_codex_slot_index?: number | null;
    /** Stable hashed Codex account key, when applicable */
    selected_codex_account_key?: string | null;
}

export interface CodexAnalysisSummary {
    requests: number;
    inputTokens: number;
    cachedInputTokens: number;
    cachedPercent: number;
    outputTokens: number;
    apiCostUsd?: number;
}

export interface CodexAnalysisSeriesRow extends CodexAnalysisSummary {
    day: string;
}

export interface CodexAnalysisMixRow {
    key: string | null;
    requests: number;
    requestSharePercent: number;
}

export interface CodexUsageSnapshotHistoryRecord extends CodexUsageSnapshotRecord {
    observedAt: string;
}

export interface RoutingDailyRollupRecord extends CodexAnalysisSummary {
    day: string;
    actualModel: string;
    tier: string;
    requestApiKind: 'chat_completions' | 'responses';
    requestedReasoningEffort: string | null;
}

export interface CodexQuotaCalibrationRow {
    window: CodexUsageWindowKey;
    observedQuotaDelta: number;
    totalTokens: number;
    uncachedPlusOutputTokens: number;
    quotaPctPerMillionTotalTokens: number;
    quotaPctPerMillionUncachedPlusOutput: number;
}

export interface CodexSlotUsageEstimate {
    slotIndex: number;
    window: CodexUsageWindowKey;
    requests: number;
    inputTokens: number;
    cachedInputTokens: number;
    outputTokens: number;
    totalTokens: number;
    uncachedPlusOutputTokens: number;
    actualQuotaDelta: number;
    expectedQuotaDelta: number;
    varianceQuotaDelta: number;
    expectedSource: 'calibrated_total_tokens';
    apiCostUsd: number;
}

export interface CodexUsageChartPoint {
    bucket: string;
    slotIndex: number;
    requests: number;
    totalTokens: number;
    apiCostUsd: number;
    weeklyActualQuotaDelta: number;
    weeklyExpectedQuotaDelta: number;
    fiveHourActualQuotaDelta: number;
    fiveHourExpectedQuotaDelta: number;
}

export interface CodexToolSchemaGroup {
    key: string;
    tools: number;
    chars: number;
}

export interface CodexBloatConversationRow {
    key: string | null;
    requests: number;
    inputTokens: number;
    cachedInputTokens: number;
    outputTokens: number;
    totalTokens: number;
    cachedPercent: number;
    firstSeenAt: string | null;
    lastSeenAt: string | null;
    maxMessageCount: number;
    maxToolCount: number;
    maxMessageChars: number;
    maxToolSchemaChars: number;
    maxToolSchemaRoughTokens: number;
    topToolSchemaGroups: CodexToolSchemaGroup[];
    alerts: string[];
    alertCount: number;
    slots: number[];
    models: string[];
}

export interface CodexApiPricingModel {
    model: string;
    inputUsdPerMillion: number;
    cachedInputUsdPerMillion: number;
    outputUsdPerMillion: number;
}

export interface CodexAnalysisResponse {
    period: {
        key: string;
        start: string;
        end: string;
    };
    summary: CodexAnalysisSummary;
    daily: CodexAnalysisSeriesRow[];
    modelMix: CodexAnalysisMixRow[];
    tierMix: CodexAnalysisMixRow[];
    apiKindMix: CodexAnalysisMixRow[];
    reasoningEffortMix: CodexAnalysisMixRow[];
    quotaSnapshots: CodexUsageSnapshotRecord[];
    quotaHistory: CodexUsageSnapshotHistoryRecord[];
    activationCheckpoints: CodexActivationCheckpoint[];
    dailyRollups: RoutingDailyRollupRecord[];
    quotaCalibration: CodexQuotaCalibrationRow[];
    slotUsageEstimates: CodexSlotUsageEstimate[];
    dailySlotUsage: CodexUsageChartPoint[];
    weeklySlotUsage: CodexUsageChartPoint[];
    expensiveConversations: CodexBloatConversationRow[];
    apiPricing: {
        source: string;
        currency: 'USD';
        unit: 'per_1m_tokens';
        models: CodexApiPricingModel[];
    };
    flags: {
        hasQuotaHistory: boolean;
        hasSelectedCodexAttribution: boolean;
        hasReasoningEffort: boolean;
    };
}

// === Validation ===

/**
 * Result from validating an LLM response.
 */
export interface ValidationResult {
    /** Whether the response is valid */
    valid: boolean;
    /** Reason for invalidity (if any) */
    reason: string;
    /** Whether the response contained tool calls */
    hadToolCalls: boolean;
}

// === Donation & Community Support ===

/**
 * Alerts configuration (Pro feature).
 */
export interface AlertsConfig {
    /** Email for daily/weekly savings alerts */
    email?: string;
    /** Slack webhook URL for alerts */
    slackWebhook?: string;
}

// === Codex Usage ===

export type CodexUsageWindowKey = 'fiveHour' | 'weekly';

export interface CodexUsageRawWindow {
    limit_window_seconds?: number;
    used_percent?: number;
    reset_at?: number;
    resets_at?: number;
    resets_in_seconds?: number;
}

export interface CodexUsageRawRateLimit {
    primary_window?: CodexUsageRawWindow;
    secondary_window?: CodexUsageRawWindow;
}

export interface CodexUsageRawResponse {
    account_id?: string;
    primary_window?: CodexUsageRawWindow;
    secondary_window?: CodexUsageRawWindow;
    rate_limit?: CodexUsageRawRateLimit;
    rate_limit_reset_credits?: unknown;
    reset_credits?: unknown;
}

export interface CodexUsageWindowSnapshot {
    window: CodexUsageWindowKey;
    usedPercent: number;
    resetAt: string;
    windowMinutes: number;
    updatedAt: string;
}

export type CodexUsageRowSource = 'live' | 'cache' | 'persisted' | 'cooldown';

export type CodexResetCreditsSource = 'live' | 'liveCountOnly' | 'persisted';

export interface CodexResetCreditItem {
    creditKey: string;
    status: string;
    resetType: string | null;
    title: string | null;
    grantedAt: string | null;
    expiresAt: string | null;
    redeemedAt: string | null;
}

export interface CodexResetCreditsSnapshot {
    accountKey: string;
    slotIndex: number;
    availableCount: number | null;
    detailsAvailable: boolean;
    source: CodexResetCreditsSource;
    updatedAt: string;
    credits: CodexResetCreditItem[];
}

export interface CodexUsageAccountRow {
    accountKey: string;
    slotIndex: number;
    slotIndexes: number[];
    slotPaths: string[];
    source: CodexUsageRowSource;
    stale: boolean;
    cooldownUntil: string | null;
    lastFetchedAt: string | null;
    updatedAt: string | null;
    fiveHour: CodexUsageWindowSnapshot | null;
    weekly: CodexUsageWindowSnapshot | null;
    resetCredits: CodexResetCreditsSnapshot | null;
}

export interface CodexUsageSnapshotRecord {
    accountKey: string;
    slotIndex: number;
    window: CodexUsageWindowKey;
    usedPercent: number;
    resetAt: string;
    windowMinutes: number;
    updatedAt: string;
}

export interface CodexUsageSlotError {
    slotIndex: number;
    message: string;
    source: CodexUsageRowSource | 'none';
}

export interface CodexUsageApiBody {
    partial: boolean;
    accounts: CodexUsageAccountRow[];
    slotErrors: CodexUsageSlotError[];
    resetCreditErrors?: CodexUsageSlotError[];
    cacheUsage?: {
        inputTokens: number;
        cachedInputTokens: number;
        cachedPercent: number;
        periodHours: number;
    };
    error?: {
        message: string;
    };
}

export interface CodexUsageResult {
    status: number;
    body: CodexUsageApiBody;
}

export type CodexAuthUnavailableReason = 'missing' | 'unknown_account' | 'expired' | 'expired_refresh_failed';

export interface CodexUsageSelectorSlotIdentity {
    slotIndex: number;
    slotPath: string | null;
    accountKey: string | null;
    rateLimitedUntil: number;
    authAvailable: boolean;
    authUnavailableReason: CodexAuthUnavailableReason | null;
    authRetryAt: string | null;
}

export interface CodexUsageSelectorRequest {
    slots: CodexUsageSelectorSlotIdentity[];
    persistedMaxAgeMs?: number;
    refreshThrottleMs?: number;
    allowBackgroundRefresh?: boolean;
}

export interface CodexUsageSelectorSnapshot {
    slots: CodexUsageSelectorSlotIdentity[];
    accounts: CodexUsageAccountRow[];
    unknownAccountSlotIndexes: number[];
    missingUsageSlotIndexes: number[];
    staleAccountKeys: string[];
    triggeredBackgroundRefresh: boolean;
}

export type CodexScheduleStartDay = 'mon' | 'tue' | 'wed' | 'thu' | 'fri' | 'sat' | 'sun';

export interface CodexAccountScheduleRow {
    accountKey: string;
    slotIndex: number;
    seedOrder: number;
    anchorWeekday: number;
    laneRank: number;
    updatedAt: string;
}

export type CodexBalanceLoaderMode = 'off' | 'shadow' | 'on';

export interface CodexBalancerSettings {
    mode: CodexBalanceLoaderMode;
    earlyActivationEnabled: boolean;
    earlyActivationWeeklyPercent: number;
    coldMigrationFiveHourThresholdPercent: number;
}

export interface CodexBalancerSlotOverride {
    slotIndex: number;
    enabled: boolean;
    manualActivationCycleResetAt: string | null;
    updatedAt: string;
}

export interface CodexActivationCheckpoint {
    slotIndex: number;
    accountKey: string;
    expectedWeeklyResetAt: string | null;
    lastUsageCheckAt: string | null;
    updatedAt: string;
}

export interface CodexBalancerAuditRow {
    id: number;
    timestamp: string;
    action: string;
    slotIndex: number | null;
    previousValue: string | null;
    nextValue: string | null;
}

export interface CodexBalancerDecision {
    timestamp: string;
    slotIndex: number | null;
    accountKey: string | null;
    mode: CodexBalanceLoaderMode;
    reason: string | null;
    fallbackReason: string | null;
    weeklyUsedPercent: number | null;
    weeklyResetAt: string | null;
    requiredBurnRate: number | null;
}

export type CodexColdMigrationDecisionStatus = 'pending' | 'approved' | 'dismissed' | 'consumed';

export interface CodexColdMigrationDecision {
    id: string;
    status: CodexColdMigrationDecisionStatus;
    createdAt: string;
    updatedAt: string;
    expiresAt: string;
    approvedAt: string | null;
    dismissedAt: string | null;
    consumedAt: string | null;
    sessionKey: string;
    previousAccountKey: string;
    previousSlotIndex: number | null;
    targetAccountKey: string;
    targetSlotIndex: number;
    estimatedInputTokens: number;
    estimatedFiveHourPercent: number;
    thresholdFiveHourPercent: number;
    previousFiveHourUsedPercent: number | null;
    previousFiveHourRemainingPercent: number | null;
    targetFiveHourUsedPercent: number | null;
    targetFiveHourRemainingPercent: number | null;
    targetWeeklyUsedPercent: number | null;
    targetWeeklyRemainingPercent: number | null;
    reason: 'cold_prompt_cache_migration';
}

export interface CodexBalancerSlotState {
    slotIndex: number;
    path: string | null;
    accountKey: string | null;
    authAvailable: boolean;
    authUnavailableReason: CodexAuthUnavailableReason | null;
    authRetryAt: string | null;
    enabled: boolean;
    activated: boolean;
    activationReason: 'scheduled' | 'early' | 'manual' | 'pending' | 'unknown';
    anchorWeekday: number;
    scheduledDay: string;
    rateLimitedUntil: string | null;
    telemetryFresh: boolean;
    fiveHourUsedPercent: number | null;
    fiveHourResetAt: string | null;
    weeklyUsedPercent: number | null;
    weeklyResetAt: string | null;
    expectedWeeklyResetAt: string | null;
    lastUsageCheckAt: string | null;
    exhausted: boolean;
}

export interface CodexBalancerLease {
    id: string;
    accountKey: string;
    slotIndex: number;
    startedAt: string;
    lastUsedAt: string;
    nominalExpiresAt: string;
    maxExpiresAt: string;
    status: 'active' | 'grace';
    selectionReason: string | null;
}

export interface CodexBalancerState {
    settings: CodexBalancerSettings;
    defaults: CodexBalancerSettings;
    settingSources: {
        mode: 'environment' | 'persisted';
        earlyActivationEnabled: 'environment' | 'persisted';
        earlyActivationWeeklyPercent: 'environment' | 'persisted';
        coldMigrationFiveHourThresholdPercent: 'environment' | 'persisted';
    };
    currentWeekdayUtc: number;
    slots: CodexBalancerSlotState[];
    activeLease: CodexBalancerLease | null;
    lastDecision: CodexBalancerDecision | null;
    coldMigrationDecisions: CodexColdMigrationDecision[];
    recentAudit: CodexBalancerAuditRow[];
}

export interface CodexBalanceLoaderScheduleContext {
    rows: CodexAccountScheduleRow[];
    currentWeekdayUtc: number;
}

export interface CodexSessionAccountAffinity {
    provider: ProviderType;
    accountKey: string;
    slotIndex?: number;
    lastSelectedAt?: string | null;
    lastCompletedAt?: string | null;
}

export interface CodexBalanceLoaderAffinityContext {
    sessionId: string | null;
    cacheEligible: boolean;
    preferred: CodexSessionAccountAffinity | null;
}

export type CodexBalanceLoaderFallbackReason =
    | 'missing_usage'
    | 'stale_usage'
    | 'cooldown_only'
    | 'unknown_account'
    | 'no_eligible_slot'
    | 'selector_error';

export type CodexBalanceLoaderAffinityStatus =
    | 'not_requested'
    | 'cache_hint_missing'
    | 'provider_mismatch'
    | 'preferred_missing'
    | 'preferred_ineligible'
    | 'preferred_low_headroom'
    | 'score_gap'
    | 'best_score'
    | 'applied';

export type CodexBalanceLoaderDecisionReason =
    | 'legacy_unavailable'
    | 'optimized_burn_rate'
    | 'optimized_affinity'
    | 'scheduled_usage_score'
    | 'scheduled_non_usage'
    | 'scheduled_affinity'
    | 'spillover_usage_score'
    | 'spillover_non_usage'
    | 'spillover_affinity';

export interface CodexBalanceLoaderRequest {
    now: number;
    provider: ProviderType;
    snapshot: CodexUsageSelectorSnapshot;
    excludedSlotIndexes?: ReadonlySet<number> | readonly number[];
    excludedAccountKeys?: ReadonlySet<string> | readonly string[];
    pendingLeasesByAccountKey?: Readonly<Record<string, number>>;
    slotPendingLeasesByIndex?: Readonly<Record<number, number>>;
    slotLastSelectedAtByIndex?: Readonly<Record<number, number>>;
    persistedMaxAgeMs?: number;
    affinity?: CodexBalanceLoaderAffinityContext | null;
    schedule?: CodexBalanceLoaderScheduleContext | null;
}

export interface CodexBalanceLoaderAccountScore {
    accountKey: string;
    slotIndexes: number[];
    baseScore: number;
    source: CodexUsageRowSource;
    pendingLeases: number;
    bottleneckResidual: number | null;
    fiveHourResidual: number | null;
    weeklyResidual: number | null;
}

export interface CodexBalanceLoaderSelection {
    accountKey: string;
    slotIndex: number;
    slotIndexes: number[];
    baseScore: number;
    anchorWeekday?: number;
    laneRank?: number;
    spillover?: boolean;
    telemetryFresh?: boolean;
}

export interface CodexBalanceLoaderResult {
    selection: CodexBalanceLoaderSelection | null;
    fallbackReason: CodexBalanceLoaderFallbackReason | null;
    affinityStatus: CodexBalanceLoaderAffinityStatus;
    scores: CodexBalanceLoaderAccountScore[];
    decisionReason?: CodexBalanceLoaderDecisionReason;
    currentWeekdayUtc?: number;
    activeWeekday?: number | null;
    activeLaneTelemetryFresh?: boolean;
}
