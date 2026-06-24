import { getApiKey } from './config.js';
import { makeCodexImageRequest } from './codex-transport.js';
import { FetchInitWithDispatcher, getProxyAgent } from './http-proxy.js';
import { getApiBaseUrl, getAuthHeader, getModelEntryFromCatalog } from './models.js';
import { ClawRouteConfig, ImageEditRequest, ImageGenerationRequest, ModelEntry } from './types.js';

const SUPPORTED_IMAGE_MODELS: Record<string, string> = {
    'gpt-image-2': 'openai/gpt-image-2',
    'openai/gpt-image-2': 'openai/gpt-image-2',
};

const OPTIONAL_IMAGE_FIELDS: Array<keyof ImageGenerationRequest> = [
    'size',
    'quality',
    'n',
    'background',
    'user',
];

const OPTIONAL_IMAGE_EDIT_FIELDS: Array<keyof ImageEditRequest> = [
    'size',
    'quality',
    'n',
    'background',
    'moderation',
    'output_format',
    'output_compression',
    'user',
];

type MultipartValue = string | File | Array<string | File>;
type MultipartBody = Record<string, MultipartValue | undefined>;

function createJsonResponse(body: Record<string, unknown>, status: number): Response {
    return new Response(JSON.stringify(body), {
        status,
        headers: {
            'Content-Type': 'application/json',
        },
    });
}

function createInvalidRequestResponse(message: string): Response {
    return createJsonResponse({
        error: {
            message,
            type: 'invalid_request_error',
        },
    }, 400);
}

function createServerErrorResponse(message: string): Response {
    return createJsonResponse({
        error: {
            message,
            type: 'server_error',
            code: 'internal_error',
        },
    }, 500);
}

function createUnsupportedResponse(message: string): Response {
    return createJsonResponse({
        error: {
            message,
            type: 'invalid_request_error',
            code: 'unsupported_endpoint',
        },
    }, 400);
}

function extractModelName(modelId: string): string {
    if (modelId.includes('/')) {
        return modelId.split('/').slice(1).join('/');
    }
    return modelId;
}

function resolveImageModel(modelId: string, modelCatalog: ModelEntry[]): string | null {
    const normalizedModelId = SUPPORTED_IMAGE_MODELS[modelId];
    if (!normalizedModelId) {
        return null;
    }

    const modelEntry = getModelEntryFromCatalog(normalizedModelId, modelCatalog);
    if (!modelEntry || !modelEntry.enabled || modelEntry.provider !== 'openai') {
        return null;
    }

    return normalizedModelId;
}

function buildUpstreamRequest(body: ImageGenerationRequest, upstreamModel: string): Record<string, unknown> {
    const upstreamBody: Record<string, unknown> = {
        model: upstreamModel,
        prompt: body.prompt,
    };

    for (const field of OPTIONAL_IMAGE_FIELDS) {
        if (body[field] !== undefined) {
            upstreamBody[field] = body[field];
        }
    }

    return upstreamBody;
}

function asArray(value: MultipartValue | undefined): Array<string | File> {
    if (value === undefined) {
        return [];
    }
    return Array.isArray(value) ? value : [value];
}

function isFile(value: unknown): value is File {
    return typeof File !== 'undefined' && value instanceof File;
}

function getFirstString(body: MultipartBody, key: string): string | undefined {
    const first = asArray(body[key]).find((value) => typeof value === 'string');
    return typeof first === 'string' ? first : undefined;
}

function getOptionalField(body: MultipartBody, key: keyof ImageEditRequest): string | undefined {
    const value = getFirstString(body, String(key));
    return value !== undefined && value.length > 0 ? value : undefined;
}

function getFirstFile(body: MultipartBody, key: string): File | undefined {
    return asArray(body[key]).find(isFile);
}

function collectImageFiles(body: MultipartBody): ImageEditRequest['images'] {
    return [
        ...asArray(body['image']).map((value) => ({ fieldName: 'image', value })),
        ...asArray(body['image[]']).map((value) => ({ fieldName: 'image[]', value })),
    ]
        .filter((entry): entry is { fieldName: string; value: File } => isFile(entry.value))
        .map((entry) => ({ fieldName: entry.fieldName, file: entry.value }));
}

function appendOptionalFormFields(form: FormData, request: ImageEditRequest): void {
    for (const field of OPTIONAL_IMAGE_EDIT_FIELDS) {
        const value = request[field];
        if (value !== undefined) {
            form.append(String(field), String(value));
        }
    }
}

function buildUpstreamEditForm(request: ImageEditRequest, upstreamModel: string): FormData {
    const form = new FormData();
    form.append('model', upstreamModel);
    form.append('prompt', request.prompt);

    for (const image of request.images) {
        form.append('image[]', image.file, image.file.name || 'image.png');
    }

    if (request.mask) {
        form.append('mask', request.mask, request.mask.name || 'mask.png');
    }

    appendOptionalFormFields(form, request);
    return form;
}

function validateRequest(body: unknown, modelCatalog: ModelEntry[]):
    | { request: ImageGenerationRequest; normalizedModelId: string }
    | { error: Response } {
    if (!body || typeof body !== 'object' || Array.isArray(body)) {
        return { error: createInvalidRequestResponse('Request body must be a JSON object') };
    }

    const request = body as Partial<ImageGenerationRequest>;

    if (typeof request.model !== 'string' || request.model.trim().length === 0) {
        return { error: createInvalidRequestResponse('model is required') };
    }

    if (typeof request.prompt !== 'string' || request.prompt.trim().length === 0) {
        return { error: createInvalidRequestResponse('prompt is required') };
    }

    const normalizedModelId = resolveImageModel(request.model, modelCatalog);
    if (!normalizedModelId) {
        return {
            error: createInvalidRequestResponse(
                `Unsupported image model: ${request.model}. Supported models: gpt-image-2, openai/gpt-image-2. Use model gpt-image-2 with quality=medium instead of gpt-image-2-medium.`
            ),
        };
    }

    return {
        request: {
            ...request,
            model: request.model,
            prompt: request.prompt,
        } as ImageGenerationRequest,
        normalizedModelId,
    };
}

function validateEditRequest(body: MultipartBody, modelCatalog: ModelEntry[]):
    | { request: ImageEditRequest; normalizedModelId: string }
    | { error: Response } {
    const model = getFirstString(body, 'model')?.trim();
    if (!model) {
        return { error: createInvalidRequestResponse('model is required') };
    }

    const prompt = getFirstString(body, 'prompt')?.trim();
    if (!prompt) {
        return { error: createInvalidRequestResponse('prompt is required') };
    }

    const normalizedModelId = resolveImageModel(model, modelCatalog);
    if (!normalizedModelId) {
        return {
            error: createInvalidRequestResponse(
                `Unsupported image model: ${model}. Supported models: gpt-image-2, openai/gpt-image-2. Use model gpt-image-2 with quality=medium instead of gpt-image-2-medium.`
            ),
        };
    }

    const images = collectImageFiles(body);
    if (images.length === 0) {
        return { error: createInvalidRequestResponse('image is required') };
    }

    if (images.length > 16) {
        return { error: createInvalidRequestResponse('image supports at most 16 files') };
    }

    const request: ImageEditRequest = {
        model,
        prompt,
        images,
    };

    const mask = getFirstFile(body, 'mask');
    if (mask) {
        request.mask = mask;
    }

    for (const field of OPTIONAL_IMAGE_EDIT_FIELDS) {
        const value = getOptionalField(body, field);
        if (value !== undefined) {
            request[field] = value;
        }
    }

    return {
        request,
        normalizedModelId,
    };
}

export async function executeImageGeneration(
    body: unknown,
    config: ClawRouteConfig,
    modelCatalog: ModelEntry[]
): Promise<Response> {
    const validation = validateRequest(body, modelCatalog);
    if ('error' in validation) {
        return validation.error;
    }

    const { request, normalizedModelId } = validation;
    const apiKey = getApiKey(config, 'openai');
    if (!apiKey) {
        return makeCodexImageRequest(request, getProxyAgent());
    }

    const upstreamBody = buildUpstreamRequest(request, extractModelName(normalizedModelId));

    if (config.logging.debugMode) {
        console.log(
            `[images] forwarding image generation ${request.model} -> ${String(upstreamBody.model)}`
        );
    }

    const fetchOptions: FetchInitWithDispatcher = {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            ...getAuthHeader('openai', apiKey),
        },
        body: JSON.stringify(upstreamBody),
    };

    const proxyAgent = getProxyAgent();
    if (proxyAgent) {
        fetchOptions.dispatcher = proxyAgent;
    }

    try {
        return await fetch(`${getApiBaseUrl('openai')}/images/generations`, fetchOptions as RequestInit);
    } catch (error) {
        const message = error instanceof Error ? error.message : 'Failed to generate image';
        return createServerErrorResponse(message);
    }
}

export async function executeImageEdit(
    body: MultipartBody,
    config: ClawRouteConfig,
    modelCatalog: ModelEntry[]
): Promise<Response> {
    const validation = validateEditRequest(body, modelCatalog);
    if ('error' in validation) {
        return validation.error;
    }

    const { request, normalizedModelId } = validation;
    const apiKey = getApiKey(config, 'openai');
    if (!apiKey) {
        if (request.mask) {
            return createUnsupportedResponse(
                'mask is not supported for Codex-auth image edits; configure OPENAI_API_KEY to use /v1/images/edits with mask'
            );
        }
        return makeCodexImageRequest(request, getProxyAgent());
    }

    const upstreamBody = buildUpstreamEditForm(request, extractModelName(normalizedModelId));

    if (config.logging.debugMode) {
        console.log(
            `[images] forwarding image edit ${request.model} -> ${extractModelName(normalizedModelId)}`
        );
    }

    const fetchOptions: FetchInitWithDispatcher = {
        method: 'POST',
        headers: {
            ...getAuthHeader('openai', apiKey),
        },
        body: upstreamBody,
    };

    const proxyAgent = getProxyAgent();
    if (proxyAgent) {
        fetchOptions.dispatcher = proxyAgent;
    }

    try {
        return await fetch(`${getApiBaseUrl('openai')}/images/edits`, fetchOptions as RequestInit);
    } catch (error) {
        const message = error instanceof Error ? error.message : 'Failed to edit image';
        return createServerErrorResponse(message);
    }
}
