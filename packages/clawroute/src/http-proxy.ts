import { ProxyAgent } from 'undici';

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
