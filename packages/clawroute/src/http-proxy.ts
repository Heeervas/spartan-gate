import { fetch as undiciFetch, ProxyAgent } from 'undici';

export type FetchInitWithDispatcher = Omit<RequestInit, 'dispatcher'> & {
    dispatcher?: unknown;
};

let proxyAgent: ProxyAgent | null = null;
let proxyUrl = '';

export function getProxyAgent(): ProxyAgent | null {
    const nextUrl = process.env.HTTPS_PROXY ?? process.env.https_proxy ?? '';
    if (!nextUrl) return null;
    if (!proxyAgent || proxyUrl !== nextUrl) {
        proxyUrl = nextUrl;
        proxyAgent = new ProxyAgent(nextUrl);
    }
    return proxyAgent;
}

export async function fetchWithProxyAgent(input: string | URL, init: FetchInitWithDispatcher): Promise<Response> {
    return await undiciFetch(input, init as Parameters<typeof undiciFetch>[1]) as unknown as Response;
}
