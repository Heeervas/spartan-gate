# Fixed-text Discord reminders

Source-only opt-in plugin. No helper, profile, job or service is installed by
importing or registering it. It does not restore Alberto Tools, pins or old jobs.
The separately owned `discord-deferred-thread-title` retains `/rename-thread`.

## Contract

- `discord_reminder_create(duration, note)` retains the original create contract.
  One explicit task/current direct Discord request; `30m`, `2h`, `1d2h30m`, up to
  365 days. Missing task/time clarifies; an empty note can use an explicit reply.
- `discord_reminder_cancel(reminder_id)` accepts only this plugin's opaque receipt,
  from its original requester/profile/exact conversation. It revokes literal
  input then pauses the bound native job. It never lists or removes other jobs.
- Explicit service enrollment and a requester subset are mandatory. The active
  profile's native source toolset resolver, adapter overrides, composite/global
  exclusions and cron availability must allow both tools and `cronjob_manage`.
  The normal native dispatch still runs pre-tool policy and execution middleware.
  No raw registry dispatch, skip flags, generic cron exposure or disable override.
- Fresh ingress is correlated through native session/task/turn execution IDs.
  Cross-profile routes, missing registered receiving transport, internal/bot and
  delegated contexts refuse. This initial deployment proposal selects DEFAULT
  only; Work and other profiles receive no implicit enrollment.
- The native job is a paused one-shot (`no_agent=True`, fixed script, repeat one)
  until literal input/job binding is saved and read back, then resumed. Native
  scheduling, locks, execution/delivery claims and unknown-send evidence remain
  native. A retry never changes its due time or creates a second runnable job.
- Literal task bytes are retained in JSON. Display is deterministic, not generated:
  mentions and native media/control directives are neutralized. Native transport
  wrapping/redaction still applies; this is not byte-identical network delivery.
  There is **no model at firing time**, alternate dispatcher or retry engine.
- A feature-root lock serializes create/recovery/cancel/helper input validation.
  Native job operations occur only after the feature lock; the helper performs
  no job operation. No network send occurs under the feature lock. Helper lock
  contention exits 75 (native failure, not silently successful consumption).
- Grants expire at the original native due time plus the supported 120-second
  one-shot grace. Invalid/revoked/expired input emits no text. The 128-input cap
  denies new creation; it does not evict active or unknown-delivery evidence.
  Input cleanup is operator-owned; no background sweeper or automatic rearming.

Cancellation may be too late after output/send was claimed. Persistence readback
is not receipt; native unknown delivery remains unknown, never automatic replay.
Feature disable, requester-subset removal or receipt revocation stops future
helper output. Discord permissions still apply at send. Editing some other
allowlist is **not** claimed to revoke all previously granted reminders: native
cron exposes no live Discord-role check to this subprocess.

## Explicit trust boundary

The static native script receives no authenticated job ID or argv payload.
The request directory is an input capability, not authenticated executing-job
identity. Same-profile native script/job administrators and same-UID filesystem
writers are trusted, as they already can run code. This is not a tenant sandbox,
job-administrator isolation or exactly-once claim. Reminder tools expose neither
input paths nor arbitrary jobs/scripts/recipients. No execution tokens, extra
jobs reader, credentials or core patch are introduced.

## Provisioning proposal — NOT performed

See `docs/discord-reminders-design.md` for the reviewed design and authority.
A later, separately authorized operator window must:

1. Resolve DEFAULT's real profile home and runtime UID/GID; inspect only required
   enrollment facts. Verify profile/scripts are real same-owner directories,
   not group/world-writable, and the helper/input destinations are vacant.
   Missing/incompatible layout is a refusal, not permission to chmod personal data.
2. Add only this named plugin's read-only mount (example below); never bind over
   all plugins or scripts. Existing DEFAULT configuration and Work stay intact.
3. Copy exactly the reviewed `reminder_helper.py` bytes to the regular file
   `$HERMES_HOME/scripts/discord-reminder.py`, owned by runtime UID, mode `0600`.
   Stage an owned temporary file, compare expected absent/old bytes immediately
   before atomic replacement, and verify SHA-256/mode after installation. The
   helper cannot be a symlink outside scripts; native cron rejects such escapes.
4. Create only the vacant `$HERMES_HOME/scripts/.discord-reminder-inputs` at
   `0700` with runtime ownership. Runtime JSON and lock files are `0600`. Do not
   read/migrate historical jobs or overwrite any personal script/input.
5. CAS-update only the selected profile's enrollment, preserving all native
   disables and existing toolsets. The public shape is:

   ```yaml
   plugins:
     enabled: [discord-reminders] # append, do not replace existing entries
     entries:
       discord-reminders:
         enabled: true
         profile: default
         requesters: [] # operator selects explicit Discord user IDs privately
   ```

   An empty requester list refuses all creation. Select the `discord-reminders`
   toolset without overwriting existing selections. Do not enable `cronjob` or
   remove its exclusions implicitly: a current native cron denial is a material
   operator decision. Saved defaults/config are not fresh effective registration.
6. Controller coordinates any shared-gateway reload/quiet window, verifies actual
   DEFAULT discovery/registration and exact helper bytes, and separately approves
   one disposable human reminder/cancel trial. No image build is required by this
   source design. Do not activate Work or schedule inference-backed tests.

One named mount, expressed as a template only:

```yaml
services:
  hermes:
    volumes:
      - type: bind
        source: ../hermes-plugins/discord-reminders
        target: /opt/data/plugins/discord-reminders
        read_only: true
        bind:
          create_host_path: false
```

Rollback first disables new enrollment and revokes/pauses only bound plugin jobs,
accounting for in-flight/unknown sends. Restore only exact still-matching owned
helper/config bytes. Keep nonterminal input/output evidence; never restore a
whole config/jobs file/scripts tree or delete other-profile state. A helper
upgrade without the matching installed copy fails closed on byte comparison.

## Offline source verification

Explicit inputs are program files only, never an active home. `HERMES_PROGRAM_SOURCE`
is a complete supported native program tree with its ordinary dependencies;
`HERMES_REMINDER_SOURCE` is either that tree or selected installed-program exports.
The fixture compares relevant native source hashes, then creates a disposable
HOME/profile/store with synthetic IDs and denies socket calls. It does not start
Hermes, a scheduler, bot, provider or platform connection. No real job is created.

```sh
HERMES_PROGRAM_SOURCE=/path/to/native-program-source \
HERMES_REMINDER_SOURCE=/path/to/matching-installed-program-exports \
PYTHONDONTWRITEBYTECODE=1 \
python infra/hermes-plugins/test_discord_reminders.py -v
```

Core proof includes actual native PluginContext/manifest opt-in/disable handling,
bounded hook and middleware correlation, per-source resolver and native policy
refusals, real synthetic native job persistence, process death after paused-create
and concurrent recovery with ONE unchanged job/due, foreign-conversation cancel
refusal and retention of synthetic unknown-send status/output. Actual native
media/wake/silence parsers consume hostile literal envelopes with active unsafe
positive controls. Helper subprocess lock-timeout RED/GREEN protects a real bug.
The extended installed-helper/no_agent subprocess boundary can also be exercised
with synthetic native execution; no result implies Discord delivery or registration.

This is not full native gateway discovery, cached-agent tool exposure, all native
dependencies or live acceptance. Missing explicit program inputs fail, not skip.
Run existing unchanged TOP/native-source project gates and independent exact-
candidate review separately. Limits/warnings must remain visible.
