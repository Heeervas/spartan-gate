#!/usr/bin/env node
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');

const MARKER = 'SPARTAN_VNC_READINESS_V2';
const files = {
  'plugins/vnc/index.js': '9e82f34e9b1af9c3b4f2879501aa13ded76724e11c8f90a714f7da3b7f9a1477',
  'plugins/vnc/vnc-watcher.sh': '5034e71803f4d40721af01b8b87806fc568ea07a440f67025bdf2515ac1177b0',
  'plugins/vnc/vnc-launcher.js': '79e079a0f3a4049d282fe9bd8c57b80db13a8271b83acbe8f5bdeaaec0d44f88',
};
const patchedHashes = {
  'plugins/vnc/index.js': '23d37f4c1fb94c10b8f5f17e28a4a8d8ed924e6a6525d732dd4619936f91929e',
  'plugins/vnc/vnc-watcher.sh': '444681525d784d1aa06d8465d78cdca1cc4ac470ddd5f295a3f7e5a03f08c149',
  'plugins/vnc/vnc-launcher.js': 'e701cc3cff27441ac7cdea63002504e2ab60e0764530ecb9009e21494caf1133',
};

function digest(value) { return crypto.createHash('sha256').update(value).digest('hex'); }
function replaceOnce(source, anchor, replacement, label) {
  const count = source.split(anchor).length - 1;
  if (count !== 1) throw new Error(`expected one ${label} anchor, found ${count}`);
  return source.replace(anchor, replacement);
}

const original = Object.fromEntries(Object.keys(files).map(file => [file, fs.readFileSync(file, 'utf8')]));
const marked = Object.values(original).filter(source => source.includes(MARKER)).length;
if (marked) {
  if (marked !== Object.keys(files).length) throw new Error('mixed Spartan VNC readiness patch state');
  for (const [file, source] of Object.entries(original)) {
    if (digest(source) !== patchedHashes[file]) throw new Error(`unexpected patched VNC source hash: ${file}`);
  }
  process.exit(0);
}
for (const [file, source] of Object.entries(original)) {
  if (digest(source) !== files[file]) throw new Error(`unexpected pristine VNC source hash: ${file}`);
}

let index = original['plugins/vnc/index.js'];
const startBlock = `  const watcher = startWatcher({
    resolution: vncConfig.resolution,
    vncPassword: vncConfig.vncPassword,
    viewOnly: vncConfig.viewOnly,
    vncPort: vncConfig.vncPort,
    novncPort: vncConfig.novncPort,
    log,
    events,
  });

  // Clean up watcher on server shutdown
  events.on('server:shutdown', () => {
    if (watcher.exitCode === null) {
      log('info', 'killing vnc watcher on shutdown');
      watcher.kill('SIGTERM');
    }
  });`;
const gatedBlock = `  // ${MARKER}: bind transports to one accepted launch identity.
  let watcher = null;
  let watcherIdentity = null;
  let stopWatcherPromise = null;
  function startAcceptedWatcher(identity) {
    for (const field of ['launchId', 'generation', 'serverPid', 'browserPid', 'browserStartTime', 'xvfbPid', 'xvfbStartTime', 'display']) {
      if (identity?.[field] === undefined || identity?.[field] === null || identity?.[field] === '') {
        throw new Error('missing accepted VNC identity field: ' + field);
      }
    }
    if (watcher?.exitCode === null) {
      if (watcherIdentity?.launchId === identity.launchId) return;
      throw new Error('refusing to replace a live VNC watcher');
    }
    watcherIdentity = identity;
    watcher = startWatcher({
      resolution: vncConfig.resolution,
      vncPassword: vncConfig.vncPassword,
      viewOnly: vncConfig.viewOnly,
      vncPort: vncConfig.vncPort,
      novncPort: vncConfig.novncPort,
      identity,
      log,
      events,
    });
  }
  async function stopAcceptedWatcher() {
    if (stopWatcherPromise) return stopWatcherPromise;
    const active = watcher;
    if (!active || active.exitCode !== null) {
      watcher = null;
      watcherIdentity = null;
      return;
    }
    stopWatcherPromise = new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('vnc watcher teardown timeout')), 5000);
      active.once('exit', () => { clearTimeout(timer); resolve(); });
      active.kill('SIGTERM');
    }).then(() => {
      if (watcher === active) {
        watcher = null;
        watcherIdentity = null;
      }
    }).finally(() => { stopWatcherPromise = null; });
    return stopWatcherPromise;
  }
  events.on('browser:accepted', startAcceptedWatcher);
  events.on('browser:closing', stopAcceptedWatcher);
  events.on('server:shutdown', stopAcceptedWatcher);`;
index = replaceOnce(index, startBlock, gatedBlock, 'VNC watcher lifecycle');
index = replaceOnce(index,
  '    const watcherRunning = watcher.exitCode === null && !watcher.killed;',
  '    const watcherRunning = Boolean(watcher && watcher.exitCode === null && !watcher.killed);',
  'VNC status watcher');
index = replaceOnce(index,
  '    const vncStatus = watcher.getVncStatus();',
  '    const vncStatus = watcher?.getVncStatus?.() || { running: false };',
  'VNC status payload');

let launcher = original['plugins/vnc/vnc-launcher.js'];
launcher = replaceOnce(launcher,
  'export function buildWatcherEnv({ resolution, vncPassword, viewOnly, vncPort, novncPort, statusFile }, env = process.env) {',
  `// ${MARKER}: watcher receives immutable accepted process identities.
export function buildWatcherEnv({ resolution, vncPassword, viewOnly, vncPort, novncPort, statusFile, identity }, env = process.env) {`,
  'watcher environment signature');
launcher = replaceOnce(launcher,
  '    VNC_STATUS_FILE: statusFile,',
  `    VNC_STATUS_FILE: statusFile,
    VNC_LAUNCH_ID: identity.launchId,
    VNC_GENERATION: identity.generation,
    VNC_SERVER_PID: identity.serverPid,
    VNC_BROWSER_PID: identity.browserPid,
    VNC_BROWSER_STARTTIME: identity.browserStartTime,
    VNC_XVFB_PID: identity.xvfbPid,
    VNC_XVFB_STARTTIME: identity.xvfbStartTime,
    VNC_DISPLAY: identity.display,`,
  'watcher identity environment');
launcher = replaceOnce(launcher,
  'export function startWatcher({ resolution, vncPassword, viewOnly, vncPort, novncPort, log, events }) {',
  'export function startWatcher({ resolution, vncPassword, viewOnly, vncPort, novncPort, identity, log, events }) {',
  'watcher launch signature');
launcher = replaceOnce(launcher,
  '    env: buildWatcherEnv({ resolution, vncPassword, viewOnly, vncPort, novncPort, statusFile }),',
  '    env: buildWatcherEnv({ resolution, vncPassword, viewOnly, vncPort, novncPort, statusFile, identity }),',
  'watcher identity launch');

const watcher = `#!/bin/sh
# ${MARKER}: exact accepted display identity, owned children, bounded teardown.
set -eu

VNC_PORT="\${VNC_PORT:-5900}"
NOVNC_PORT="\${NOVNC_PORT:-6080}"
VNC_STATUS_FILE="\${VNC_STATUS_FILE:-}"
VNC_BIND="\${VNC_BIND:-127.0.0.1}"
DISPLAY_NUM="\${VNC_DISPLAY#:}"
X11VNC_PID=""
NOVNC_PID=""

log() { printf '[vnc-watcher] %s\\n' "$*" >&2; }
clear_status() { [ -z "$VNC_STATUS_FILE" ] || rm -f "$VNC_STATUS_FILE"; }
proc_identity_ok() {
  _pid="$1"; _start="$2"; _ppid="$3"; _needle="$4"
  [ -r "/proc/$_pid/stat" ] && [ -r "/proc/$_pid/status" ] && [ -r "/proc/$_pid/cmdline" ] || return 1
  _stat=$(cat "/proc/$_pid/stat") || return 1
  _tail=\${_stat##*) }
  set -- $_tail
  [ "$1" != Z ] && [ "\${20}" = "$_start" ] || return 1
  [ "$(awk '/^PPid:/ { print $2 }' "/proc/$_pid/status")" = "$_ppid" ] || return 1
  tr '\\000' ' ' < "/proc/$_pid/cmdline" | grep -F -- "$_needle" >/dev/null
}
child_alive() {
  _pid="$1"
  [ -n "$_pid" ] && [ -r "/proc/$_pid/stat" ] || return 1
  _stat=$(cat "/proc/$_pid/stat") || return 1
  _tail=\${_stat##*) }
  set -- $_tail
  [ "$1" != Z ] && kill -0 "$_pid" 2>/dev/null
}
stop_child() {
  _pid="$1"
  [ -n "$_pid" ] || return 0
  kill "$_pid" 2>/dev/null || true
  _attempt=0
  while child_alive "$_pid" && [ "$_attempt" -lt 10 ]; do
    sleep 0.1
    _attempt=$((_attempt + 1))
  done
  child_alive "$_pid" && kill -KILL "$_pid" 2>/dev/null || true
  wait "$_pid" 2>/dev/null || true
}
cleanup() {
  trap - EXIT INT TERM
  stop_child "$NOVNC_PID"
  stop_child "$X11VNC_PID"
  clear_status
}
trap cleanup EXIT
trap 'exit 0' INT TERM

[ -n "\${VNC_SERVER_PID:-}" ] && [ -n "\${VNC_BROWSER_PID:-}" ] && [ -n "\${VNC_BROWSER_STARTTIME:-}" ] \\
  && [ -n "\${VNC_XVFB_PID:-}" ] && [ -n "\${VNC_XVFB_STARTTIME:-}" ] && [ -n "\${VNC_DISPLAY:-}" ] \\
  && [ -n "\${VNC_LAUNCH_ID:-}" ] && [ -n "\${VNC_GENERATION:-}" ] \\
  || { log 'missing accepted launch identity'; exit 1; }
proc_identity_ok "$VNC_BROWSER_PID" "$VNC_BROWSER_STARTTIME" "$VNC_SERVER_PID" camoufox-bin || { log 'browser identity mismatch'; exit 1; }
proc_identity_ok "$VNC_XVFB_PID" "$VNC_XVFB_STARTTIME" "$VNC_SERVER_PID" /usr/bin/Xvfb || { log 'Xvfb identity mismatch'; exit 1; }
[ -S "/tmp/.X11-unix/X$DISPLAY_NUM" ] || { log 'Xvfb socket missing'; exit 1; }
PASSFILE=""
if [ -n "\${VNC_PASSWORD:-}" ]; then
  mkdir -p /tmp/.vnc
  x11vnc -storepasswd "$VNC_PASSWORD" /tmp/.vnc/passwd >/dev/null 2>&1
  PASSFILE=/tmp/.vnc/passwd
fi
NOVNC_DIR=/usr/share/novnc
[ -d "$NOVNC_DIR" ] || { log 'noVNC assets missing'; exit 1; }
websockify --web "$NOVNC_DIR" "$VNC_BIND:$NOVNC_PORT" "127.0.0.1:$VNC_PORT" >/tmp/camofox-novnc.log 2>&1 &
NOVNC_PID=$!
X11VNC_ARGS="-display :$DISPLAY_NUM -forever -shared -localhost -rfbport $VNC_PORT -noxdamage -quiet -o /tmp/camofox-x11vnc.log"
[ "\${VIEW_ONLY:-0}" = 1 ] && X11VNC_ARGS="$X11VNC_ARGS -viewonly"
if [ -n "$PASSFILE" ]; then X11VNC_ARGS="$X11VNC_ARGS -rfbauth $PASSFILE"; else X11VNC_ARGS="$X11VNC_ARGS -nopw"; fi
# shellcheck disable=SC2086
x11vnc $X11VNC_ARGS &
X11VNC_PID=$!
sleep 1
child_alive "$NOVNC_PID" && child_alive "$X11VNC_PID" || { log 'VNC transport failed to start'; exit 1; }
[ -z "$VNC_STATUS_FILE" ] || printf '%s %s\\n' ":$DISPLAY_NUM" "$X11VNC_PID" > "$VNC_STATUS_FILE"
log "VNC ready for launch=$VNC_LAUNCH_ID generation=$VNC_GENERATION display=:$DISPLAY_NUM"

while sleep 1; do
  proc_identity_ok "$VNC_BROWSER_PID" "$VNC_BROWSER_STARTTIME" "$VNC_SERVER_PID" camoufox-bin || exit 1
  proc_identity_ok "$VNC_XVFB_PID" "$VNC_XVFB_STARTTIME" "$VNC_SERVER_PID" /usr/bin/Xvfb || exit 1
  child_alive "$NOVNC_PID" && child_alive "$X11VNC_PID" || exit 1
done
`;

const outputs = {
  'plugins/vnc/index.js': index,
  'plugins/vnc/vnc-watcher.sh': watcher,
  'plugins/vnc/vnc-launcher.js': launcher,
};
const staged = [];
const backups = [];
try {
  for (const [file, source] of Object.entries(outputs)) {
    const stat = fs.statSync(file);
    const stage = `${file}.spartan-stage-${process.pid}`;
    fs.writeFileSync(stage, source, { mode: stat.mode });
    fs.chmodSync(stage, stat.mode);
    fs.chownSync(stage, stat.uid, stat.gid);
    fs.fsyncSync(fs.openSync(stage, 'r'));
    staged.push([file, stage]);
  }
  for (const [file, stage] of staged) {
    const backup = `${file}.spartan-backup-${process.pid}`;
    fs.renameSync(file, backup);
    backups.push([file, backup]);
    fs.renameSync(stage, file);
  }
  for (const [, backup] of backups) fs.unlinkSync(backup);
} catch (error) {
  for (const [file, backup] of backups.reverse()) {
    if (fs.existsSync(backup)) {
      if (fs.existsSync(file)) fs.unlinkSync(file);
      fs.renameSync(backup, file);
    }
  }
  throw error;
} finally {
  for (const [, stage] of staged) if (fs.existsSync(stage)) fs.unlinkSync(stage);
}
