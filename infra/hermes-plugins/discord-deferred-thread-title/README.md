# Deferred Discord thread titles

Source-only compatibility adaptation of the selected installed
`discord-deferred-thread-title` 0.6.2 package. Original manifest attribution:
**Adán**, retained in `plugin.yaml`. The three supplied historical files had no
separate license notice; none has been removed and no public upstream origin or
additional license grant is asserted here. Source file modes were `0600`.

Historical SHA-256 identities before adaptation:

| File | SHA-256 |
| --- | --- |
| `__init__.py` | `ce2d878cb48d83a3e5875005c2121a8ce693e34eea43d47172a1e9076a50d522` |
| `plugin.yaml` | `a388e87ad1326ef321699aa4071f6f75bc8de865134fef0cefdcfd5fb17916d3` |
| `deferred_thread_title.py` | `885702e81cc75a2635931c73ca6378b25f7fa5331f3a614331da03d4c9c7c692` |

## Compatibility changes

- Append the existing workload policy to pinned Hermes' consumed
  `_TITLE_PROMPT_TEMPLATE`, preserving native language substitution, JSON schema,
  extraction and validation. Workload markers belong inside the JSON `title`.
  Keep the existing one-marker normalization/neutral fallback wrapper.
- Preserve the materialized adapter wrapper: accept shared gateway rename kwargs
  while forwarding the native `only_if_current_name` guard. Native Discord's
  adapter is installed at `plugins/platforms/discord/adapter.py`, not the retired
  `gateway/platforms/discord.py` location. No native adapter import is needed.
- Stop passing the obsolete assistant-text positional argument to
  `generate_title`; pinned Hermes interprets that position as `timeout`.
- Successful `/rename-thread close` and `drop` discard pending automatic title
  metadata, preventing a later content event from rearming a completed thread.
  Explicit terminal actions still apply their marker and archive in one edit.
- Extract the existing older-session evidence lookup into a focused helper to
  keep the newly carried source below the unchanged TOP strong-complexity limit.

## Manual command and profile boundary

This plugin, not the historical Alberto Tools pins/reminders bundle, owns
`/rename-thread [new title|close|drop]`. An empty argument requests an auxiliary
model title; an explicit title, `close`, or `drop` does not require a model.

Dispatch uses current Hermes `_is_user_authorized_for_source` and
`_intake_adapter_for`: authorize under the source transport's policy and mutate
through its registered receiving adapter. Never select a bot from the primary
`gateway.adapters` map for a secondary-profile event. Missing requester identity,
internal events, unavailable native methods or missing receiving provenance do
not rename or archive anything. Authorization is checked again when the queued
manual task begins. The source must identify a Discord thread; the command has
no destination/channel argument. Native Discord permission failures remain
failures. Existing title generation and close/drop semantics are unchanged.

The policy is portable across default and named profiles, not blanket enrollment.
Each profile must explicitly enable the existing plugin; a native disable still
wins. This change neither enables profiles nor restores old reminder jobs, pins,
commands or a dispatcher. Reminders are a separate capability: native `cronjob`
agent runs are **not** equivalent to the historical zero-agent fixed-text sender.

No Compose mounts, activation or configuration changes are included. For an
approved installation, mount only this directory read-only at
`$HERMES_HOME/plugins/discord-deferred-thread-title` and add the plugin name to
that profile's `plugins.enabled` without removing any `plugins.disabled` entry.
A shared gateway already serving profiles needs controller-coordinated reload
and command-catalog verification before availability can be claimed. Rollback
restores the previous reviewed plugin bytes and the exact prior enrollment;
it must not overwrite another profile's settings or restore an entire config.

## Focused regression

Supply the actual exported pinned program files, not a running gateway or user
profile. There are no network/provider calls. The tests execute the actual title
consumer with synthetic imported runtime boundaries and a JSON-returning provider
stub; the adapter test compiles only the actual native `rename_thread` method.
Missing program inputs fail rather than skip.

```sh
HERMES_TITLE_SOURCE=/path/to/exported/agent/title_generator.py \
HERMES_DISCORD_ADAPTER_SOURCE=/path/to/exported/discord/adapter.py \
PYTHONDONTWRITEBYTECODE=1 \
python infra/hermes-plugins/test_discord_deferred_thread_title.py -v
```

Program input SHA-256 identities used for this adaptation:

- Title consumer: `a749a64dd55f777fd4d0236429d7f89f8d7c28434727efdde240d5fe376673ee`
- Discord adapter: `625643c7a5ea4f3ec33ed0abecab2f85d8e396bf9b73981ab63ba5adb17b61aa`

This proves prompt/JSON plumbing and the narrow native adapter contract, not live
model workload judgement, Discord permissions/rate limits or gateway scheduling.
Parent-owned integrated tests, final TOP reporting and review remain separate.
