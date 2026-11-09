"""Run the real native Desktop slash hook/router with a synthetic gateway transport.

Requires an explicitly prepared native headless workspace, never installs a
browser or Electron. See .agents/validation.md for the frozen preparation route.
"""
import os
from pathlib import Path
import subprocess
import unittest

WORKSPACE = os.environ.get('HERMES_DESKTOP_WORKSPACE')
TEST_PATH = 'apps/desktop/src/app/session/hooks/use-prompt-actions/spartan-branch-navigation.test.ts'
NATIVE_TEST = r'''import { act, cleanup, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const sent: { method: string; params: Record<string, unknown>; url: string }[] = []
let rejectNavigation = false
let complete: (() => void) | undefined
let hold = false
class FakeHermesGateway {
  connectionState = 'closed'
  private url = ''
  async connect(url: string) { this.url = url; this.connectionState = 'open' }
  async request<T>(method: string, params: Record<string, unknown> = {}): Promise<T> {
    sent.push({ method, params, url: this.url })
    if (method === 'session.branch_return' || method === 'session.branch_cancel') {
      if (rejectNavigation) throw new Error('summary failed')
      if (hold) await new Promise<void>(resolve => { complete = resolve })
      return { status: method.endsWith('cancel') ? 'cancelled' : 'completed', parent_session_id: 'stored-parent' } as T
    }
    return {} as T
  }
  close() { this.connectionState = 'closed' }
  onEvent() { return () => undefined }
  onState() { return () => undefined }
  onStateChange() { return () => undefined }
  on() {} off() {} addEventListener() {} removeEventListener() {}
}
vi.mock('@/hermes', async original => ({
  ...(await original<Record<string, unknown>>()), HermesGateway: FakeHermesGateway, setApiRequestConnection: vi.fn()
}))
const { en } = await import('@/i18n/en')
const { createClientSessionState } = await import('@/lib/chat-runtime')
const { setConnection, setSessionOwnerHint, getSessionOwnerHint, _resetSessionOwnerHintsForTests } = await import('@/store/session')
const { useSlashCommand } = await import('./slash')
const { resolveDesktopCommand } = await import('@/lib/desktop-slash-commands')
const owner = { connectionId: 'branch-owner', profile: 'alpha' }

beforeEach(() => {
  sent.length = 0
  rejectNavigation = false
  hold = false
  complete = undefined
  _resetSessionOwnerHintsForTests()
  setConnection({ mode: 'remote', connectionId: 'unrelated', profile: 'default', baseUrl: 'http://unrelated' } as never)
  setSessionOwnerHint('stored-child', owner)
  ;(window as unknown as { hermesDesktop: unknown }).hermesDesktop = {
    getConnection: async () => ({ mode: 'local' }),
    getConnectionFor: async ({ connectionId, profile }: { connectionId: string; profile: string }) => ({ connectionId, mode: 'remote', profile }),
    getGatewayWsUrlFor: async ({ connectionId, profile }: { connectionId: string; profile: string }) => `ws://${connectionId}/gateway?profile=${profile}`
  }
})
afterEach(cleanup)

function fixture() {
  const resume = vi.fn()
  const append = vi.fn()
  const ambient = vi.fn(async () => ({}))
  const active = { current: 'runtime-child' as string | null }
  const selected = { current: 'stored-child' as string | null }
  const { result } = renderHook(() => useSlashCommand({
    activeSessionIdRef: active, selectedStoredSessionIdRef: selected, busyRef: { current: false }, copy: en.desktop,
    createBackendSessionForSend: async () => null, getRoutedStoredSessionId: () => 'stored-child',
    getRuntimeIdForStoredSession: () => 'runtime-child', requestGateway: ambient as never,
    resumeStoredSession: resume, updateSessionState: vi.fn((_sid, update) => update(createClientSessionState())),
    submitPromptText: vi.fn(async () => true), appendSessionTextMessage: append, branchCurrentSession: async () => true,
    handleSkinCommand: () => '', handoffSession: async () => ({ ok: true }), openMemoryGraph: vi.fn(),
    refreshSessions: async () => undefined, startFreshSessionDraft: vi.fn()
  }))
  return { result, resume, append, ambient, active }
}

it.each(['/return', '/cancel'])('routes %s to the child owner and resumes the immediate stored parent only on success', async command => {
  const f = fixture()
  expect(resolveDesktopCommand(command)?.surface).toMatchObject({ kind: 'action' })
  await act(async () => { await f.result.current(command) })
  const navigation = sent.filter(r => r.method === (command === '/return' ? 'session.branch_return' : 'session.branch_cancel'))
  expect(navigation).toHaveLength(1)
  expect(navigation[0].url).toContain('branch-owner')
  expect(navigation[0].url).toContain('alpha')
  expect(navigation[0].params.session_id).toBe('runtime-child')
  expect(f.resume).toHaveBeenCalledWith('stored-parent')
  expect(getSessionOwnerHint('stored-parent')).toEqual(owner)
  expect(f.ambient).not.toHaveBeenCalledWith('slash.exec', expect.anything())
  expect(sent.some(r => ['slash.exec', 'command.dispatch', 'prompt.submit'].includes(r.method))).toBe(false)
})

it('keeps failure in the child and does not fall back to generic execution', async () => {
  rejectNavigation = true
  const f = fixture()
  await act(async () => { await f.result.current('/return') })
  expect(f.resume).not.toHaveBeenCalled()
  expect(f.append).toHaveBeenCalledWith('runtime-child', 'system', expect.stringContaining('summary failed'), 'stored-child')
  expect(sent.filter(r => r.method.startsWith('session.branch_'))).toHaveLength(1)
  expect(sent.some(r => ['slash.exec', 'command.dispatch', 'prompt.submit'].includes(r.method))).toBe(false)
})

it('does not steal the current view when the user switches while the return is pending', async () => {
  hold = true
  const f = fixture()
  let pending: Promise<boolean>
  await act(async () => { pending = f.result.current('/return'); await new Promise(resolve => setTimeout(resolve, 10)) })
  expect(complete).toBeTypeOf('function')
  f.active.current = 'different-view'
  await act(async () => { complete!(); await pending! })
  expect(f.resume).not.toHaveBeenCalled()
})
'''


class DesktopRendererTests(unittest.TestCase):
    @unittest.skipUnless(WORKSPACE, 'set HERMES_DESKTOP_WORKSPACE to native frozen headless workspace')
    def test_native_slash_owner_navigation(self):
        root = Path(WORKSPACE)
        target = root / TEST_PATH
        if not target.parent.is_dir():
            self.fail('Native Desktop source is missing from the selected workspace')
        target.write_text(NATIVE_TEST)
        result = subprocess.run([os.environ.get('HERMES_DESKTOP_NODE', 'node'), str(root / 'apps/desktop/node_modules/vitest/vitest.mjs'), 'run', '--project', 'ui',
                                 'src/app/session/hooks/use-prompt-actions/spartan-branch-navigation.test.ts',
                                 'src/app/session/hooks/branch-owner-routing.integration.test.ts',
                                 'src/api/canonical-protocol.test.ts'],
                                cwd=root / 'apps/desktop', capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        print(result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
