import { describe, expect, it } from 'vitest';
import { readFileSync } from 'fs';
import { join } from 'path';

function fmtMtokensFromDashboard(value: number): string {
    const html = readFileSync(join(process.cwd(), 'web', 'dashboard-codex.html'), 'utf8');
    const match = html.match(/function fmtMtokens\(value\) \{[\s\S]*?\n        \}/);
    if (!match) throw new Error('fmtMtokens function not found');
    return new Function('value', `${match[0]}; return fmtMtokens(value);`)(value) as string;
}

describe('Codex dashboard token formatting', () => {
    it('formats sub-million token values as thousands', () => {
        expect(fmtMtokensFromDashboard(154_020)).toBe('154k');
        expect(fmtMtokensFromDashboard(9_500)).toBe('9.5k');
    });

    it('keeps million formatting for million-scale values', () => {
        expect(fmtMtokensFromDashboard(1_540_200)).toBe('1.54M');
    });
});
