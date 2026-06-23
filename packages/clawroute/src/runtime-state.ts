import { buildRoutingSnapshot } from './config.js';
import { RoutingSnapshot } from './types.js';
import { existsSync, statSync } from 'node:fs';
import { join } from 'node:path';

interface RuntimeStateOptions {
    projectRoot: string;
    pollIntervalMs?: number;
    buildSnapshot?: (projectRoot: string) => RoutingSnapshot;
}

export interface RuntimeStateManager {
    getSnapshot(): RoutingSnapshot;
    reloadNow(reason?: string): Promise<RoutingSnapshot>;
    stop(): void;
}

type FileSignature = {
    exists: boolean;
    size: number;
    mtimeMs: number;
};

type ConfigSignature = Record<string, FileSignature>;

function startPolling(options: RuntimeStateOptions, tick: () => void): NodeJS.Timeout | null {
    if (!options.pollIntervalMs || options.pollIntervalMs < 1) {
        return null;
    }

    return setInterval(tick, options.pollIntervalMs);
}

function readFileSignature(path: string): FileSignature {
    if (!existsSync(path)) {
        return { exists: false, size: 0, mtimeMs: 0 };
    }

    const stat = statSync(path);
    return { exists: true, size: stat.size, mtimeMs: stat.mtimeMs };
}

function getWatchedConfigPaths(projectRoot: string, snapshot: RoutingSnapshot): string[] {
    const paths = [
        join(projectRoot, 'config', 'default.json'),
        join(projectRoot, 'config', 'clawroute.json'),
        join(projectRoot, 'config', 'model-registry.json'),
    ];
    if (snapshot.providerProfile) {
        paths.push(join(projectRoot, 'config', 'providers', `${snapshot.providerProfile}.json`));
    }
    return paths;
}

function readConfigSignature(projectRoot: string, snapshot: RoutingSnapshot): ConfigSignature {
    return Object.fromEntries(
        getWatchedConfigPaths(projectRoot, snapshot).map((path) => [path, readFileSignature(path)]),
    );
}

function signaturesEqual(left: ConfigSignature, right: ConfigSignature): boolean {
    const leftKeys = Object.keys(left).sort();
    const rightKeys = Object.keys(right).sort();
    if (leftKeys.length !== rightKeys.length) return false;

    for (let i = 0; i < leftKeys.length; i++) {
        const key = leftKeys[i]!;
        if (key !== rightKeys[i]) return false;
        const a = left[key]!;
        const b = right[key]!;
        if (a.exists !== b.exists || a.size !== b.size || a.mtimeMs !== b.mtimeMs) {
            return false;
        }
    }

    return true;
}

export function createRuntimeStateManager(
    options: RuntimeStateOptions
): RuntimeStateManager {
    const buildSnapshot = options.buildSnapshot ?? buildRoutingSnapshot;
    let snapshot = buildSnapshot(options.projectRoot);
    let signature = readConfigSignature(options.projectRoot, snapshot);

    const loadSnapshot = (): void => {
        const nextSnapshot = buildSnapshot(options.projectRoot);
        snapshot = nextSnapshot;
        signature = readConfigSignature(options.projectRoot, snapshot);
    };

    let timer = startPolling(options, () => {
        try {
            const nextSignature = readConfigSignature(options.projectRoot, snapshot);
            if (signaturesEqual(signature, nextSignature)) {
                return;
            }
            loadSnapshot();
        } catch {
            // Keep serving the last known-good snapshot.
        }
    });

    return {
        getSnapshot(): RoutingSnapshot {
            return snapshot;
        },
        async reloadNow(): Promise<RoutingSnapshot> {
            loadSnapshot();
            return snapshot;
        },
        stop(): void {
            if (!timer) return;
            clearInterval(timer);
            timer = null;
        },
    };
}
