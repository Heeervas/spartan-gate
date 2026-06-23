/**
 * ClawRoute Responses API Adapter Tests
 *
 * Tests for the /v1/responses endpoint and its translation functions:
 * - responsesInputToChatMessages()
 * - responsesBodyToChatCompletions()
 * - chatCompletionToResponsesBody()
 *
 * RED state: These tests should FAIL until responses-adapter.ts is implemented.
 */

import { describe, it, expect, beforeAll, afterAll, beforeEach, afterEach, vi } from 'vitest';
import { Hono } from 'hono';
import { TaskTier, ClawRouteConfig } from '../src/types.js';
import { closeDb, getRecentDecisions, initDb } from '../src/logger.js';
import {
    responsesInputToChatMessages,
    responsesBodyToChatCompletions,
    chatCompletionToResponsesBody,
    responsesBodyToSSEResponse,
    chatCompletionStreamToResponsesSSE,
} from '../src/responses-adapter.js';

async function readResponseBody(response: Response): Promise<string> {
    const reader = response.body?.getReader();
    const chunks: Uint8Array[] = [];
    if (!reader) return '';

    while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        if (value) chunks.push(value);
    }

    return new TextDecoder().decode(Buffer.concat(chunks));
}

function createChatSseStream(chunks: Array<Record<string, unknown> | '[DONE]'>): ReadableStream<Uint8Array> {
    const encoder = new TextEncoder();
    const body = chunks
        .map((chunk) => chunk === '[DONE]' ? 'data: [DONE]\n\n' : `data: ${JSON.stringify(chunk)}\n\n`)
        .join('');

    return new ReadableStream<Uint8Array>({
        start(controller) {
            controller.enqueue(encoder.encode(body));
            controller.close();
        },
    });
}

function parseResponseSse(body: string): Array<{ event: string; data: Record<string, unknown> }> {
    return body
        .trim()
        .split('\n\n')
        .map((frame) => {
            const lines = frame.split('\n');
            const event = lines.find((line) => line.startsWith('event: '))?.slice(7) ?? '';
            const data = lines.find((line) => line.startsWith('data: '))?.slice(6) ?? '{}';
            return { event, data: JSON.parse(data) as Record<string, unknown> };
        });
}

// ─── Unit Tests: responsesInputToChatMessages ───────────────────────

describe('responsesInputToChatMessages', () => {
    it('should convert developer message to system message', () => {
        const input = [{ role: 'developer', content: 'system prompt' }];
        const result = responsesInputToChatMessages(input);

        expect(result).toEqual([{ role: 'system', content: 'system prompt' }]);
    });

    it('should pass through user text message', () => {
        const input = [{ role: 'user', content: 'hello' }];
        const result = responsesInputToChatMessages(input);

        expect(result).toEqual([{ role: 'user', content: 'hello' }]);
    });

    it('should convert user multimodal content to CC format', () => {
        const input = [
            {
                role: 'user',
                content: [
                    { type: 'input_text', text: 'look' },
                    { type: 'input_image', image_url: 'https://example.com/img.png' },
                ],
            },
        ];
        const result = responsesInputToChatMessages(input);

        expect(result).toEqual([
            {
                role: 'user',
                content: [
                    { type: 'text', text: 'look' },
                    { type: 'image_url', image_url: { url: 'https://example.com/img.png' } },
                ],
            },
        ]);
    });

    it('should convert assistant message with output_text', () => {
        const input = [
            {
                type: 'message',
                role: 'assistant',
                content: [{ type: 'output_text', text: 'hi' }],
            },
        ];
        const result = responsesInputToChatMessages(input);

        expect(result).toEqual([{ role: 'assistant', content: 'hi' }]);
    });

    it('should preserve reasoning items on assistant turns', () => {
        const input = [
            { role: 'user', content: 'Continue after inspecting the tool result.' },
            {
                type: 'reasoning',
                id: 'rs_1',
                summary: [{ type: 'summary_text', text: 'I should inspect the tool result first.' }],
            },
            {
                type: 'function_call',
                call_id: 'c1',
                name: 'read_logs',
                arguments: '{"service":"clawroute"}',
            },
        ];
        const result = responsesInputToChatMessages(input);

        expect(result).toEqual([
            { role: 'user', content: 'Continue after inspecting the tool result.' },
            {
                role: 'assistant',
                content: null,
                reasoning_content: 'I should inspect the tool result first.',
                reasoning_item_id: 'rs_1',
                tool_calls: [
                    {
                        id: 'c1',
                        type: 'function',
                        function: { name: 'read_logs', arguments: '{"service":"clawroute"}' },
                    },
                ],
            },
        ]);
    });

    it('should convert function_call to assistant message with tool_calls', () => {
        const input = [
            {
                type: 'function_call',
                call_id: 'c1',
                name: 'search',
                arguments: '{}',
            },
        ];
        const result = responsesInputToChatMessages(input);

        expect(result).toEqual([
            {
                role: 'assistant',
                content: null,
                tool_calls: [
                    {
                        id: 'c1',
                        type: 'function',
                        function: { name: 'search', arguments: '{}' },
                    },
                ],
            },
        ]);
    });

    it('should merge adjacent function_call items into single assistant message', () => {
        const input = [
            {
                type: 'function_call',
                call_id: 'c1',
                name: 'search',
                arguments: '{"q":"gate"}',
            },
            {
                type: 'function_call',
                call_id: 'c2',
                name: 'read_page',
                arguments: '{"url":"https://example.com"}',
            },
        ];
        const result = responsesInputToChatMessages(input);

        expect(result).toHaveLength(1);
        expect(result[0]).toEqual({
            role: 'assistant',
            content: null,
            tool_calls: [
                {
                    id: 'c1',
                    type: 'function',
                    function: { name: 'search', arguments: '{"q":"gate"}' },
                },
                {
                    id: 'c2',
                    type: 'function',
                    function: { name: 'read_page', arguments: '{"url":"https://example.com"}' },
                },
            ],
        });
    });

    it('should convert function_call_output to tool message', () => {
        const input = [
            {
                type: 'function_call_output',
                call_id: 'c1',
                output: 'result text',
            },
        ];
        const result = responsesInputToChatMessages(input);

        expect(result).toEqual([
            { role: 'tool', tool_call_id: 'c1', content: 'result text' },
        ]);
    });

    it('should handle full conversation round-trip', () => {
        const input = [
            { role: 'developer', content: 'You are a helpful assistant.' },
            { role: 'user', content: 'Search for gates' },
            {
                type: 'message',
                role: 'assistant',
                content: [{ type: 'output_text', text: 'I will search for that.' }],
            },
            {
                type: 'function_call',
                call_id: 'fc1',
                name: 'web_search',
                arguments: '{"query":"gates"}',
            },
            {
                type: 'function_call_output',
                call_id: 'fc1',
                output: 'Found 10 results about gates.',
            },
        ];
        const result = responsesInputToChatMessages(input);

        expect(result).toEqual([
            { role: 'system', content: 'You are a helpful assistant.' },
            { role: 'user', content: 'Search for gates' },
            { role: 'assistant', content: 'I will search for that.' },
            {
                role: 'assistant',
                content: null,
                tool_calls: [
                    {
                        id: 'fc1',
                        type: 'function',
                        function: { name: 'web_search', arguments: '{"query":"gates"}' },
                    },
                ],
            },
            { role: 'tool', tool_call_id: 'fc1', content: 'Found 10 results about gates.' },
        ]);
    });
});

// ─── Unit Tests: responsesBodyToChatCompletions ─────────────────────

describe('responsesBodyToChatCompletions', () => {
    it('should translate basic Responses API body to CC request', () => {
        const body = {
            model: 'anthropic/claude-sonnet-4-6',
            input: [{ role: 'user', content: 'hello' }],
            temperature: 0.7,
        };
        const result = responsesBodyToChatCompletions(body);

        expect(result.model).toBe('anthropic/claude-sonnet-4-6');
        expect(result.messages).toEqual([{ role: 'user', content: 'hello' }]);
        expect(result.temperature).toBe(0.7);
    });

    it('should translate tools from flat Responses format to nested CC format', () => {
        const body = {
            model: 'anthropic/claude-sonnet-4-6',
            input: [{ role: 'user', content: 'search' }],
            tools: [
                {
                    type: 'function',
                    name: 'web_search',
                    description: 'Search the web',
                    parameters: {
                        type: 'object',
                        properties: { query: { type: 'string' } },
                        required: ['query'],
                    },
                    strict: true,
                },
            ],
        };
        const result = responsesBodyToChatCompletions(body);

        expect(result.tools).toEqual([
            {
                type: 'function',
                function: {
                    name: 'web_search',
                    description: 'Search the web',
                    parameters: {
                        type: 'object',
                        properties: { query: { type: 'string' } },
                        required: ['query'],
                    },
                    strict: true,
                },
            },
        ]);
    });

    it('should translate reasoning effort and reasoning items', () => {
        const body = {
            model: 'codex/gpt-5.4',
            reasoning: { effort: 'high' },
            input: [
                { role: 'user', content: 'Continue the analysis.' },
                {
                    type: 'reasoning',
                    id: 'rs_1',
                    summary: [{ type: 'summary_text', text: 'Need to check the previous tool output.' }],
                },
            ],
        };
        const result = responsesBodyToChatCompletions(body);

        expect(result.reasoning_effort).toBe('high');
        expect(result.messages).toEqual([
            { role: 'user', content: 'Continue the analysis.' },
            {
                role: 'assistant',
                content: null,
                reasoning_content: 'Need to check the previous tool output.',
                reasoning_item_id: 'rs_1',
            },
        ]);
    });

    it('should use defaults for missing optional fields', () => {
        const body = {
            model: 'google/gemini-2.5-flash',
            input: [{ role: 'user', content: 'hi' }],
        };
        const result = responsesBodyToChatCompletions(body);

        expect(result.model).toBe('google/gemini-2.5-flash');
        expect(result.messages).toEqual([{ role: 'user', content: 'hi' }]);
        expect(result.tools).toBeUndefined();
        expect(result.temperature).toBeUndefined();
    });
});

// ─── Unit Tests: chatCompletionToResponsesBody ──────────────────────

describe('chatCompletionToResponsesBody', () => {
    it('should convert text response to Responses API format', () => {
        const ccResponse = {
            id: 'chatcmpl-123',
            model: 'anthropic/claude-sonnet-4-6',
            choices: [
                {
                    message: { role: 'assistant', content: 'Hello there!' },
                    finish_reason: 'stop',
                },
            ],
            usage: { prompt_tokens: 10, completion_tokens: 5, total_tokens: 15 },
        };
        const result = chatCompletionToResponsesBody(ccResponse);

        expect(result.id).toBe('chatcmpl-123');
        expect(result.object).toBe('response');
        expect(result.model).toBe('anthropic/claude-sonnet-4-6');
        expect(result.status).toBe('completed');
        expect(result.output).toEqual([
            {
                type: 'message',
                role: 'assistant',
                content: [{ type: 'output_text', text: 'Hello there!' }],
            },
        ]);
    });

    it('should convert tool call response to function_call items', () => {
        const ccResponse = {
            id: 'chatcmpl-456',
            model: 'anthropic/claude-sonnet-4-6',
            choices: [
                {
                    message: {
                        role: 'assistant',
                        content: null,
                        tool_calls: [
                            {
                                id: 'tc1',
                                type: 'function',
                                function: { name: 'search', arguments: '{"q":"test"}' },
                            },
                        ],
                    },
                    finish_reason: 'tool_calls',
                },
            ],
            usage: { prompt_tokens: 20, completion_tokens: 10, total_tokens: 30 },
        };
        const result = chatCompletionToResponsesBody(ccResponse);

        expect(result.output).toEqual([
            {
                type: 'function_call',
                call_id: 'tc1',
                name: 'search',
                arguments: '{"q":"test"}',
            },
        ]);
    });

    it('should preserve successful reasoning-only responses without inventing incomplete details', () => {
        const ccResponse = {
            id: 'chatcmpl-reasoning',
            model: 'codex/gpt-5.4',
            choices: [
                {
                    message: {
                        role: 'assistant',
                        content: null,
                        reasoning_content: 'Need to inspect the tool result before answering.',
                    },
                    finish_reason: 'stop',
                },
            ],
            usage: { prompt_tokens: 20, completion_tokens: 8, total_tokens: 28 },
        };
        const result = chatCompletionToResponsesBody(ccResponse);

        expect(result.status).toBe('completed');
        expect(result.incomplete_details).toBeUndefined();
        expect(result.output).toEqual([
            {
                type: 'reasoning',
                summary: [{ type: 'summary_text', text: 'Need to inspect the tool result before answering.' }],
            },
        ]);
    });

    it('should complete successful empty assistant responses without malformed output parts', () => {
        const ccResponse = {
            id: 'chatcmpl-empty',
            model: 'codex/gpt-5.4',
            choices: [
                {
                    message: { role: 'assistant', content: null },
                    finish_reason: 'stop',
                },
            ],
        };
        const result = chatCompletionToResponsesBody(ccResponse);

        expect(result.status).toBe('completed');
        expect(result.incomplete_details).toBeUndefined();
        expect(result.output).toEqual([]);
    });

    it('should map usage fields correctly', () => {
        const ccResponse = {
            id: 'chatcmpl-789',
            model: 'google/gemini-2.5-flash',
            choices: [
                {
                    message: { role: 'assistant', content: 'ok' },
                    finish_reason: 'stop',
                },
            ],
            usage: { prompt_tokens: 100, completion_tokens: 50, total_tokens: 150 },
        };
        const result = chatCompletionToResponsesBody(ccResponse);

        expect(result.usage).toEqual({
            input_tokens: 100,
            output_tokens: 50,
            total_tokens: 150,
        });
    });
});

describe('responsesBodyToSSEResponse', () => {
    it('should stream completed reasoning items without non-contractual incomplete details', async () => {
        const response = responsesBodyToSSEResponse({
            id: 'resp-reasoning',
            object: 'response',
            model: 'codex/gpt-5.4',
            status: 'completed',
            output: [
                {
                    type: 'reasoning',
                    summary: [{ type: 'summary_text', text: 'Need another turn.' }],
                },
            ],
        });

        const body = await readResponseBody(response);

        expect(body).toContain('event: response.reasoning_summary_text.delta');
        expect(body).toContain('"delta":"Need another turn."');
        expect(body).toContain('event: response.completed');
        expect(body).not.toContain('event: response.incomplete');
    });
});

describe('chatCompletionStreamToResponsesSSE', () => {
    it('streams text deltas into Responses SSE events', async () => {
        const response = chatCompletionStreamToResponsesSSE(createChatSseStream([
            { choices: [{ delta: { role: 'assistant' }, finish_reason: null }] },
            { choices: [{ delta: { content: 'Hel' }, finish_reason: null }] },
            { choices: [{ delta: { content: 'lo' }, finish_reason: null }] },
            { choices: [{ delta: {}, finish_reason: 'stop' }], usage: { prompt_tokens: 3, completion_tokens: 2, total_tokens: 5 } },
            '[DONE]',
        ]), { id: 'resp-text', model: 'clawroute/auto' });

        const body = await readResponseBody(response);

        expect(body).toContain('event: response.created');
        const events = parseResponseSse(body);
        const added = events.find((event) => event.event === 'response.output_item.added');
        const done = events.find((event) => event.event === 'response.output_item.done');
        const addedItem = added?.data.item as Record<string, unknown> | undefined;
        const doneItem = done?.data.item as Record<string, unknown> | undefined;
        expect(body).toContain('event: response.output_text.delta');
        expect(body).toContain('"delta":"Hel"');
        expect(addedItem).toMatchObject({ type: 'message', status: 'in_progress' });
        expect(doneItem).toMatchObject({ type: 'message', status: 'completed' });
        expect(doneItem?.id).toBe(addedItem?.id);
        expect(addedItem?.id).toMatch(/^msg_[0-9a-f-]+$/);
        expect(body).toContain('"text":"Hello"');
        expect(body).toContain('event: response.completed');
        expect(body).toContain('"input_tokens":3');
    });

    it('streams successful reasoning-only chunks as completed Responses SSE', async () => {
        const response = chatCompletionStreamToResponsesSSE(createChatSseStream([
            { choices: [{ delta: { reasoning_content: 'Need another step.' }, finish_reason: null }] },
            { choices: [{ delta: {}, finish_reason: 'stop' }] },
            '[DONE]',
        ]), { id: 'resp-reasoning-stream', model: 'clawroute/auto' });

        const body = await readResponseBody(response);

        expect(body).toContain('event: response.reasoning_summary_text.delta');
        expect(body).toContain('"delta":"Need another step."');
        expect(body).toContain('event: response.completed');
        expect(body).not.toContain('event: response.incomplete');
        expect(body).not.toContain('incomplete_details');
    });

    it('streams tool call argument deltas into Responses function call events', async () => {
        const response = chatCompletionStreamToResponsesSSE(createChatSseStream([
            {
                choices: [{
                    delta: {
                        tool_calls: [{
                            index: 0,
                            id: 'call_1',
                            type: 'function',
                            function: { name: 'search', arguments: '{"q":"' },
                        }],
                    },
                    finish_reason: null,
                }],
            },
            {
                choices: [{
                    delta: {
                        tool_calls: [{
                            index: 0,
                            function: { arguments: 'clawroute"}' },
                        }],
                    },
                    finish_reason: null,
                }],
            },
            { choices: [{ delta: {}, finish_reason: 'tool_calls' }] },
            '[DONE]',
        ]), { id: 'resp-tool', model: 'clawroute/auto' });

        const body = await readResponseBody(response);

        expect(body).toContain('event: response.output_item.added');
        expect(body).toContain('"type":"function_call"');
        expect(body).toContain('event: response.function_call_arguments.delta');
        expect(body).toContain('event: response.function_call_arguments.done');
        expect(body).toContain('"arguments":"{\\"q\\":\\"clawroute\\"}"');
        expect(body).toContain('event: response.completed');
    });

    it('maps length finish_reason to response.incomplete', async () => {
        const response = chatCompletionStreamToResponsesSSE(createChatSseStream([
            { choices: [{ delta: { content: 'Partial' }, finish_reason: null }] },
            { choices: [{ delta: {}, finish_reason: 'length' }] },
            '[DONE]',
        ]), { id: 'resp-length', model: 'clawroute/auto' });

        const body = await readResponseBody(response);

        expect(body).toContain('event: response.incomplete');
        expect(body).toContain('"reason":"max_output_tokens"');
    });

    it('streams upstream error payloads as error events without completed events', async () => {
        const response = chatCompletionStreamToResponsesSSE(createChatSseStream([
            { error: { message: 'upstream failed', type: 'server_error', code: 'bad_upstream' } },
        ]), { id: 'resp-error', model: 'clawroute/auto' });

        const body = await readResponseBody(response);

        const events = parseResponseSse(body);
        const errorEvent = events.find((event) => event.event === 'error');

        expect(errorEvent?.data).toEqual({
            type: 'error',
            code: 'bad_upstream',
            message: 'upstream failed',
            param: null,
            sequence_number: 1,
        });
        expect(body).not.toContain('event: response.completed');
    });

    it('preserves usage delivered after finish_reason before completing the response', async () => {
        const response = chatCompletionStreamToResponsesSSE(createChatSseStream([
            { choices: [{ delta: { content: 'Done' }, finish_reason: null }] },
            { choices: [{ delta: {}, finish_reason: 'stop' }] },
            { choices: [], usage: { prompt_tokens: 7, completion_tokens: 2, total_tokens: 9 } },
            '[DONE]',
        ]), { id: 'resp-late-usage', model: 'clawroute/auto' });

        const events = parseResponseSse(await readResponseBody(response));
        const completed = events.find((event) => event.event === 'response.completed');
        const completedResponse = completed?.data.response as Record<string, unknown> | undefined;

        expect(completedResponse?.usage).toEqual({
            input_tokens: 7,
            output_tokens: 2,
            total_tokens: 9,
        });
    });

    it('propagates effective upstream identity to created and completed responses', async () => {
        const response = chatCompletionStreamToResponsesSSE(createChatSseStream([
            {
                id: 'chatcmpl-effective',
                model: 'openai/gpt-5.4',
                created: 1_780_000_000,
                choices: [{ delta: { content: 'Effective' }, finish_reason: null }],
            },
            { choices: [{ delta: {}, finish_reason: 'stop' }] },
            '[DONE]',
        ]), { id: 'resp-requested', model: 'clawroute/auto' });

        const events = parseResponseSse(await readResponseBody(response));
        const created = events.find((event) => event.event === 'response.created');
        const completed = events.find((event) => event.event === 'response.completed');
        const createdResponse = created?.data.response as Record<string, unknown> | undefined;
        const completedResponse = completed?.data.response as Record<string, unknown> | undefined;

        expect(createdResponse).toMatchObject({
            id: 'chatcmpl-effective',
            model: 'openai/gpt-5.4',
            created_at: 1_780_000_000,
        });
        expect(completedResponse).toMatchObject({
            id: 'chatcmpl-effective',
            model: 'openai/gpt-5.4',
            created_at: 1_780_000_000,
        });
    });

    it('completes successful no-output streams without incomplete_details', async () => {
        const response = chatCompletionStreamToResponsesSSE(createChatSseStream([
            { choices: [{ delta: {}, finish_reason: 'stop' }] },
            '[DONE]',
        ]), { id: 'resp-empty', model: 'clawroute/auto' });

        const events = parseResponseSse(await readResponseBody(response));
        const completed = events.find((event) => event.event === 'response.completed');
        const completedResponse = completed?.data.response as Record<string, unknown> | undefined;

        expect(completedResponse).toMatchObject({ status: 'completed', output: [] });
        expect(completedResponse?.incomplete_details).toBeUndefined();
        expect(events.some((event) => event.event === 'response.incomplete')).toBe(false);
    });
});

// ─── Integration Tests: POST /v1/responses ──────────────────────────

const mockFetch = vi.fn();
global.fetch = mockFetch;

function createTestConfig(): ClawRouteConfig {
    return {
        enabled: true,
        dryRun: false,
        proxyPort: 18799,
        proxyHost: '127.0.0.1',
        authToken: null,
        classification: {
            conservativeMode: true,
            minConfidence: 0.7,
            toolAwareRouting: true,
        },
        escalation: {
            enabled: true,
            maxRetries: 2,
            retryDelayMs: 10,
            onlyRetryBeforeStreaming: true,
            onlyRetryWithoutToolCalls: true,
            alwaysFallbackToOriginal: true,
        },
        models: {
            [TaskTier.HEARTBEAT]: { primary: 'google/gemini-2.5-flash-lite', fallback: 'deepseek/deepseek-chat' },
            [TaskTier.SIMPLE]: { primary: 'deepseek/deepseek-chat', fallback: 'google/gemini-2.5-flash' },
            [TaskTier.MODERATE]: { primary: 'google/gemini-2.5-flash', fallback: 'openai/gpt-5-mini' },
            [TaskTier.COMPLEX]: { primary: 'anthropic/claude-sonnet-4-6', fallback: 'openai/gpt-5.2' },
            [TaskTier.FRONTIER_SONNET]: { primary: 'anthropic/claude-sonnet-4-6', fallback: 'openai/gpt-5' },
            [TaskTier.FRONTIER_OPUS]: { primary: 'anthropic/claude-opus-4-6', fallback: 'openai/o3' },
        },
        logging: {
            dbPath: ':memory:',
            logContent: false,
            logSystemPrompts: false,
            debugMode: false,
            retentionDays: 30,
        },
        dashboard: { enabled: true },
        overrides: { globalForceModel: null, sessions: {} },
        apiKeys: {
            anthropic: 'test-key',
            openai: 'test-key',
            codex: 'test-key',
            google: 'test-key',
            deepseek: 'test-key',
            openrouter: 'test-key',
            ollama: 'test-key',
            'x-ai': 'test-key',
            stepfun: 'test-key',
        },
        alerts: {},
    } as ClawRouteConfig;
}

describe('POST /v1/responses', () => {
    let app: Hono;

    beforeAll(async () => {
        const { createApp } = await import('../src/server.js');
        app = createApp(createTestConfig());
    });

    afterAll(() => {
        vi.restoreAllMocks();
    });

    beforeEach(async () => {
        mockFetch.mockReset();
        await initDb(createTestConfig());
    });

    afterEach(() => {
        closeDb();
    });

    it('should return 200 with valid Responses API body', async () => {
        // Mock the provider returning a CC response
        mockFetch.mockResolvedValueOnce(
            new Response(
                JSON.stringify({
                    id: 'chatcmpl-test',
                    model: 'deepseek/deepseek-chat',
                    choices: [
                        {
                            message: { role: 'assistant', content: 'Hello!' },
                            finish_reason: 'stop',
                        },
                    ],
                    usage: { prompt_tokens: 5, completion_tokens: 3, total_tokens: 8 },
                }),
                { status: 200, headers: { 'Content-Type': 'application/json' } },
            ),
        );

        const res = await app.request('/v1/responses', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                model: 'clawroute/auto',
                input: [{ role: 'user', content: 'hi' }],
            }),
        });

        expect(res.status).toBe(200);

        const body = await res.json();
        expect(body.object).toBe('response');
        expect(body.status).toBe('completed');
        expect(body.output).toBeDefined();
        expect(Array.isArray(body.output)).toBe(true);
    });

    it('logs non-streaming Responses API requests with API kind and reasoning effort', async () => {
        mockFetch.mockImplementation(async () => (
            new Response(
                JSON.stringify({
                    id: 'chatcmpl-log-test',
                    model: 'deepseek/deepseek-chat',
                    choices: [{ message: { role: 'assistant', content: 'Logged.' }, finish_reason: 'stop' }],
                    usage: { prompt_tokens: 7, completion_tokens: 2, total_tokens: 9 },
                }),
                { status: 200, headers: { 'Content-Type': 'application/json' } },
            )
        ));

        const res = await app.request('/v1/responses', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                model: 'clawroute/auto',
                reasoning: { effort: 'high' },
                input: [{ role: 'user', content: 'log this response request' }],
            }),
        });
        expect(res.status).toBe(200);
        await new Promise((resolve) => setImmediate(resolve));

        expect(getRecentDecisions(1)[0]).toMatchObject({
            requestApiKind: 'responses',
            requestedReasoningEffort: 'high',
            inputTokens: 7,
            outputTokens: 2,
        });
    });

    it('should stream Responses SSE without waiting for a full Chat Completions JSON body', async () => {
        mockFetch.mockResolvedValueOnce(
            new Response(
                createChatSseStream([
                    { choices: [{ delta: { role: 'assistant' }, finish_reason: null }] },
                    { choices: [{ delta: { content: 'Streaming hello' }, finish_reason: null }] },
                    { choices: [{ delta: {}, finish_reason: 'stop' }], usage: { prompt_tokens: 4, completion_tokens: 2, total_tokens: 6 } },
                    '[DONE]',
                ]),
                { status: 200, headers: { 'Content-Type': 'text/event-stream' } },
            ),
        );

        const res = await app.request('/v1/responses', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                model: 'clawroute/auto',
                stream: true,
                input: [{ role: 'user', content: 'hi' }],
            }),
        });

        expect(res.status).toBe(200);
        expect(res.headers.get('Content-Type')).toContain('text/event-stream');

        const sentBody = JSON.parse(String(mockFetch.mock.calls[0]?.[1]?.body ?? '{}')) as Record<string, unknown>;
        expect(sentBody.stream).toBe(true);

        const body = await readResponseBody(res);
        expect(body).toContain('event: response.created');
        expect(body).toContain('event: response.output_text.delta');
        expect(body).toContain('"delta":"Streaming hello"');
        expect(body).toContain('event: response.completed');
        expect(getRecentDecisions(1)[0]).toMatchObject({
            requestApiKind: 'responses',
            inputTokens: 4,
            outputTokens: 2,
        });
    });

    it('should return 400 for missing model', async () => {
        const res = await app.request('/v1/responses', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({}),
        });

        expect(res.status).toBe(400);

        const body = await res.json();
        expect(body.error).toBeDefined();
        expect(body.error.type).toBe('invalid_request_error');
    });

    it('should return 400 for missing input', async () => {
        const res = await app.request('/v1/responses', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ model: 'clawroute/auto' }),
        });

        expect(res.status).toBe(400);

        const body = await res.json();
        expect(body.error).toBeDefined();
        expect(body.error.type).toBe('invalid_request_error');
    });
});
