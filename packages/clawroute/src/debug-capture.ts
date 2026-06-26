import { appendFileSync, mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { ChatCompletionRequest, ClassificationResult, ExecutionResult, RoutingDecision } from './types.js';
import { SessionIdentity } from './request-trace.js';

type DebugCaptureConfig = {
    dir: string;
    untilMs: number;
    maxRequests: number;
    minInputTokens: number;
};

type DebugCaptureInput = {
    requestId: string;
    requestApiKind: 'chat_completions' | 'responses';
    body: ChatCompletionRequest;
    routing: RoutingDecision;
    classification: ClassificationResult;
    result: ExecutionResult;
    session: SessionIdentity;
    promptCacheKey: string | null;
    promptCacheKeyHash: string | null;
    contextInfo: string;
};

type CodexFinalRequestCaptureInput = {
    requestId: string | null;
    sessionId: string | null;
    promptCacheKeyHash: string | null;
    modelId: string;
    modelName: string;
    slotIndex: number;
    accountKey: string | null;
    attempt: number;
    responseStatus: number;
    responseBodyExcerpt?: string | null;
    estimatedInputTokens?: number | null;
    body: Record<string, unknown>;
};

const PROCESS_STARTED_AT_MS = Date.now();
const CAPTURE_FILE_STAMP = new Date(PROCESS_STARTED_AT_MS).toISOString().replace(/[:.]/g, '-');
let capturedRequests = 0;
let capturedCodexFinalRequests = 0;
let warned = false;
let announced = false;
let announcedCodexFinal = false;

function parseEnabled(value: string | undefined): boolean {
    return ['1', 'true', 'yes', 'on'].includes((value ?? '').trim().toLowerCase());
}

function parseInteger(value: string | undefined, fallback: number, min: number, max: number): number {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return fallback;
    return Math.min(max, Math.max(min, Math.trunc(parsed)));
}

function resolveDebugCaptureConfig(nowMs = Date.now(), env: NodeJS.ProcessEnv = process.env): DebugCaptureConfig | null {
    if (!parseEnabled(env['CLAWROUTE_DEBUG_CAPTURE_RAW'])) return null;

    const seconds = parseInteger(env['CLAWROUTE_DEBUG_CAPTURE_SECONDS'], 600, 1, 86_400);
    const untilEnv = env['CLAWROUTE_DEBUG_CAPTURE_UNTIL'];
    const untilMs = untilEnv ? Date.parse(untilEnv) : PROCESS_STARTED_AT_MS + seconds * 1000;
    if (!Number.isFinite(untilMs) || nowMs > untilMs) return null;

    return {
        dir: env['CLAWROUTE_DEBUG_CAPTURE_DIR'] || '/app/data/debug-captures',
        untilMs,
        maxRequests: parseInteger(env['CLAWROUTE_DEBUG_CAPTURE_MAX_REQUESTS'], 1000, 1, 100_000),
        minInputTokens: parseInteger(env['CLAWROUTE_DEBUG_CAPTURE_MIN_INPUT_TOKENS'], 0, 0, 10_000_000),
    };
}

function parseContextInfo(contextInfo: string): unknown {
    try {
        return JSON.parse(contextInfo);
    } catch {
        return null;
    }
}

export function writeDebugRequestCapture(input: DebugCaptureInput): void {
    const config = resolveDebugCaptureConfig();
    if (!config) return;
    if (capturedRequests >= config.maxRequests) return;
    if (input.result.inputTokens < config.minInputTokens) return;

    try {
        mkdirSync(config.dir, { recursive: true });
        const filePath = join(config.dir, `raw-requests-${CAPTURE_FILE_STAMP}-${process.pid}.jsonl`);
        const row = {
            version: 1,
            captured_at: new Date().toISOString(),
            capture_expires_at: new Date(config.untilMs).toISOString(),
            request_id: input.requestId,
            request_api_kind: input.requestApiKind,
            session: input.session,
            prompt_cache_key_present: Boolean(input.promptCacheKey),
            prompt_cache_key_hash: input.promptCacheKeyHash,
            model: {
                original: input.routing.originalModel,
                routed: input.routing.routedModel,
                actual: input.result.actualModel,
                tier: input.routing.tier,
            },
            classification: {
                reason: input.classification.reason,
                confidence: input.classification.confidence,
            },
            result: {
                status: input.result.response.status,
                input_tokens: input.result.inputTokens,
                cached_input_tokens: input.result.cachedInputTokens ?? 0,
                output_tokens: input.result.outputTokens,
                response_time_ms: input.result.responseTimeMs,
                had_tool_calls: input.result.hadToolCalls,
                stream_error: input.result.streamError ?? null,
                selected_codex_slot_index: input.result.selectedCodexSlotIndex ?? null,
                selected_codex_account_key: input.result.selectedCodexAccountKey ?? null,
            },
            context_info: parseContextInfo(input.contextInfo),
            raw_request: input.body,
        };

        appendFileSync(filePath, `${JSON.stringify(row)}\n`, { encoding: 'utf-8' });
        capturedRequests += 1;
        if (!announced) {
            announced = true;
            console.warn(`[debug-capture] Raw request capture enabled until ${row.capture_expires_at}; writing ${filePath}`);
        }
    } catch (error) {
        if (!warned) {
            warned = true;
            console.warn('[debug-capture] Failed to write raw request capture:', error);
        }
    }
}

export function writeDebugCodexFinalRequestCapture(input: CodexFinalRequestCaptureInput): void {
    const config = resolveDebugCaptureConfig();
    if (!config) return;
    if (capturedCodexFinalRequests >= config.maxRequests) return;
    if ((input.estimatedInputTokens ?? 0) < config.minInputTokens) return;

    try {
        mkdirSync(config.dir, { recursive: true });
        const filePath = join(config.dir, `codex-final-requests-${CAPTURE_FILE_STAMP}-${process.pid}.jsonl`);
        const row = {
            version: 1,
            captured_at: new Date().toISOString(),
            capture_expires_at: new Date(config.untilMs).toISOString(),
            request_id: input.requestId,
            session_id: input.sessionId,
            prompt_cache_key_hash: input.promptCacheKeyHash,
            model_id: input.modelId,
            model_name: input.modelName,
            slot_index: input.slotIndex,
            account_key: input.accountKey,
            attempt: input.attempt,
            response_status: input.responseStatus,
            response_body_excerpt: input.responseBodyExcerpt ?? null,
            estimated_input_tokens: input.estimatedInputTokens ?? null,
            final_request: input.body,
        };

        appendFileSync(filePath, `${JSON.stringify(row)}\n`, { encoding: 'utf-8' });
        capturedCodexFinalRequests += 1;
        if (!announcedCodexFinal) {
            announcedCodexFinal = true;
            console.warn(`[debug-capture] Final Codex request capture enabled until ${row.capture_expires_at}; writing ${filePath}`);
        }
    } catch (error) {
        if (!warned) {
            warned = true;
            console.warn('[debug-capture] Failed to write final Codex request capture:', error);
        }
    }
}
