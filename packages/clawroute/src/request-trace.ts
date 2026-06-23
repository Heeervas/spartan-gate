import { createHash } from 'crypto';
import { ChatCompletionRequest, ChatMessage, RequestTrace, RequestTraceCandidate, RequestTraceDeltaItem } from './types.js';
import { stripMetadataPreamble } from './utils.js';

const TRACE_VERSION = 1;
const MAX_DELTA_ITEMS = 24;
const MESSAGE_PREVIEW_CHARS = 300;
const TOOL_PREVIEW_CHARS = 240;

export type SessionIdentity = {
    id: string | null;
    source: 'prompt_cache_key' | 'sender_id' | 'none';
};

export type RequestTraceSnapshot = {
    turnId: string | null;
    requestFingerprint: string;
    messageFingerprints: string[];
    messages: ChatMessage[];
    toolSchemaFingerprint: string;
    toolCount: number;
    toolSchemaChars: number;
    sessionSource: SessionIdentity['source'];
    logContent: boolean;
};

function hash(value: string, length = 16): string {
    return createHash('sha256').update(value).digest('hex').slice(0, length);
}

function stableJson(value: unknown): string {
    if (value === null || typeof value !== 'object') return JSON.stringify(value) ?? 'null';
    if (Array.isArray(value)) return `[${value.map(stableJson).join(',')}]`;
    const record = value as Record<string, unknown>;
    return `{${Object.keys(record).sort().map((key) => `${JSON.stringify(key)}:${stableJson(record[key])}`).join(',')}}`;
}

function safeJson(value: unknown): string {
    try {
        return JSON.stringify(value) ?? '';
    } catch {
        return '';
    }
}

function contentText(content: ChatMessage['content']): string {
    if (typeof content === 'string') return content;
    if (!Array.isArray(content)) return '';
    return content
        .filter((part) => part?.type === 'text' && typeof part.text === 'string')
        .map((part) => part.text ?? '')
        .join(' ');
}

export function sanitizeLoggedPreview(value: string, maxChars: number): string {
    return value
        .replace(/\b(authorization)(\s*[=:]\s*)(?:Bearer\s+)?[^\s,"'};]+/gi, '$1$2[REDACTED]')
        .replace(/\bBearer\s+[A-Za-z0-9._~+\/-]+=*/gi, 'Bearer [REDACTED]')
        .replace(/\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b/g, '[REDACTED_JWT]')
        .replace(/\b(api[_-]?key|(?:access[_-]?|refresh[_-]?)?token|password|passwd|cookie|secret)\b(\s*[=:]\s*)(["']?)[^\s,"'};]+\3/gi, '$1$2[REDACTED]')
        .slice(0, maxChars);
}

export function resolveSessionIdentity(
    messages: ChatCompletionRequest['messages'] | undefined,
    explicitPromptCacheKey: string | null,
): SessionIdentity {
    if (explicitPromptCacheKey?.trim()) {
        return {
            id: hash(`prompt_cache_key\0${explicitPromptCacheKey.trim()}`),
            source: 'prompt_cache_key',
        };
    }
    const systemMessage = (messages ?? []).find((message) => message.role === 'system');
    const systemContent = typeof systemMessage?.content === 'string' ? systemMessage.content : '';
    const senderId = systemContent.match(/"sender_id"\s*:\s*"(\d+)"/)?.[1];
    return senderId
        ? { id: hash(senderId, 8), source: 'sender_id' }
        : { id: null, source: 'none' };
}

export function buildRequestTraceSnapshot(
    body: ChatCompletionRequest,
    session: SessionIdentity,
    logContent: boolean,
): RequestTraceSnapshot {
    const messages = body.messages ?? [];
    const messageFingerprints: string[] = [];
    let cumulative = 'root';
    for (const message of messages) {
        cumulative = hash(`${cumulative}\0${hash(stableJson(message), 32)}`);
        messageFingerprints.push(cumulative);
    }
    const lastUserIndex = messages.reduce(
        (found, message, index) => message.role === 'user' ? index : found,
        -1,
    );
    const turnId = session.id && lastUserIndex >= 0
        ? hash(`turn\0${session.id}\0${messageFingerprints[lastUserIndex]}`)
        : null;
    const toolsJson = stableJson(body.tools ?? []);
    return {
        turnId,
        requestFingerprint: messageFingerprints.at(-1) ?? hash('empty-request'),
        messageFingerprints,
        messages,
        toolSchemaFingerprint: hash(toolsJson),
        toolCount: body.tools?.length ?? 0,
        toolSchemaChars: safeJson(body.tools ?? []).length,
        sessionSource: session.source,
        logContent,
    };
}

function summarizeMessage(message: ChatMessage, logContent: boolean): RequestTraceDeltaItem {
    const chars = safeJson(message).length;
    const item: RequestTraceDeltaItem = { role: message.role, chars };
    if (message.role === 'system') return item;

    if (message.role === 'assistant' && message.tool_calls?.length) {
        item.toolCalls = message.tool_calls.map((call) => {
            let argumentKeys: string[] = [];
            try {
                const parsed = JSON.parse(call.function.arguments) as unknown;
                if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
                    argumentKeys = Object.keys(parsed as Record<string, unknown>).slice(0, 20);
                }
            } catch {
                // Invalid arguments are still represented by their size.
            }
            return {
                name: call.function.name,
                argumentKeys,
                argumentChars: call.function.arguments.length,
            };
        });
    }

    if (logContent) {
        let text = contentText(message.content);
        if (message.role === 'user') text = stripMetadataPreamble(text);
        if (text) {
            item.preview = sanitizeLoggedPreview(
                text,
                message.role === 'tool' ? TOOL_PREVIEW_CHARS : MESSAGE_PREVIEW_CHARS,
            );
        }
    }
    return item;
}

function commonPrefixLength(left: string[], right: string[]): number {
    const limit = Math.min(left.length, right.length);
    let index = 0;
    while (index < limit && left[index] === right[index]) index += 1;
    return index;
}

export function finalizeRequestTrace(
    snapshot: RequestTraceSnapshot,
    candidates: RequestTraceCandidate[],
): RequestTrace {
    const exact = candidates.find((candidate) => candidate.requestFingerprint === snapshot.requestFingerprint);
    let parent = exact ?? candidates
        .filter((candidate) => snapshot.messageFingerprints.includes(candidate.requestFingerprint))
        .sort((a, b) => b.messageCount - a.messageCount)[0] ?? null;
    let comparison: RequestTrace['delta']['comparison'] = exact ? 'retry' : parent ? 'prefix' : 'baseline';
    let lastUserIndex = -1;
    for (let index = snapshot.messages.length - 1; index >= 0; index -= 1) {
        if (snapshot.messages[index]?.role === 'user') {
            lastUserIndex = index;
            break;
        }
    }
    let startIndex = parent?.messageCount ?? Math.max(0, lastUserIndex);
    let removedMessageCount = 0;

    if (!exact && !parent && candidates.length > 0) {
        parent = candidates[0] ?? null;
        if (parent) {
            startIndex = commonPrefixLength(parent.messageFingerprints, snapshot.messageFingerprints);
            removedMessageCount = Math.max(0, parent.messageCount - startIndex);
            comparison = 'history_rewrite';
        }
    }

    const addedMessages = exact ? [] : snapshot.messages.slice(startIndex);
    const allItems = addedMessages.map((message) => summarizeMessage(message, snapshot.logContent));
    const items = allItems.slice(-MAX_DELTA_ITEMS);
    const roleCounts = addedMessages.reduce<Record<string, number>>((counts, message) => {
        counts[message.role] = (counts[message.role] ?? 0) + 1;
        return counts;
    }, {});
    const toolCallCount = addedMessages.reduce((total, message) => total + (message.tool_calls?.length ?? 0), 0);
    const toolResultCount = roleCounts['tool'] ?? 0;
    const latestToolFingerprint = parent?.toolSchemaFingerprint ?? null;
    const toolSchemaStatus = !parent
        ? 'baseline'
        : latestToolFingerprint === snapshot.toolSchemaFingerprint ? 'unchanged' : 'changed';
    const phase: RequestTrace['phase'] = exact
        ? 'retry'
        : comparison === 'history_rewrite'
            ? 'history_rewrite'
            : (roleCounts['user'] ?? 0) > 0
                ? 'user_input'
                : toolResultCount > 0
                    ? 'tool_results'
                    : 'assistant_continuation';

    return {
        version: TRACE_VERSION,
        sessionSource: snapshot.sessionSource,
        requestFingerprint: snapshot.requestFingerprint,
        messageFingerprints: snapshot.messageFingerprints,
        parentRequestId: parent?.requestId ?? null,
        phase,
        delta: {
            comparison,
            addedMessageCount: addedMessages.length,
            removedMessageCount,
            addedChars: allItems.reduce((total, item) => total + item.chars, 0),
            roleCounts,
            toolCallCount,
            toolResultCount,
            items,
            omittedItems: Math.max(0, allItems.length - items.length),
            toolSchemas: {
                status: toolSchemaStatus,
                count: snapshot.toolCount,
                chars: snapshot.toolSchemaChars,
            },
        },
    };
}
