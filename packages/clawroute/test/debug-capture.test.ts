import { afterEach, describe, expect, it, vi } from 'vitest';
import { mkdtempSync, readdirSync, readFileSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { TaskTier } from '../src/types.js';

let tempDirs: string[] = [];

function createTempDir(): string {
    const dir = mkdtempSync(join(tmpdir(), 'clawroute-debug-capture-'));
    tempDirs.push(dir);
    return dir;
}

function baseCaptureInput() {
    return {
        requestId: 'cr_test',
        requestApiKind: 'chat_completions' as const,
        body: {
            model: 'codex/gpt-5.5',
            messages: [
                { role: 'user' as const, content: 'private prompt for debug capture' },
            ],
            prompt_cache_key: 'hermes:session',
        },
        routing: {
            originalModel: 'codex/gpt-5.5',
            routedModel: 'codex/gpt-5.5',
            tier: TaskTier.SIMPLE,
            reason: 'test',
            confidence: 1,
            isDryRun: false,
            isOverride: false,
            isPassthrough: false,
            estimatedSavingsUsd: 0,
            safeToRetry: true,
        },
        classification: { tier: TaskTier.SIMPLE, confidence: 1, reason: 'test' },
        result: {
            response: new Response('{}', { status: 200 }),
            routingDecision: {} as never,
            actualModel: 'codex/gpt-5.5',
            escalated: false,
            escalationChain: ['codex/gpt-5.5'],
            inputTokens: 80_000,
            outputTokens: 100,
            cachedInputTokens: 0,
            originalCostUsd: 0,
            actualCostUsd: 0,
            savingsUsd: 0,
            responseTimeMs: 1234,
            hadToolCalls: true,
            selectedCodexSlotIndex: 2,
            selectedCodexAccountKey: 'acct',
        },
        session: { id: 'session-id', source: 'prompt_cache_key' as const },
        promptCacheKey: 'hermes:session',
        promptCacheKeyHash: 'hash123',
        contextInfo: JSON.stringify({
            cache_key_present: true,
            cache_key_hash: 'hash123',
            same_session_cache_trace: { comparison: 'prefix' },
        }),
    };
}

afterEach(() => {
    vi.unstubAllEnvs();
    vi.resetModules();
    for (const dir of tempDirs) {
        rmSync(dir, { recursive: true, force: true });
    }
    tempDirs = [];
});

describe('debug raw request capture', () => {
    it('does not write captures unless explicitly enabled', async () => {
        const dir = createTempDir();
        vi.stubEnv('CLAWROUTE_DEBUG_CAPTURE_DIR', dir);
        const { writeDebugRequestCapture } = await import('../src/debug-capture.js');

        writeDebugRequestCapture(baseCaptureInput());

        expect(readdirSync(dir)).toEqual([]);
    });

    it('writes raw request and cache metadata when enabled', async () => {
        const dir = createTempDir();
        vi.stubEnv('CLAWROUTE_DEBUG_CAPTURE_RAW', '1');
        vi.stubEnv('CLAWROUTE_DEBUG_CAPTURE_DIR', dir);
        vi.stubEnv('CLAWROUTE_DEBUG_CAPTURE_UNTIL', new Date(Date.now() + 60_000).toISOString());
        const { writeDebugRequestCapture } = await import('../src/debug-capture.js');

        writeDebugRequestCapture(baseCaptureInput());

        const files = readdirSync(dir);
        expect(files).toHaveLength(1);
        const row = JSON.parse(readFileSync(join(dir, files[0]), 'utf-8'));
        expect(row.request_id).toBe('cr_test');
        expect(row.prompt_cache_key_present).toBe(true);
        expect(row.prompt_cache_key_hash).toBe('hash123');
        expect(row.result.cached_input_tokens).toBe(0);
        expect(row.context_info.same_session_cache_trace.comparison).toBe('prefix');
        expect(row.raw_request.messages[0].content).toBe('private prompt for debug capture');
    });

    it('stops writing after the configured until time', async () => {
        const dir = createTempDir();
        vi.stubEnv('CLAWROUTE_DEBUG_CAPTURE_RAW', '1');
        vi.stubEnv('CLAWROUTE_DEBUG_CAPTURE_DIR', dir);
        vi.stubEnv('CLAWROUTE_DEBUG_CAPTURE_UNTIL', new Date(Date.now() - 60_000).toISOString());
        const { writeDebugRequestCapture } = await import('../src/debug-capture.js');

        writeDebugRequestCapture(baseCaptureInput());

        expect(readdirSync(dir)).toEqual([]);
    });

    it('writes final Codex request bodies to a separate capture file', async () => {
        const dir = createTempDir();
        vi.stubEnv('CLAWROUTE_DEBUG_CAPTURE_RAW', '1');
        vi.stubEnv('CLAWROUTE_DEBUG_CAPTURE_DIR', dir);
        vi.stubEnv('CLAWROUTE_DEBUG_CAPTURE_UNTIL', new Date(Date.now() + 60_000).toISOString());
        const { writeDebugCodexFinalRequestCapture } = await import('../src/debug-capture.js');

        writeDebugCodexFinalRequestCapture({
            requestId: 'cr_final',
            sessionId: 'session-id',
            promptCacheKeyHash: 'hash123',
            modelId: 'codex/gpt-5.5',
            modelName: 'gpt-5.5',
            slotIndex: 3,
            accountKey: 'acct',
            attempt: 2,
            responseStatus: 502,
            responseBodyExcerpt: '{"error":"upstream"}',
            estimatedInputTokens: 50_000,
            body: {
                model: 'gpt-5.5',
                input: [{ role: 'user', content: 'translated final body' }],
                prompt_cache_key: 'hermes:session',
                tools: [{ type: 'function', name: 'todo' }],
            },
        });

        const files = readdirSync(dir);
        expect(files).toHaveLength(1);
        expect(files[0]).toContain('codex-final-requests-');
        const row = JSON.parse(readFileSync(join(dir, files[0]), 'utf-8'));
        expect(row.request_id).toBe('cr_final');
        expect(row.slot_index).toBe(3);
        expect(row.response_status).toBe(502);
        expect(row.response_body_excerpt).toBe('{"error":"upstream"}');
        expect(row.final_request.input[0].content).toBe('translated final body');
        expect(row.final_request.prompt_cache_key).toBe('hermes:session');
    });
});
