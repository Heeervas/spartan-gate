import { describe, expect, it } from 'vitest';
import {
    buildRequestTraceSnapshot,
    finalizeRequestTrace,
    resolveSessionIdentity,
    sanitizeLoggedPreview,
} from '../src/request-trace.js';
import { ChatCompletionRequest, RequestTraceCandidate } from '../src/types.js';

const session = resolveSessionIdentity([], 'hermes:session-123');

function request(messages: ChatCompletionRequest['messages']): ChatCompletionRequest {
    return {
        model: 'clawroute/auto',
        messages,
        tools: [{
            type: 'function',
            function: { name: 'search', parameters: { type: 'object' } },
        }],
    };
}

function candidate(requestId: string, snapshot: ReturnType<typeof buildRequestTraceSnapshot>): RequestTraceCandidate {
    return {
        requestId,
        requestFingerprint: snapshot.requestFingerprint,
        messageFingerprints: snapshot.messageFingerprints,
        messageCount: snapshot.messageFingerprints.length,
        toolSchemaFingerprint: snapshot.toolSchemaFingerprint,
    };
}

describe('request tracing', () => {
    it('uses an explicit Hermes cache key as the session and does not treat user as session input', () => {
        expect(session).toEqual({
            id: expect.stringMatching(/^[a-f0-9]{16}$/),
            source: 'prompt_cache_key',
        });
        expect(resolveSessionIdentity([], null)).toEqual({ id: null, source: 'none' });
        expect(resolveSessionIdentity([
            { role: 'system', content: '{"sender_id":"123456"}' },
        ], null)).toEqual({
            id: expect.stringMatching(/^[a-f0-9]{8}$/),
            source: 'sender_id',
        });
    });

    it('keeps one turn across tool continuations and records only the appended messages', () => {
        const first = buildRequestTraceSnapshot(request([
            { role: 'system', content: 'private system prompt' },
            { role: 'user', content: 'Find current status' },
        ]), session, true);
        const firstTrace = finalizeRequestTrace(first, []);
        expect(firstTrace.phase).toBe('user_input');
        expect(firstTrace.delta.items).toEqual([
            expect.objectContaining({ role: 'user', preview: 'Find current status' }),
        ]);
        expect(JSON.stringify(firstTrace)).not.toContain('private system prompt');

        const continued = buildRequestTraceSnapshot(request([
            { role: 'system', content: 'private system prompt' },
            { role: 'user', content: 'Find current status' },
            {
                role: 'assistant',
                content: null,
                tool_calls: [{
                    id: 'call-1',
                    type: 'function',
                    function: { name: 'search', arguments: '{"query":"secret value"}' },
                }],
            },
            { role: 'tool', tool_call_id: 'call-1', content: 'result token=super-secret' },
        ]), session, true);
        const continuedTrace = finalizeRequestTrace(continued, [candidate('request-1', first)]);

        expect(continued.turnId).toBe(first.turnId);
        expect(continuedTrace).toMatchObject({
            parentRequestId: 'request-1',
            phase: 'tool_results',
            delta: {
                comparison: 'prefix',
                addedMessageCount: 2,
                toolCallCount: 1,
                toolResultCount: 1,
                toolSchemas: { status: 'unchanged' },
            },
        });
        expect(continuedTrace.delta.items[0]).toMatchObject({
            role: 'assistant',
            toolCalls: [{ name: 'search', argumentKeys: ['query'] }],
        });
        expect(continuedTrace.delta.items[1]?.preview).toBe('result token=[REDACTED]');
        expect(JSON.stringify(continuedTrace)).not.toContain('secret value');
        expect(JSON.stringify(continuedTrace)).not.toContain('super-secret');
    });

    it('records structure without previews when content logging is disabled', () => {
        const snapshot = buildRequestTraceSnapshot(request([
            { role: 'user', content: 'do not persist me' },
        ]), session, false);
        const trace = finalizeRequestTrace(snapshot, []);
        expect(trace.delta.items[0]).toMatchObject({ role: 'user' });
        expect(trace.delta.items[0]).not.toHaveProperty('preview');
        expect(JSON.stringify(trace)).not.toContain('do not persist me');
    });

    it('detects retries and rewritten history', () => {
        const first = buildRequestTraceSnapshot(request([{ role: 'user', content: 'one' }]), session, true);
        const exact = finalizeRequestTrace(first, [candidate('request-1', first)]);
        expect(exact).toMatchObject({ phase: 'retry', delta: { comparison: 'retry', addedMessageCount: 0 } });

        const rewritten = buildRequestTraceSnapshot(request([
            { role: 'system', content: 'compacted' },
            { role: 'user', content: 'one' },
            { role: 'assistant', content: 'continued' },
        ]), session, true);
        const forcedCandidate = { ...candidate('request-1', first), requestFingerprint: 'not-a-prefix' };
        const trace = finalizeRequestTrace(rewritten, [forcedCandidate]);
        expect(trace.phase).toBe('history_rewrite');
        expect(trace.delta.comparison).toBe('history_rewrite');
    });

    it('redacts common authorization material', () => {
        expect(sanitizeLoggedPreview(
            'Authorization: Bearer abc.def password=hunter2 api_key=sk-test',
            300,
        )).toBe('Authorization: [REDACTED] password=[REDACTED] api_key=[REDACTED]');
    });
});
