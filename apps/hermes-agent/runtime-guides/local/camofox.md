# Camofox Browser Runtime

This document describes the Camofox mode shipped by Spartan Gate. It is for
Hermes browser work inside the runtime. Operator procedures remain in
`infra/camofox/README.md` and `docs/operations.md` in the Spartan Gate source
tree.

## When This Backend Is Active

Camofox is a REST service around Camoufox, a Firefox-derived anti-detection
browser. Spartan Gate selects it for every Hermes profile when the deployment
sets `CAMOFOX_URL`. This administrator policy overrides profile cloud/CDP
settings and keeps the built-in `browser_*` tools; `browser_exec` is unavailable
in this mode. Camofox is the supported persistent browser; Browserless/CDP
workflows are retired, not an alternate mode. Google Meet supports caption
transcription only, not realtime audio. No Chrome/CDP replacement is implied.

The endpoint and authentication are provided through Hermes' native managed
secret scope. Their absence from the terminal environment does not identify the
active browser. A failed Camofox connection/authentication returns an error;
Hermes must not silently launch Chromium. Each profile retains its own durable
identity and session state.

The optional Camofox-only image requires this managed configuration at startup.
It cannot provision or select local Chromium, including through explicit Hermes
PM installs, transitive browser dependencies, Meet installation/auth commands,
or tool staging. Tool updates continue for supported packages. Fix Camofox
connectivity or authentication when a browser call fails; installing Chromium
inside this image cannot restore browser service.

Prefer Hermes' `browser_*` tools. They apply URL and website policy checks,
redact returned text, keep one tab associated with the durable Hermes session,
and recover safely from a replaced browser generation. Calling the REST API
directly bypasses some of those client-side checks and should be limited to a
capability that Hermes does not expose, such as a controlled viewport change.

Browser page traffic still passes through Spartan Gate's proxy and DNS
allowlists. A browser backend is not an egress-policy bypass.

## Hermes Capabilities

Call `browser_navigate` first. It creates or reuses the tab, navigates, and
normally returns a compact accessibility snapshot with element refs.

| Tool | Camofox behavior |
| --- | --- |
| `browser_navigate` | Open a URL or navigate the current tab; recreates a stale tab only on explicit navigation. |
| `browser_snapshot` | Return compact or full accessibility text and refs such as `@e3`. |
| `browser_click` | Click an element ref from the latest snapshot. |
| `browser_type` | Clear and fill an element ref; returned text is secret-redacted. |
| `browser_scroll` | Scroll up or down. |
| `browser_back` | Go back in page history. |
| `browser_press` | Send a key such as `Enter`, `Tab`, or `Escape`. |
| `browser_get_images` | Derive image URLs and alt text from the accessibility snapshot. |
| `browser_vision` | Save a PNG and analyze it with the configured auxiliary vision model. |
| `browser_console` | Evaluate a supplied JavaScript expression; console-log capture itself is unavailable and otherwise returns an empty result with a note. |

Refs belong to the current page state. Refresh the snapshot after navigation or
an interaction that materially changes the DOM. Do not replay a click, type,
or JavaScript action after `stale_browser_generation`; call
`browser_navigate` explicitly so a one-time URL or side effect is not silently
replayed.

Hermes does not expose browser tools for forward, refresh, arbitrary waits,
downloads, structured extraction, explicit tab close, or viewport changes in
Camofox mode. The service has low-level endpoints for some of these operations,
but that does not make them normal Hermes tool capabilities.

`browser_vision` can send the screenshot to the configured auxiliary vision
model when the active model cannot inspect it directly. Treat screenshots as
potentially sensitive. Managed cleanup drops Hermes' process-local tab
tracking without deleting the persisted Camofox context.

## Sessions, Groups, And Tabs

Camofox uses three identifiers:

- `userId` owns one browser context and its persisted storage state.
- `sessionKey` groups tabs inside that context. `listItemId` is the legacy name
  accepted by the API.
- `tabId` identifies one live page.

With managed persistence, Spartan Gate derives a stable `userId` from the
Hermes profile. Hermes groups agent tabs by durable Hermes `session_id`, with
`task_id` only as a fallback. The operator login helper uses the reserved
`spartan-operator-login` group. The groups share cookies inside the same
profile context but do not adopt or navigate each other's tabs.

The public defaults are:

| Limit | Default | Consequence |
| --- | ---: | --- |
| Contexts / `userId` sessions | 8 | A ninth context is rejected. |
| Tabs per context | 4 | Normal creation recycles the oldest tab in that context at capacity. |
| Tabs across Camofox | 12 | Normal creation may recycle the oldest tab in the target context. |
| Concurrent requests per user | 2 | Further work waits in the bounded per-user queue. |
| Concurrent browser create/lifecycle operations | 1 | Camoufox mutations are serialized globally. |
| Create waiters | 8 | Capacity beyond the bounded FIFO is rejected. |

Hermes normally owns one tracked tab per durable session, but popups, operator
tabs, other Hermes sessions, and direct API callers all count toward the same
limits. Use `nonDestructive: true` with direct `POST /tabs` calls that must fail
with HTTP 429 instead of recycling an active tab. The operator login helper
already does this.

`GET /health` reports `activeTabs` and `activeSessions`. `GET /tabs?userId=...`
lists only the tabs for that known owner. Do not scan, guess, or cross-adopt
other profile identities.

Managed storage preserves cookies and origin `localStorage`. It is not a full
Firefox profile and does not promise extension state, preferences, downloads,
IndexedDB, or every browser storage API. Never delete a profile or the
watchdog ledger as a recovery shortcut.

## Timeouts, Cooldowns, And Recovery

There is no fixed Camofox cooldown between ordinary Hermes actions. Synchronize
with a fresh snapshot or an application-specific condition rather than adding
blind delays. The relevant default timing controls are:

| Control | Default |
| --- | ---: |
| Hermes `browser.command_timeout` | 30 s, minimum 5 s |
| Camofox request handler | 30 s |
| Navigation | 25 s |
| Accessibility-ref building | 12 s |
| Tab inactivity cleanup | 5 min |
| Browser idle cleanup | 5 min |
| Empty session cleanup | 10 min |
| Operator-tab lease | 30 min, renewable by the operator helper |
| Health monitor interval | 20 s |
| Recovery after ordinary deep-health failures | 3 failed samples |
| Recycle backoff | 0 s, 30 s, 2 min, 10 min, 30 min |
| Healthy time that resets recycle backoff | 30 min |

Three terminal real-navigation timeouts immediately latch the current browser
generation as degraded; validation, capacity, proxy/policy, TLS, queue, and
handler failures do not increment that latch. During recovery, stale or
unhealthy work fails closed.

Use status and error codes as guidance:

- `400`, `403`, or `unsupported_viewport_options`: fix the request or policy;
  do not retry blindly.
- `404`, `410`, or `stale_browser_generation`: the tab generation is gone;
  only an explicit `browser_navigate` should create a replacement.
- `429` or `tab_capacity_reached`: release unneeded tabs or wait for capacity;
  do not increase limits from an agent session.
- `503`: the queue, lifecycle, memory admission, navigation, or recovery path
  is unavailable. Check health and retry with bounded backoff.
- `409 viewport_mismatch`: live resize did not become the effective page
  viewport. A launch-time relaunch is the only supported fallback.

At 75% cgroup memory usage Camofox rejects new contexts. At 82% for two
consecutive samples it checkpoints and evicts at most one least-recently-used,
non-leased context, then waits for usage below 70% before another soft
eviction. A cgroup OOM event or the 90% hard threshold triggers supervised
recovery.

## API And Authentication

The API is internal at `CAMOFOX_URL`, normally `http://camofox:9377`. Spartan
Gate does not publish port 9377 on the host. When the access gate is enabled,
routes require:

```http
Authorization: Bearer <CAMOFOX_API_KEY>
```

Hermes receives the protected-route token in `CAMOFOX_API_KEY`; never print,
log, or place it in a URL. `GET /health` is the unauthenticated readiness
exception. Cookie import, stop/admin, and internal supervisor routes have
separate authorization contracts and are not agent endpoints.

The main route mapping is:

| Route | Used by Hermes |
| --- | --- |
| `POST /tabs` | First `browser_navigate`; requires `userId` and `sessionKey`. |
| `POST /tabs/{tabId}/navigate` | Later `browser_navigate` calls. |
| `GET /tabs/{tabId}/snapshot` | Navigate auto-snapshot, `browser_snapshot`, and image/ref support. |
| `POST /tabs/{tabId}/click` | `browser_click`. |
| `POST /tabs/{tabId}/type` | `browser_type`. |
| `POST /tabs/{tabId}/scroll` | `browser_scroll`. |
| `POST /tabs/{tabId}/back` | `browser_back`. |
| `POST /tabs/{tabId}/press` | `browser_press`. |
| `POST /tabs/{tabId}/evaluate` | `browser_console(expression=...)`, subject to Hermes evaluation and URL guards. |
| `GET /tabs/{tabId}/screenshot` | `browser_vision`. |
| `DELETE /tabs/{tabId}` | Internal managed-tab cleanup; requires the owning `userId`. |

Useful service-only routes include `GET /health`, `GET /live`,
`GET /tabs?userId=...`, `POST /tabs/{tabId}/wait`,
`POST /tabs/{tabId}/forward`, `POST /tabs/{tabId}/refresh`,
`GET /tabs/{tabId}/links`, `GET /tabs/{tabId}/images`,
`GET /tabs/{tabId}/downloads`, `GET /tabs/{tabId}/stats`,
`POST /tabs/{tabId}/extract`, and the viewport route below. A route being
present does not bypass the need for a known owning `userId`, the bearer token,
and the relevant safety policy.

`POST /tabs/open` is intentionally disabled with HTTP 410. Do not call
`/spartan/health/deep`, `/spartan/recover`, destructive session deletion,
cookie-import, or admin stop routes from an agent workflow.

For a trusted low-level call from inside Hermes, use the installed auth-aware
client helpers so the bearer token stays out of command arguments. Supply only
an exact `userId` and `tabId` already owned by the current workflow:

```python
from tools.browser_camofox import _get, _post

user_id = "<exact-owning-user-id>"
tabs = _get("/tabs", params={"userId": user_id})["tabs"]
tab_id = tabs[-1]["tabId"]
result = _post(
    f"/tabs/{tab_id}/wait",
    {"userId": user_id, "timeout": 10_000, "waitForNetwork": True},
)
```

These underscore-prefixed helpers are tied to the pinned Hermes runtime. Do
not enumerate or guess profile identities, and prefer registered browser tools
whenever they expose the required operation.

## Runtime Viewports

Hermes has no `browser_viewport` tool. Spartan Gate adds these equivalent,
authenticated routes for a known tab:

```http
POST /tabs/{tabId}/spartan/viewport
POST /tabs/{tabId}/viewport
Content-Type: application/json
Authorization: Bearer <CAMOFOX_API_KEY>

{
  "userId": "<owning-user-id>",
  "width": 390,
  "height": 844,
  "reload": false,
  "relaunch": false,
  "timeoutMs": 10000
}
```

Width and height must be integers from 100 through 4096. `timeoutMs` is clamped
to 1-60 seconds and defaults to 10 seconds. The route verifies tab ownership,
serializes against other operations on that tab, calls Playwright
`setViewportSize`, and reports `requested`, `before`, `effective`, `strategy`,
and `attempts`. Success requires the effective `innerWidth` and `innerHeight`
to match within two pixels.

Start with `relaunch: false`; it is non-destructive and returns
`viewport_mismatch` plus a hint if Firefox did not apply the live resize.
`reload: true` reloads after an effective live resize.

`relaunch: true` is a shared-browser operation. It closes every Camofox
session and tab, restarts Camoufox with a launch-time window size, recreates
the same `tabId`, and reopens only its current HTTP/HTTPS URL. Use it only on a
scratch session or with explicit operator authorization after all other
browser work has stopped. It can invalidate every active Hermes and operator
tab.

Live `deviceScaleFactor`, `isMobile`, `hasTouch`, `touch`, and `userAgent`
changes are unsupported and fail with `unsupported_viewport_options`. A narrow
screenshot is not evidence that responsive CSS saw a narrow runtime viewport;
inspect the returned effective dimensions.

From the Spartan Gate host, the safe diagnostic is:

```sh
sg-camofox-viewport-check 390 844
```

It creates and removes a scratch identity and verifies viewport behavior and
screenshot dimensions through the Hermes-to-Camofox network path.
