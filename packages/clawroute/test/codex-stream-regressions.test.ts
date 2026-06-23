import { describe, expect, it } from 'vitest';
import { buildCodexRequestBody, codexResponseToStream } from '../src/codex-transport.js';
import { pipeStream } from '../src/streaming.js';

function createSseStream(events: Array<{ event: string; data: Record<string, unknown> }>): ReadableStream<Uint8Array> {
    const encoder = new TextEncoder();
    const body = events
        .map(({ event, data }) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)
        .join('');

    return new ReadableStream<Uint8Array>({
        start(controller) {
            controller.enqueue(encoder.encode(body));
            controller.close();
        },
    });
}

async function readStream(stream: ReadableStream<Uint8Array>): Promise<string> {
    const reader = stream.getReader();
    const chunks: Uint8Array[] = [];

    while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        if (value) chunks.push(value);
    }

    return new TextDecoder().decode(Buffer.concat(chunks));
}

describe('codexResponseToStream regressions', () => {
    it('uses response.output_text.done when Codex omits output_text.delta', async () => {
        const upstreamBody = createSseStream([
            {
                event: 'response.output_item.added',
                data: {
                    item: {
                        id: 'msg_1',
                        type: 'message',
                        role: 'assistant',
                        content: [],
                    },
                },
            },
            {
                event: 'response.output_text.done',
                data: {
                    item_id: 'msg_1',
                    text: 'Tool results processed.',
                },
            },
            {
                event: 'response.completed',
                data: {
                    response: {
                        status: 'completed',
                        usage: { input_tokens: 3, output_tokens: 5 },
                    },
                },
            },
        ]);

        const body = await readStream(codexResponseToStream(upstreamBody, 'gpt-5.4', false));
        const parsed = JSON.parse(body) as {
            choices: Array<{ message: { content: string | null } }>;
        };

        expect(parsed.choices[0]?.message.content).toBe('Tool results processed.');
    });

    it('falls back to response.output_item.done content when no text delta events arrive', async () => {
        const upstreamBody = createSseStream([
            {
                event: 'response.output_item.added',
                data: {
                    item: {
                        id: 'msg_2',
                        type: 'message',
                        role: 'assistant',
                        content: [],
                    },
                },
            },
            {
                event: 'response.output_item.done',
                data: {
                    item: {
                        id: 'msg_2',
                        type: 'message',
                        role: 'assistant',
                        content: [{ type: 'output_text', text: 'Done-item fallback text.' }],
                    },
                },
            },
            {
                event: 'response.completed',
                data: {
                    response: {
                        status: 'completed',
                        usage: { input_tokens: 4, output_tokens: 6 },
                    },
                },
            },
        ]);

        const body = await readStream(codexResponseToStream(upstreamBody, 'gpt-5.4', false));
        const parsed = JSON.parse(body) as {
            choices: Array<{ message: { content: string | null } }>;
        };

        expect(parsed.choices[0]?.message.content).toBe('Done-item fallback text.');
    });

    it('uses response.function_call_arguments.done when Codex omits function_call_arguments.delta', async () => {
        const upstreamBody = createSseStream([
            {
                event: 'response.output_item.added',
                data: {
                    item: {
                        id: 'fc_item_1',
                        type: 'function_call',
                        call_id: 'call_1',
                        name: 'write_file',
                    },
                },
            },
            {
                event: 'response.function_call_arguments.done',
                data: {
                    item_id: 'fc_item_1',
                    name: 'write_file',
                    arguments: '{"path":"/tmp/final.md","content":"Done-event tool body."}',
                },
            },
            {
                event: 'response.completed',
                data: {
                    response: {
                        status: 'completed',
                        usage: { input_tokens: 4, output_tokens: 8 },
                    },
                },
            },
        ]);

        const body = await readStream(codexResponseToStream(upstreamBody, 'gpt-5.4', false));
        const parsed = JSON.parse(body) as {
            choices: Array<{
                finish_reason: string;
                message: {
                    content: string | null;
                    tool_calls?: Array<{
                        function: { name: string; arguments: string };
                    }>;
                };
            }>;
        };

        expect(parsed.choices[0]?.finish_reason).toBe('tool_calls');
        expect(parsed.choices[0]?.message.content).toBeNull();
        expect(parsed.choices[0]?.message.tool_calls?.[0]?.function.name).toBe('write_file');
        expect(parsed.choices[0]?.message.tool_calls?.[0]?.function.arguments).toBe('{"path":"/tmp/final.md","content":"Done-event tool body."}');
    });

    it('falls back to function_call data from response.output_item.done when start events are missing', async () => {
        const upstreamBody = createSseStream([
            {
                event: 'response.output_item.done',
                data: {
                    item: {
                        id: 'fc_item_2',
                        type: 'function_call',
                        call_id: 'call_2',
                        name: 'write_file',
                        arguments: '{"path":"/tmp/revision.md","content":"Done-item tool body."}',
                    },
                },
            },
            {
                event: 'response.completed',
                data: {
                    response: {
                        status: 'completed',
                        usage: { input_tokens: 4, output_tokens: 8 },
                    },
                },
            },
        ]);

        const body = await readStream(codexResponseToStream(upstreamBody, 'gpt-5.4', false));
        const parsed = JSON.parse(body) as {
            choices: Array<{
                finish_reason: string;
                message: {
                    content: string | null;
                    tool_calls?: Array<{
                        id: string;
                        function: { name: string; arguments: string };
                    }>;
                };
            }>;
        };

        expect(parsed.choices[0]?.finish_reason).toBe('tool_calls');
        expect(parsed.choices[0]?.message.content).toBeNull();
        expect(parsed.choices[0]?.message.tool_calls?.[0]?.id).toBe('call_2');
        expect(parsed.choices[0]?.message.tool_calls?.[0]?.function.name).toBe('write_file');
        expect(parsed.choices[0]?.message.tool_calls?.[0]?.function.arguments).toBe('{"path":"/tmp/revision.md","content":"Done-item tool body."}');
    });

    it('finishes the stream on response.incomplete with a length finish_reason', async () => {
        const upstreamBody = createSseStream([
            {
                event: 'response.output_text.delta',
                data: {
                    item_id: 'msg_3',
                    delta: 'Partial answer',
                },
            },
            {
                event: 'response.incomplete',
                data: {
                    response: {
                        status: 'incomplete',
                        usage: { input_tokens: 5, output_tokens: 7 },
                    },
                },
            },
        ]);

        const body = await readStream(codexResponseToStream(upstreamBody, 'gpt-5.4', true));

        expect(body).toContain('"content":"Partial answer"');
        expect(body).toContain('"finish_reason":"length"');
        expect(body).toContain('data: [DONE]');
    });

    it('preserves reasoning-only Codex turns as reasoning_content in streaming responses', async () => {
        const upstreamBody = createSseStream([
            {
                event: 'response.reasoning_text.done',
                data: {
                    item_id: 'rs_1',
                    content_index: 0,
                    text: 'Need to inspect the tool output before answering.',
                },
            },
            {
                event: 'response.completed',
                data: {
                    response: {
                        status: 'completed',
                        usage: { input_tokens: 9, output_tokens: 12 },
                    },
                },
            },
        ]);

        const body = await readStream(codexResponseToStream(upstreamBody, 'gpt-5.4', true));

        expect(body).toContain('"reasoning_content":"Need to inspect the tool output before answering."');
        expect(body).toContain('data: [DONE]');
    });

    it('preserves raw reasoning-only Codex turns without readable text as structured reasoning', async () => {
        const upstreamBody = createSseStream([
            {
                event: 'response.output_item.done',
                data: {
                    item: {
                        id: 'rs_raw_1',
                        type: 'reasoning',
                        encrypted_content: 'opaque-reasoning-state',
                    },
                },
            },
            {
                event: 'response.completed',
                data: {
                    response: {
                        status: 'completed',
                        usage: { input_tokens: 9, output_tokens: 2 },
                    },
                },
            },
        ]);

        const body = await readStream(codexResponseToStream(upstreamBody, 'gpt-5.5', true));

        expect(body).toContain('"reasoning_content":" "');
        expect(body).toContain('"content":"<think> </think>"');
        expect(body).toContain('data: [DONE]');
    });

    it('backfills raw reasoning-only Codex turns from terminal response output', async () => {
        const upstreamBody = createSseStream([
            {
                event: 'response.completed',
                data: {
                    response: {
                        status: 'completed',
                        output: [
                            {
                                id: 'rs_raw_2',
                                type: 'reasoning',
                                encrypted_content: 'opaque-terminal-reasoning-state',
                            },
                        ],
                        usage: { input_tokens: 9, output_tokens: 2 },
                    },
                },
            },
        ]);

        const body = await readStream(codexResponseToStream(upstreamBody, 'gpt-5.5', false));
        const parsed = JSON.parse(body) as {
            choices: Array<{ message: { content: string | null; reasoning_content?: string } }>;
        };

        expect(parsed.choices[0]?.message.content).toBe('<think> </think>');
        expect(parsed.choices[0]?.message.reasoning_content).toBe(' ');
    });

    it('reports provider error events while piping streaming responses', async () => {
        const upstreamResponse = new Response(createSseStream([
            {
                event: 'error',
                data: {
                    error: {
                        message: 'Codex upstream failed',
                        code: 'server_error',
                        type: 'server_error',
                        slot: 1,
                    },
                },
            },
        ]), { status: 200, headers: { 'Content-Type': 'text/event-stream' } });
        const written: string[] = [];
        const decoder = new TextDecoder();
        const writable = new WritableStream<Uint8Array>({
            write(chunk) {
                written.push(decoder.decode(chunk));
            },
        });

        const result = await pipeStream(upstreamResponse, writable.getWriter());

        expect(written.join('')).toContain('"code":"server_error"');
        expect(result.error).toBe('Codex upstream failed [slot:1 code:server_error]');
    });

    it('round-trips assistant reasoning_content into Responses reasoning items', () => {
        const requestBody = buildCodexRequestBody({
            messages: [
                { role: 'user', content: 'Inspect the failing tool output.' },
                {
                    role: 'assistant',
                    content: null,
                    reasoning_content: 'I should inspect the tool output and then continue.',
                    tool_calls: [
                        {
                            id: 'call_1',
                            type: 'function',
                            function: { name: 'read_logs', arguments: '{"service":"clawroute"}' },
                        },
                    ],
                },
                { role: 'tool', tool_call_id: 'call_1', content: 'tool output' },
            ],
        }, 'gpt-5.4');

        expect(requestBody.input).toEqual([
            { role: 'user', content: 'Inspect the failing tool output.' },
            {
                type: 'reasoning',
                summary: [{ type: 'summary_text', text: 'I should inspect the tool output and then continue.' }],
            },
            {
                type: 'function_call',
                call_id: 'call_1',
                name: 'read_logs',
                arguments: '{"service":"clawroute"}',
            },
            {
                type: 'function_call_output',
                call_id: 'call_1',
                output: 'tool output',
            },
        ]);
    });
});
