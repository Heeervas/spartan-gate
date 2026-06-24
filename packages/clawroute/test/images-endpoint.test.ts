import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { Hono } from 'hono';
import { ClawRouteConfig, TaskTier } from '../src/types.js';

const AUTH_TOKEN = 'image-route-secret';
const UNAUTHORIZED = {
    error: {
        message: 'Unauthorized. Provide Bearer token in Authorization header.',
        type: 'authentication_error',
        code: 'unauthorized',
    },
};

const mockFetch = vi.fn();
global.fetch = mockFetch;

function createTestConfig(authToken: string | null = null): ClawRouteConfig {
    return {
        enabled: true,
        dryRun: false,
        baselineModel: 'openai/gpt-5.2',
        providerProfile: null,
        proxyPort: 18799,
        proxyHost: '127.0.0.1',
        authToken,
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
            openrouter: '',
            ollama: '',
            'x-ai': '',
            stepfun: '',
        },
        alerts: {},
    } as ClawRouteConfig;
}

function postJson(body: unknown, token?: string): RequestInit {
    const headers: Record<string, string> = { 'Content-Type': 'application/json' };
    if (token) {
        headers.Authorization = `Bearer ${token}`;
    }

    return {
        method: 'POST',
        headers,
        body: JSON.stringify(body),
    };
}

function postMultipart(build: (form: FormData) => void, token?: string): RequestInit {
    const form = new FormData();
    build(form);
    const headers: Record<string, string> = {};
    if (token) {
        headers.Authorization = `Bearer ${token}`;
    }

    return {
        method: 'POST',
        headers,
        body: form,
    };
}

function createPngFile(name: string, bytes: number[] = [137, 80, 78, 71]): File {
    return new File([new Uint8Array(bytes)], name, { type: 'image/png' });
}

function createImageResponse(data: Array<Record<string, string>>): Response {
    return new Response(
        JSON.stringify({
            created: 1715430000,
            data,
        }),
        {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
        },
    );
}

function createCodexImageStreamResponse(imageB64: string): Response {
    const encoder = new TextEncoder();
    const body = [
        'event: response.output_item.done',
        `data: ${JSON.stringify({ item: { type: 'image_generation_call', result: imageB64 } })}`,
        '',
        'event: response.completed',
        `data: ${JSON.stringify({ response: { status: 'completed', output: [{ type: 'image_generation_call', result: imageB64 }] } })}`,
        '',
    ].join('\n');

    return new Response(encoder.encode(body), {
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
    });
}

function getFetchCall() {
    const [url, init] = mockFetch.mock.calls[0] as [string, RequestInit];
    return {
        url: String(url),
        body: JSON.parse(String(init.body ?? '{}')) as Record<string, unknown>,
        headers: init.headers as Record<string, string> | undefined,
    };
}

function getRawFetchCall() {
    const [url, init] = mockFetch.mock.calls[0] as [string, RequestInit];
    return {
        url: String(url),
        init,
    };
}

describe('POST /v1/images/generations', () => {
    let app: Hono;
    let authedApp: Hono;

    beforeAll(async () => {
        const { createApp } = await import('../src/server.js');
        app = createApp(createTestConfig());
        authedApp = createApp(createTestConfig(AUTH_TOKEN));
    });

    beforeEach(() => {
        mockFetch.mockReset();
        vi.unstubAllEnvs();
    });

    afterAll(() => {
        vi.restoreAllMocks();
    });

    it('requires the same /v1 auth and reaches route-level validation with a valid bearer token', async () => {
        const missing = await authedApp.request('/v1/images/generations', postJson({}));

        expect(missing.status).toBe(401);
        expect(await missing.json()).toEqual(UNAUTHORIZED);

        const authorized = await authedApp.request('/v1/images/generations', postJson({}, AUTH_TOKEN));

        expect(authorized.status).toBe(400);
        expect((await authorized.json()).error.type).toBe('invalid_request_error');
    });

    it.each([
        ['model', { prompt: 'draw a guarded gate' }],
        ['prompt', { model: 'gpt-image-2' }],
    ])('rejects missing %s with an OpenAI-style invalid_request_error', async (_field, body) => {
        const response = await app.request('/v1/images/generations', postJson(body));

        expect(response.status).toBe(400);
        expect((await response.json()).error.type).toBe('invalid_request_error');
        expect(mockFetch).not.toHaveBeenCalled();
    });

    it('rejects unsupported image models like clawroute/auto before any upstream call', async () => {
        const response = await app.request('/v1/images/generations', postJson({
            model: 'clawroute/auto',
            prompt: 'draw a guarded gate',
        }));

        expect(response.status).toBe(400);
        const body = await response.json();
        expect(body.error.type).toBe('invalid_request_error');
        expect(body.error.message).toMatch(/unsupported|image|clawroute\/auto/i);
        expect(mockFetch).not.toHaveBeenCalled();
    });

    it('rejects quality-suffixed image model aliases so callers use model plus quality', async () => {
        const response = await app.request('/v1/images/generations', postJson({
            model: 'gpt-image-2-medium',
            prompt: 'draw a guarded gate',
        }));

        expect(response.status).toBe(400);
        const body = await response.json();
        expect(body.error.type).toBe('invalid_request_error');
        expect(body.error.message).toMatch(/gpt-image-2.*quality=medium/i);
        expect(mockFetch).not.toHaveBeenCalled();
    });

    it('accepts bare gpt-image-2 and forwards to /images/generations with b64_json pass-through', async () => {
        mockFetch.mockResolvedValueOnce(createImageResponse([{ b64_json: 'ZmFrZS1pbWFnZQ==' }]));

        const response = await app.request('/v1/images/generations', postJson({
            model: 'gpt-image-2',
            prompt: 'draw a guarded gate',
            size: '1024x1024',
        }));

        expect(response.status).toBe(200);
        const body = await response.json();
        expect(body).toMatchObject({
            created: 1715430000,
            data: [{ b64_json: 'ZmFrZS1pbWFnZQ==' }],
        });

        const fetchCall = getFetchCall();
        expect(fetchCall.url).toMatch(/\/images\/generations$/);
        expect(fetchCall.url).not.toMatch(/\/chat\/completions$/);
        expect(fetchCall.body).toMatchObject({
            model: 'gpt-image-2',
            prompt: 'draw a guarded gate',
            size: '1024x1024',
        });
    });

    it('accepts openai/gpt-image-2 and preserves url fallback while normalizing the upstream model', async () => {
        mockFetch.mockResolvedValueOnce(createImageResponse([
            { url: 'https://example.test/generated/spartan-gate.png' },
        ]));

        const response = await app.request('/v1/images/generations', postJson({
            model: 'openai/gpt-image-2',
            prompt: 'draw a guarded gate',
            quality: 'high',
        }));

        expect(response.status).toBe(200);
        const body = await response.json();
        expect(body).toMatchObject({
            created: 1715430000,
            data: [{ url: 'https://example.test/generated/spartan-gate.png' }],
        });

        const fetchCall = getFetchCall();
        expect(fetchCall.url).toMatch(/\/images\/generations$/);
        expect(fetchCall.body).toMatchObject({
            model: 'gpt-image-2',
            prompt: 'draw a guarded gate',
            quality: 'high',
        });
    });

    it('falls back to Codex image generation when no OpenAI API key is configured', async () => {
        vi.stubEnv('OPENAI_CODEX_TOKEN', 'codex-access-token');
        mockFetch.mockResolvedValueOnce(createCodexImageStreamResponse('Y29kZXgtaW1hZ2U='));

        const { createApp } = await import('../src/server.js');
        const codexConfig = createTestConfig();
        codexConfig.apiKeys.openai = '';
        const codexApp = createApp(codexConfig);

        const response = await codexApp.request('/v1/images/generations', postJson({
            model: 'gpt-image-2',
            prompt: 'draw a guarded gate',
            size: '1024x1024',
            quality: 'medium',
        }));

        expect(response.status).toBe(200);
        const body = await response.json();
        expect(body).toMatchObject({
            created: expect.any(Number),
            data: [{ b64_json: 'Y29kZXgtaW1hZ2U=' }],
        });

        const fetchCall = getFetchCall();
        expect(fetchCall.url).toMatch(/\/backend-api\/codex\/responses$/);
        expect(fetchCall.url).not.toMatch(/\/images\/generations$/);
        expect(fetchCall.body).toMatchObject({
            stream: true,
            input: [
                {
                    role: 'user',
                    content: 'draw a guarded gate',
                },
            ],
            tools: [
                {
                    type: 'image_generation',
                    model: 'gpt-image-2',
                    size: '1024x1024',
                    quality: 'medium',
                },
            ],
        });
    });
});

describe('POST /v1/images/edits', () => {
    let app: Hono;
    let authedApp: Hono;

    beforeAll(async () => {
        const { createApp } = await import('../src/server.js');
        app = createApp(createTestConfig());
        authedApp = createApp(createTestConfig(AUTH_TOKEN));
    });

    beforeEach(() => {
        mockFetch.mockReset();
        vi.unstubAllEnvs();
    });

    afterAll(() => {
        vi.restoreAllMocks();
    });

    it('requires the same /v1 auth and reaches route-level validation with a valid bearer token', async () => {
        const missing = await authedApp.request('/v1/images/edits', postMultipart((form) => {
            form.append('model', 'gpt-image-2');
            form.append('prompt', 'make it blue');
            form.append('image', createPngFile('red.png'));
        }));

        expect(missing.status).toBe(401);
        expect(await missing.json()).toEqual(UNAUTHORIZED);

        const authorized = await authedApp.request('/v1/images/edits', postMultipart(() => {}, AUTH_TOKEN));

        expect(authorized.status).toBe(400);
        expect((await authorized.json()).error.type).toBe('invalid_request_error');
    });

    it.each([
        ['model', (form: FormData) => {
            form.append('prompt', 'make it blue');
            form.append('image', createPngFile('red.png'));
        }],
        ['prompt', (form: FormData) => {
            form.append('model', 'gpt-image-2');
            form.append('image', createPngFile('red.png'));
        }],
        ['image', (form: FormData) => {
            form.append('model', 'gpt-image-2');
            form.append('prompt', 'make it blue');
        }],
    ])('rejects missing %s with an OpenAI-style invalid_request_error', async (_field, build) => {
        const response = await app.request('/v1/images/edits', postMultipart(build));

        expect(response.status).toBe(400);
        expect((await response.json()).error.type).toBe('invalid_request_error');
        expect(mockFetch).not.toHaveBeenCalled();
    });

    it('rejects gpt-image-2-medium on edits instead of aliasing model and quality', async () => {
        const response = await app.request('/v1/images/edits', postMultipart((form) => {
            form.append('model', 'gpt-image-2-medium');
            form.append('prompt', 'make it blue');
            form.append('image', createPngFile('red.png'));
        }));

        expect(response.status).toBe(400);
        const body = await response.json();
        expect(body.error.type).toBe('invalid_request_error');
        expect(body.error.message).toMatch(/quality=medium/i);
        expect(mockFetch).not.toHaveBeenCalled();
    });

    it('forwards OpenAI-key image edits as multipart image[] and omits compatibility-only fields', async () => {
        mockFetch.mockResolvedValueOnce(createImageResponse([
            { b64_json: 'ZWRpdGVkLWltYWdl', revised_prompt: 'Make the square blue.' },
        ]));

        const response = await app.request('/v1/images/edits', postMultipart((form) => {
            form.append('model', 'openai/gpt-image-2');
            form.append('prompt', 'make the red square blue');
            form.append('image', createPngFile('red-1.png'));
            form.append('image', createPngFile('red-2.png', [1, 2, 3]));
            form.append('image[]', createPngFile('red-3.png', [4, 5, 6]));
            form.append('mask', createPngFile('mask.png'));
            form.append('size', '1024x1024');
            form.append('quality', 'medium');
            form.append('response_format', 'b64_json');
            form.append('input_fidelity', 'high');
            form.append('steroids_unknown_probe_param', 'true');
        }));

        expect(response.status).toBe(200);
        expect(await response.json()).toMatchObject({
            data: [{ b64_json: 'ZWRpdGVkLWltYWdl', revised_prompt: 'Make the square blue.' }],
        });

        const fetchCall = getRawFetchCall();
        expect(fetchCall.url).toMatch(/\/images\/edits$/);
        const body = fetchCall.init.body;
        expect(body).toBeInstanceOf(FormData);
        const form = body as FormData;
        expect(form.get('model')).toBe('gpt-image-2');
        expect(form.get('prompt')).toBe('make the red square blue');
        expect(form.get('size')).toBe('1024x1024');
        expect(form.get('quality')).toBe('medium');
        expect(form.get('mask')).toBeInstanceOf(File);
        expect(form.getAll('image[]')).toHaveLength(3);
        expect(form.has('response_format')).toBe(false);
        expect(form.has('input_fidelity')).toBe(false);
        expect(form.has('steroids_unknown_probe_param')).toBe(false);
    });

    it('translates Codex-auth image edits into a Responses image edit request', async () => {
        vi.stubEnv('OPENAI_CODEX_TOKEN', 'codex-access-token');
        mockFetch.mockResolvedValueOnce(createCodexImageStreamResponse('Y29kZXgtZWRpdA=='));

        const { createApp } = await import('../src/server.js');
        const codexConfig = createTestConfig();
        codexConfig.apiKeys.openai = '';
        const codexApp = createApp(codexConfig);

        const response = await codexApp.request('/v1/images/edits', postMultipart((form) => {
            form.append('model', 'gpt-image-2');
            form.append('prompt', 'make the red square blue');
            form.append('image', createPngFile('red.png'));
            form.append('size', '1024x1024');
            form.append('quality', 'medium');
            form.append('response_format', 'b64_json');
            form.append('input_fidelity', 'high');
        }));

        expect(response.status).toBe(200);
        expect(await response.json()).toMatchObject({
            created: expect.any(Number),
            data: [{ b64_json: 'Y29kZXgtZWRpdA==' }],
        });

        const fetchCall = getFetchCall();
        expect(fetchCall.url).toMatch(/\/backend-api\/codex\/responses$/);
        expect(fetchCall.body).toMatchObject({
            stream: true,
            input: [
                {
                    role: 'user',
                    content: [
                        { type: 'input_text', text: 'make the red square blue' },
                        { type: 'input_image' },
                    ],
                },
            ],
            tools: [
                {
                    type: 'image_generation',
                    model: 'gpt-image-2',
                    action: 'edit',
                    size: '1024x1024',
                    quality: 'medium',
                },
            ],
        });
        const content = (fetchCall.body.input as Array<{ content: Array<Record<string, string>> }>)[0]?.content;
        expect(content?.[1]?.image_url).toMatch(/^data:image\/png;base64,/);
        expect(JSON.stringify(fetchCall.body)).not.toContain('input_fidelity');
        expect(JSON.stringify(fetchCall.body)).not.toContain('response_format');
    });

    it('returns a clear error for Codex-auth mask edits instead of silently ignoring the mask', async () => {
        vi.stubEnv('OPENAI_CODEX_TOKEN', 'codex-access-token');

        const { createApp } = await import('../src/server.js');
        const codexConfig = createTestConfig();
        codexConfig.apiKeys.openai = '';
        const codexApp = createApp(codexConfig);

        const response = await codexApp.request('/v1/images/edits', postMultipart((form) => {
            form.append('model', 'gpt-image-2');
            form.append('prompt', 'make the red square blue');
            form.append('image', createPngFile('red.png'));
            form.append('mask', createPngFile('mask.png'));
        }));

        expect(response.status).toBe(400);
        const body = await response.json();
        expect(body.error.code).toBe('unsupported_endpoint');
        expect(body.error.message).toMatch(/mask.*OPENAI_API_KEY/i);
        expect(mockFetch).not.toHaveBeenCalled();
    });
});
