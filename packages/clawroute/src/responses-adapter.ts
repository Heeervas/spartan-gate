/**
 * ClawRoute Responses API Adapter
 *
 * Translates between OpenAI Responses API format and Chat Completions format.
 * Used by the /v1/responses endpoint.
 */

import type { ChatMessage, ChatCompletionRequest, ContentPart, ToolDefinition } from './types.js';

type AssistantMessage = ChatMessage & Record<string, unknown>;

type SseFrame = {
    data: string;
};

type StreamItemState = {
    id: string;
    outputIndex: number;
    done: boolean;
};

type StreamToolCallState = StreamItemState & {
    callId: string;
    name: string;
    arguments: string;
};

function lastAssistantMessage(messages: ChatMessage[]): AssistantMessage | undefined {
    const message = messages[messages.length - 1];
    if (!message || message.role !== 'assistant') {
        return undefined;
    }
    return message as AssistantMessage;
}

function extractReasoningText(item: Record<string, unknown>): string | undefined {
    const parts = [item.summary, item.content]
        .filter(Array.isArray)
        .flatMap(section => section as Array<Record<string, unknown>>)
        .map(part => {
            if (part.type === 'summary_text' || part.type === 'reasoning_text') {
                return typeof part.text === 'string' ? part.text : '';
            }
            return '';
        })
        .filter(Boolean);

    return parts.length > 0 ? parts.join('') : undefined;
}

/**
 * Convert Responses API input items to Chat Completions messages.
 *
 * Handles: developer, user (text + multimodal), assistant messages,
 * function_call (with adjacent merging), and function_call_output.
 */
export function responsesInputToChatMessages(input: unknown[]): ChatMessage[] {
    const messages: ChatMessage[] = [];

    for (const item of input) {
        const obj = item as Record<string, unknown>;

        // developer → system
        if (obj.role === 'developer') {
            messages.push({ role: 'system', content: obj.content as string });
            continue;
        }

        // user message (string or multimodal array)
        if (obj.role === 'user') {
            const content = obj.content;
            if (typeof content === 'string') {
                messages.push({ role: 'user', content });
            } else if (Array.isArray(content)) {
                // Convert Responses API content parts to CC format
                const ccParts: ContentPart[] = content.map((part: Record<string, unknown>) => {
                    if (part.type === 'input_text') {
                        return { type: 'text' as const, text: part.text as string };
                    }
                    if (part.type === 'input_image') {
                        return {
                            type: 'image_url' as const,
                            image_url: { url: part.image_url as string },
                        };
                    }
                    return part as unknown as ContentPart;
                });
                messages.push({ role: 'user', content: ccParts });
            }
            continue;
        }

        if (obj.type === 'reasoning') {
            const reasoningContent = extractReasoningText(obj);
            if (!reasoningContent) {
                continue;
            }

            messages.push({
                role: 'assistant',
                content: null,
                reasoning_content: reasoningContent,
                reasoning_item_id: obj.id as string | undefined,
            } as ChatMessage);
            continue;
        }

        // assistant message with output_text
        if (obj.type === 'message' && obj.role === 'assistant') {
            const contentArr = obj.content as Array<Record<string, unknown>>;
            const textPart = contentArr?.find((p) => p.type === 'output_text');

            const previousAssistant = lastAssistantMessage(messages);
            if (
                previousAssistant &&
                previousAssistant.content === null &&
                typeof previousAssistant.reasoning_content === 'string'
            ) {
                previousAssistant.content = (textPart?.text as string) ?? null;
                continue;
            }

            messages.push({
                role: 'assistant',
                content: (textPart?.text as string) ?? null,
            });
            continue;
        }

        // function_call → assistant message with tool_calls (merge adjacent)
        if (obj.type === 'function_call') {
            const toolCall = {
                id: obj.call_id as string,
                type: 'function' as const,
                function: {
                    name: obj.name as string,
                    arguments: obj.arguments as string,
                },
            };

            // Merge into previous assistant message if it has tool_calls
            const previousAssistant = lastAssistantMessage(messages);
            if (previousAssistant?.tool_calls) {
                previousAssistant.tool_calls.push(toolCall);
            } else if (
                previousAssistant &&
                previousAssistant.content === null &&
                typeof previousAssistant.reasoning_content === 'string'
            ) {
                previousAssistant.tool_calls = [toolCall];
            } else {
                messages.push({
                    role: 'assistant',
                    content: null,
                    tool_calls: [toolCall],
                });
            }
            continue;
        }

        // function_call_output → tool message
        if (obj.type === 'function_call_output') {
            messages.push({
                role: 'tool',
                tool_call_id: obj.call_id as string,
                content: obj.output as string,
            });
            continue;
        }
    }

    return messages;
}

/**
 * Translate a full Responses API request body to a Chat Completions request.
 */
export function responsesBodyToChatCompletions(
    body: Record<string, unknown>,
): ChatCompletionRequest {
    const messages = responsesInputToChatMessages(body.input as unknown[]);

    // Prepend instructions as system message if present
    if (typeof body.instructions === 'string' && body.instructions.length > 0) {
        messages.unshift({ role: 'system', content: body.instructions });
    }

    const ccRequest: ChatCompletionRequest = {
        model: body.model as string,
        messages,
    };

    // Optional scalar fields
    if (body.temperature !== undefined) ccRequest.temperature = body.temperature as number;
    if (body.max_output_tokens !== undefined) ccRequest.max_tokens = body.max_output_tokens as number;
    if (body.top_p !== undefined) ccRequest.top_p = body.top_p as number;
    if (body.stream !== undefined) ccRequest.stream = body.stream as boolean;

    const reasoning = body.reasoning as Record<string, unknown> | undefined;
    if (typeof reasoning?.effort === 'string') {
        ccRequest.reasoning_effort = reasoning.effort;
    }

    // Tools: flat Responses format → nested CC format
    if (Array.isArray(body.tools)) {
        ccRequest.tools = (body.tools as Array<Record<string, unknown>>).map((t) => ({
            type: 'function' as const,
            function: {
                name: t.name as string,
                description: t.description as string | undefined,
                parameters: t.parameters as object | undefined,
                strict: t.strict as boolean | undefined,
            },
        })) as ToolDefinition[];
    }

    if (body.tool_choice !== undefined) ccRequest.tool_choice = body.tool_choice as string | object;

    return ccRequest;
}

/**
 * Convert a Chat Completions response to Responses API format (non-streaming).
 */
export function chatCompletionToResponsesBody(
    ccResponse: Record<string, unknown>,
): Record<string, unknown> {
    const choices = ccResponse.choices as Array<Record<string, unknown>> | undefined;
    const firstChoice = choices?.[0];
    const message = firstChoice?.message as Record<string, unknown> | undefined;

    const output: unknown[] = [];

    if (message) {
        const toolCalls = message.tool_calls as Array<Record<string, unknown>> | undefined;
        const reasoningContent = typeof message.reasoning_content === 'string'
            ? message.reasoning_content.trim()
            : '';

        if (reasoningContent.length > 0) {
            output.push({
                type: 'reasoning',
                summary: [{ type: 'summary_text', text: reasoningContent }],
            });
        }

        if (toolCalls && toolCalls.length > 0) {
            // Tool call response → function_call items
            for (const tc of toolCalls) {
                const fn = tc.function as Record<string, unknown>;
                output.push({
                    type: 'function_call',
                    call_id: tc.id,
                    name: fn.name,
                    arguments: fn.arguments,
                });
            }
        } else {
            const text = typeof message.content === 'string' ? message.content : '';

            // Text response → message with output_text
            if (text.length > 0) {
                output.push({
                    type: 'message',
                    role: 'assistant',
                    content: [{ type: 'output_text', text }],
                });
            }
        }
    }

    const usage = ccResponse.usage as Record<string, number> | undefined;
    const finishReason = firstChoice?.finish_reason;
    const incompleteReason = finishReason === 'length'
        ? 'max_output_tokens'
        : finishReason === 'content_filter'
            ? 'content_filter'
            : undefined;
    const status = incompleteReason ? 'incomplete' : 'completed';

    return {
        id: ccResponse.id,
        object: 'response',
        model: ccResponse.model,
        status,
        output,
        ...(incompleteReason && {
            incomplete_details: { reason: incompleteReason },
        }),
        ...(usage && {
            usage: {
                input_tokens: usage.prompt_tokens,
                output_tokens: usage.completion_tokens,
                total_tokens: usage.total_tokens,
            },
        }),
    };
}

/**
 * Convert a completed Responses API body into an SSE Response
 * suitable for clients that called with stream: true.
 *
 * Emits the minimum event sequence the OpenAI Python SDK expects:
 *   response.created → output items → response.completed
 *
 * Each event's data payload must include:
 *   - `type` matching the SSE event name (SDK uses this for discriminated union)
 *   - `sequence_number` (incrementing integer)
 *   - `item_id` on content/text/function_call events
 *
 * `response.created` and `response.completed` wrap the body under a `response` key.
 */
export function responsesBodyToSSEResponse(
    body: Record<string, unknown>,
): Response {
    const encoder = new TextEncoder();
    const events: string[] = [];
    let seq = 0;

    // Build a Response-shaped object with required SDK fields
    const now = Date.now() / 1000;
    const responseBase = {
        ...body,
        created_at: (body.created_at as number) ?? now,
        tools: (body.tools as unknown[]) ?? [],
        tool_choice: (body.tool_choice as string) ?? 'auto',
        parallel_tool_calls: (body.parallel_tool_calls as boolean) ?? true,
    };

    // 1. response.created — response with in_progress status, empty output
    const createdResponse = { ...responseBase, status: 'in_progress', output: [] };
    events.push(`event: response.created\ndata: ${JSON.stringify({
        type: 'response.created', sequence_number: seq++, response: createdResponse,
    })}\n\n`);

    // 2. Per-output-item events
    const output = (body.output || []) as Array<Record<string, unknown>>;
    for (let oi = 0; oi < output.length; oi++) {
        const item = output[oi]!;
        const itemId = (item.id as string) ?? `${item.type === 'message' ? 'msg' : 'item'}_${crypto.randomUUID()}`;

        if (item.type === 'message') {
            const itemWithId = { ...item, id: itemId, status: 'in_progress', content: [] };
            events.push(`event: response.output_item.added\ndata: ${JSON.stringify({
                type: 'response.output_item.added', sequence_number: seq++,
                output_index: oi, item: itemWithId,
            })}\n\n`);

            const content = (item.content || []) as Array<Record<string, unknown>>;
            for (let ci = 0; ci < content.length; ci++) {
                const part = content[ci]!;
                if (part.type === 'output_text') {
                    events.push(`event: response.content_part.added\ndata: ${JSON.stringify({
                        type: 'response.content_part.added', sequence_number: seq++,
                        output_index: oi, content_index: ci, item_id: itemId,
                        part: { type: 'output_text', text: '', annotations: [] },
                    })}\n\n`);
                    events.push(`event: response.output_text.delta\ndata: ${JSON.stringify({
                        type: 'response.output_text.delta', sequence_number: seq++,
                        output_index: oi, content_index: ci, item_id: itemId,
                        delta: part.text,
                    })}\n\n`);
                    events.push(`event: response.output_text.done\ndata: ${JSON.stringify({
                        type: 'response.output_text.done', sequence_number: seq++,
                        output_index: oi, content_index: ci, item_id: itemId,
                        text: part.text,
                    })}\n\n`);
                    events.push(`event: response.content_part.done\ndata: ${JSON.stringify({
                        type: 'response.content_part.done', sequence_number: seq++,
                        output_index: oi, content_index: ci, item_id: itemId,
                        part: { ...part, annotations: [] },
                    })}\n\n`);
                }
            }

            const doneItem = { ...item, id: itemId, status: 'completed' };
            events.push(`event: response.output_item.done\ndata: ${JSON.stringify({
                type: 'response.output_item.done', sequence_number: seq++,
                output_index: oi, item: doneItem,
            })}\n\n`);
        } else if (item.type === 'reasoning') {
            const itemWithId = { ...item, id: itemId };
            events.push(`event: response.output_item.added\ndata: ${JSON.stringify({
                type: 'response.output_item.added', sequence_number: seq++,
                output_index: oi, item: itemWithId,
            })}\n\n`);

            const summary = (item.summary || []) as Array<Record<string, unknown>>;
            for (let si = 0; si < summary.length; si++) {
                const part = summary[si]!;
                if (part.type === 'summary_text' && typeof part.text === 'string') {
                    events.push(`event: response.reasoning_summary_text.delta\ndata: ${JSON.stringify({
                        type: 'response.reasoning_summary_text.delta', sequence_number: seq++,
                        output_index: oi, summary_index: si, item_id: itemId,
                        delta: part.text,
                    })}\n\n`);
                    events.push(`event: response.reasoning_summary_text.done\ndata: ${JSON.stringify({
                        type: 'response.reasoning_summary_text.done', sequence_number: seq++,
                        output_index: oi, summary_index: si, item_id: itemId,
                        text: part.text,
                    })}\n\n`);
                }
            }

            events.push(`event: response.output_item.done\ndata: ${JSON.stringify({
                type: 'response.output_item.done', sequence_number: seq++,
                output_index: oi, item: itemWithId,
            })}\n\n`);
        } else if (item.type === 'function_call') {
            const itemWithId = { ...item, id: itemId, arguments: '' };
            events.push(`event: response.output_item.added\ndata: ${JSON.stringify({
                type: 'response.output_item.added', sequence_number: seq++,
                output_index: oi, item: itemWithId,
            })}\n\n`);
            events.push(`event: response.function_call_arguments.delta\ndata: ${JSON.stringify({
                type: 'response.function_call_arguments.delta', sequence_number: seq++,
                output_index: oi, item_id: itemId,
                delta: item.arguments,
            })}\n\n`);
            events.push(`event: response.function_call_arguments.done\ndata: ${JSON.stringify({
                type: 'response.function_call_arguments.done', sequence_number: seq++,
                output_index: oi, item_id: itemId,
                name: item.name,
                arguments: item.arguments,
            })}\n\n`);
            const doneItem = { ...item, id: itemId };
            events.push(`event: response.output_item.done\ndata: ${JSON.stringify({
                type: 'response.output_item.done', sequence_number: seq++,
                output_index: oi, item: doneItem,
            })}\n\n`);
        }
    }

    // 3. Terminal response event.
    const terminalStatus = body.status === 'incomplete' ? 'incomplete' : 'completed';
    const terminalEvent = terminalStatus === 'incomplete' ? 'response.incomplete' : 'response.completed';
    const completedResponse = { ...responseBase, status: terminalStatus };
    events.push(`event: ${terminalEvent}\ndata: ${JSON.stringify({
        type: terminalEvent, sequence_number: seq++, response: completedResponse,
    })}\n\n`);

    const stream = new ReadableStream({
        start(controller) {
            for (const ev of events) {
                controller.enqueue(encoder.encode(ev));
            }
            controller.close();
        },
    });

    return new Response(stream, {
        status: 200,
        headers: {
            'Content-Type': 'text/event-stream',
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
        },
    });
}

function responseBaseFromBody(
    body: Record<string, unknown>,
    responseId: string,
    model: string,
    status: string,
    output: unknown[],
): Record<string, unknown> {
    const now = Date.now() / 1000;
    return {
        ...body,
        id: responseId,
        object: 'response',
        model,
        status,
        output,
        created_at: (body.created_at as number) ?? now,
        tools: (body.tools as unknown[]) ?? [],
        tool_choice: (body.tool_choice as string) ?? 'auto',
        parallel_tool_calls: (body.parallel_tool_calls as boolean) ?? true,
    };
}

function parseSseFrames(buffer: string): { frames: SseFrame[]; rest: string } {
    const normalized = buffer.replace(/\r\n/g, '\n');
    const parts = normalized.split('\n\n');
    const rest = parts.pop() ?? '';
    const frames = parts
        .map((frame) => {
            const data = frame
                .split('\n')
                .filter((line) => line.startsWith('data:'))
                .map((line) => line.slice(5).trimStart())
                .join('\n');
            return data ? { data } : null;
        })
        .filter((frame): frame is SseFrame => frame !== null);

    return { frames, rest };
}

function chatUsageToResponsesUsage(usage: Record<string, unknown> | undefined): Record<string, unknown> | undefined {
    if (!usage) return undefined;
    const inputTokens = typeof usage.prompt_tokens === 'number' ? usage.prompt_tokens : undefined;
    const outputTokens = typeof usage.completion_tokens === 'number' ? usage.completion_tokens : undefined;
    const totalTokens = typeof usage.total_tokens === 'number'
        ? usage.total_tokens
        : (inputTokens ?? 0) + (outputTokens ?? 0);

    const promptDetails = usage.prompt_tokens_details as Record<string, unknown> | undefined;
    const cachedTokens = typeof promptDetails?.cached_tokens === 'number'
        ? promptDetails.cached_tokens
        : undefined;

    return {
        ...(inputTokens !== undefined ? { input_tokens: inputTokens } : {}),
        ...(outputTokens !== undefined ? { output_tokens: outputTokens } : {}),
        ...(cachedTokens !== undefined ? { input_tokens_details: { cached_tokens: cachedTokens } } : {}),
        total_tokens: totalTokens,
    };
}

function enqueueSse(
    controller: ReadableStreamDefaultController<Uint8Array>,
    encoder: InstanceType<typeof TextEncoder>,
    event: string,
    payload: Record<string, unknown>,
): void {
    controller.enqueue(encoder.encode(`event: ${event}\ndata: ${JSON.stringify(payload)}\n\n`));
}

function enqueueResponsesError(
    controller: ReadableStreamDefaultController<Uint8Array>,
    encoder: InstanceType<typeof TextEncoder>,
    error: unknown,
    sequenceNumber: number,
): void {
    const errorRecord = typeof error === 'object' && error !== null
        ? error as Record<string, unknown>
        : { message: String(error || 'Upstream stream error') };
    enqueueSse(controller, encoder, 'error', {
        type: 'error',
        code: typeof errorRecord.code === 'string' ? errorRecord.code : null,
        message: typeof errorRecord.message === 'string'
            ? errorRecord.message
            : 'Upstream stream error',
        param: typeof errorRecord.param === 'string' ? errorRecord.param : null,
        sequence_number: sequenceNumber,
    });
}

/**
 * Convert a Chat Completions SSE stream into Responses API SSE without buffering
 * the full model response first.
 */
export function chatCompletionStreamToResponsesSSE(
    upstreamBody: ReadableStream<Uint8Array>,
    baseBody: Record<string, unknown>,
): Response {
    const encoder = new TextEncoder();
    const decoder = new TextDecoder();
    let responseId = typeof baseBody.id === 'string' ? baseBody.id : `resp_${crypto.randomUUID()}`;
    let model = typeof baseBody.model === 'string' ? baseBody.model : 'unknown';
    let createdAt = typeof baseBody.created_at === 'number' ? baseBody.created_at : undefined;
    const output: unknown[] = [];
    const toolCalls = new Map<number, StreamToolCallState>();
    let sequenceNumber = 0;
    let nextOutputIndex = 0;
    let messageState: StreamItemState | null = null;
    let reasoningState: StreamItemState | null = null;
    let messageText = '';
    let reasoningText = '';
    let usage: Record<string, unknown> | undefined;
    let terminalStatus: 'completed' | 'incomplete' = 'completed';
    let incompleteReason: 'max_output_tokens' | 'content_filter' | undefined;
    let createdEmitted = false;
    let terminalEmitted = false;

    function emit(
        controller: ReadableStreamDefaultController<Uint8Array>,
        event: string,
        payload: Record<string, unknown>,
    ): void {
        enqueueSse(controller, encoder, event, {
            type: event,
            sequence_number: sequenceNumber++,
            ...payload,
        });
    }

    function ensureMessage(controller: ReadableStreamDefaultController<Uint8Array>): StreamItemState {
        if (messageState) return messageState;
        messageState = { id: `msg_${crypto.randomUUID()}`, outputIndex: nextOutputIndex++, done: false };
        emit(controller, 'response.output_item.added', {
            output_index: messageState.outputIndex,
            item: {
                id: messageState.id,
                type: 'message',
                status: 'in_progress',
                role: 'assistant',
                content: [],
            },
        });
        emit(controller, 'response.content_part.added', {
            output_index: messageState.outputIndex,
            content_index: 0,
            item_id: messageState.id,
            part: { type: 'output_text', text: '', annotations: [] },
        });
        return messageState;
    }

    function effectiveBaseBody(): Record<string, unknown> {
        return createdAt === undefined ? baseBody : { ...baseBody, created_at: createdAt };
    }

    function emitCreated(controller: ReadableStreamDefaultController<Uint8Array>): void {
        if (createdEmitted) return;
        createdEmitted = true;
        emit(controller, 'response.created', {
            response: responseBaseFromBody(effectiveBaseBody(), responseId, model, 'in_progress', []),
        });
    }

    function ensureReasoning(controller: ReadableStreamDefaultController<Uint8Array>): StreamItemState {
        if (reasoningState) return reasoningState;
        reasoningState = { id: `rs_${crypto.randomUUID()}`, outputIndex: nextOutputIndex++, done: false };
        emit(controller, 'response.output_item.added', {
            output_index: reasoningState.outputIndex,
            item: { id: reasoningState.id, type: 'reasoning', summary: [] },
        });
        return reasoningState;
    }

    function ensureToolCall(
        controller: ReadableStreamDefaultController<Uint8Array>,
        index: number,
        chunk: Record<string, unknown>,
    ): StreamToolCallState {
        const existing = toolCalls.get(index);
        if (existing) {
            const fn = chunk.function as Record<string, unknown> | undefined;
            if (!existing.name && typeof fn?.name === 'string') {
                existing.name = fn.name;
            }
            return existing;
        }

        const fn = chunk.function as Record<string, unknown> | undefined;
        const callId = typeof chunk.id === 'string' ? chunk.id : `call_${index}`;
        const name = typeof fn?.name === 'string' ? fn.name : '';
        const state: StreamToolCallState = {
            id: `fc_${crypto.randomUUID()}`,
            outputIndex: nextOutputIndex++,
            done: false,
            callId,
            name,
            arguments: '',
        };
        toolCalls.set(index, state);
        emit(controller, 'response.output_item.added', {
            output_index: state.outputIndex,
            item: {
                id: state.id,
                type: 'function_call',
                call_id: state.callId,
                name: state.name,
                arguments: '',
            },
        });
        return state;
    }

    function finalizeOpenItems(controller: ReadableStreamDefaultController<Uint8Array>): void {
        if (reasoningState && !reasoningState.done) {
            reasoningState.done = true;
            const reasoningItem = {
                id: reasoningState.id,
                type: 'reasoning',
                summary: reasoningText.length > 0
                    ? [{ type: 'summary_text', text: reasoningText }]
                    : [],
            };
            output.push(reasoningItem);
            emit(controller, 'response.reasoning_summary_text.done', {
                output_index: reasoningState.outputIndex,
                summary_index: 0,
                item_id: reasoningState.id,
                text: reasoningText,
            });
            emit(controller, 'response.output_item.done', {
                output_index: reasoningState.outputIndex,
                item: reasoningItem,
            });
        }

        if (messageState && !messageState.done) {
            messageState.done = true;
            const messageItem = {
                id: messageState.id,
                type: 'message',
                status: 'completed',
                role: 'assistant',
                content: [{ type: 'output_text', text: messageText, annotations: [] }],
            };
            output.push(messageItem);
            emit(controller, 'response.output_text.done', {
                output_index: messageState.outputIndex,
                content_index: 0,
                item_id: messageState.id,
                text: messageText,
            });
            emit(controller, 'response.content_part.done', {
                output_index: messageState.outputIndex,
                content_index: 0,
                item_id: messageState.id,
                part: { type: 'output_text', text: messageText, annotations: [] },
            });
            emit(controller, 'response.output_item.done', {
                output_index: messageState.outputIndex,
                item: messageItem,
            });
        }

        for (const state of [...toolCalls.values()].sort((a, b) => a.outputIndex - b.outputIndex)) {
            if (state.done) continue;
            state.done = true;
            const toolItem = {
                id: state.id,
                type: 'function_call',
                call_id: state.callId,
                name: state.name,
                arguments: state.arguments,
            };
            output.push(toolItem);
            emit(controller, 'response.function_call_arguments.done', {
                output_index: state.outputIndex,
                item_id: state.id,
                name: state.name,
                arguments: state.arguments,
            });
            emit(controller, 'response.output_item.done', {
                output_index: state.outputIndex,
                item: toolItem,
            });
        }
    }

    function emitTerminal(controller: ReadableStreamDefaultController<Uint8Array>): void {
        if (terminalEmitted) return;
        terminalEmitted = true;
        finalizeOpenItems(controller);

        const terminalEvent = terminalStatus === 'incomplete' ? 'response.incomplete' : 'response.completed';
        const response = responseBaseFromBody(effectiveBaseBody(), responseId, model, terminalStatus, output);
        if (usage) response.usage = usage;
        if (incompleteReason) {
            response.incomplete_details = { reason: incompleteReason };
        }
        emit(controller, terminalEvent, { response });
    }

    function handleChunk(
        controller: ReadableStreamDefaultController<Uint8Array>,
        parsed: Record<string, unknown>,
    ): void {
        if (terminalEmitted) return;

        if (!createdEmitted) {
            if (typeof parsed.id === 'string') responseId = parsed.id;
            if (typeof parsed.model === 'string') model = parsed.model;
            if (typeof parsed.created === 'number') createdAt = parsed.created;
            emitCreated(controller);
        }

        if (parsed.error) {
            enqueueResponsesError(controller, encoder, parsed.error, sequenceNumber++);
            terminalEmitted = true;
            return;
        }

        const parsedUsage = chatUsageToResponsesUsage(parsed.usage as Record<string, unknown> | undefined);
        if (parsedUsage) usage = parsedUsage;

        const choices = parsed.choices as Array<Record<string, unknown>> | undefined;
        const choice = choices?.[0];
        const delta = choice?.delta as Record<string, unknown> | undefined;
        const finishReason = choice?.finish_reason;

        if (delta) {
            const reasoningDelta = delta.reasoning_content;
            if (typeof reasoningDelta === 'string' && reasoningDelta.length > 0) {
                const state = ensureReasoning(controller);
                reasoningText += reasoningDelta;
                emit(controller, 'response.reasoning_summary_text.delta', {
                    output_index: state.outputIndex,
                    summary_index: 0,
                    item_id: state.id,
                    delta: reasoningDelta,
                });
            }

            const textDelta = delta.content;
            if (typeof textDelta === 'string' && textDelta.length > 0) {
                const state = ensureMessage(controller);
                messageText += textDelta;
                emit(controller, 'response.output_text.delta', {
                    output_index: state.outputIndex,
                    content_index: 0,
                    item_id: state.id,
                    delta: textDelta,
                });
            }

            const deltaToolCalls = delta.tool_calls as Array<Record<string, unknown>> | undefined;
            for (const toolCall of deltaToolCalls ?? []) {
                const index = typeof toolCall.index === 'number' ? toolCall.index : 0;
                const state = ensureToolCall(controller, index, toolCall);
                const fn = toolCall.function as Record<string, unknown> | undefined;
                const argumentDelta = typeof fn?.arguments === 'string' ? fn.arguments : '';
                if (argumentDelta.length > 0) {
                    state.arguments += argumentDelta;
                    emit(controller, 'response.function_call_arguments.delta', {
                        output_index: state.outputIndex,
                        item_id: state.id,
                        delta: argumentDelta,
                    });
                }
            }
        }

        if (finishReason) {
            if (finishReason === 'length') {
                terminalStatus = 'incomplete';
                incompleteReason = 'max_output_tokens';
            } else if (finishReason === 'content_filter') {
                terminalStatus = 'incomplete';
                incompleteReason = 'content_filter';
            }
        }
    }

    const stream = new ReadableStream<Uint8Array>({
        async start(controller) {
            const reader = upstreamBody.getReader();
            let buffer = '';
            try {
                while (true) {
                    const { done, value } = await reader.read();
                    if (done) break;
                    buffer += decoder.decode(value, { stream: true });
                    const parsedFrames = parseSseFrames(buffer);
                    buffer = parsedFrames.rest;
                    for (const frame of parsedFrames.frames) {
                        if (frame.data === '[DONE]') continue;
                        try {
                            handleChunk(controller, JSON.parse(frame.data) as Record<string, unknown>);
                        } catch {
                            // Ignore malformed upstream frames and continue streaming.
                        }
                    }
                }

                buffer += decoder.decode();
                const parsedFrames = parseSseFrames(`${buffer}\n\n`);
                for (const frame of parsedFrames.frames) {
                    if (terminalEmitted || frame.data === '[DONE]') continue;
                    try {
                        handleChunk(controller, JSON.parse(frame.data) as Record<string, unknown>);
                    } catch {
                        // Ignore malformed trailing frames.
                    }
                }

                if (!terminalEmitted) {
                    emitCreated(controller);
                    emitTerminal(controller);
                }
                controller.close();
            } catch (error) {
                if (!terminalEmitted) {
                    emitCreated(controller);
                    enqueueResponsesError(
                        controller,
                        encoder,
                        { message: error instanceof Error ? error.message : 'Stream conversion failed', type: 'server_error' },
                        sequenceNumber++,
                    );
                }
                controller.close();
            } finally {
                try {
                    reader.releaseLock();
                } catch {
                    // Ignore.
                }
            }
        },
    });

    return new Response(stream, {
        status: 200,
        headers: {
            'Content-Type': 'text/event-stream',
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
        },
    });
}
