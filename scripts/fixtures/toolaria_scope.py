"""Pinned Toolaria functions; tests provide inert dependencies and synthetic stores."""
_passref_alive: bool = False
def _safe_cfg(ctx) -> dict:
    """Read plugin config from PluginContext or config.yaml fallback."""
    try:
        cfg = ctx.config.get("toolaria")
        if cfg:
            return dict(cfg)
    except Exception:
        pass
    try:
        import yaml
        hp = Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser()
        cf = hp / "config.yaml"
        if cf.exists():
            raw = yaml.safe_load(cf.read_text())
            return dict(raw.get("toolaria", {}))
    except Exception:
        pass
    return {}


def _merge_cfg(user_cfg: dict) -> dict:
    """Merge user config over plugin defaults.

    Phase 1 instrumentation defaults (T1.1/T1.2/T1.3/T1.4) are seeded in
    code so the cfg carries them even when PyYAML is unavailable to parse
    config.yaml. The YAML file remains the operator-facing knob; the
    code-level defaults are the hard floor that keeps the suite passing
    on the canonical test runner (``uv run --with pytest --with regex``).

    Phase 3 FIX-4: validate ``sensitivity_tool_labels`` here so a bad
    operator config (typo in a label name, list instead of dict, etc.)
    fails LOUD at register time — the plugin refuses to come up
    rather than silently disabling rescue later."""
    defaults = _load_defaults()
    defaults.update(user_cfg)
    store_path = defaults.get("store_path", "~/.hermes/toolaria")
    hermes_home = os.environ.get("HERMES_HOME")
    if hermes_home and isinstance(store_path, str):
        if store_path == "~/.hermes":
            defaults["store_path"] = hermes_home
        elif store_path.startswith("~/.hermes/"):
            defaults["store_path"] = str(
                Path(hermes_home) / store_path.removeprefix("~/.hermes/")
            )
    for _k, _v in _PHASE1_DEFAULTS.items():
        defaults.setdefault(_k, _v)
    for _k, _v in _PHASE2_DEFAULTS.items():
        defaults.setdefault(_k, _v)
    # Phase 3 FIX-4: surface operator typos at startup. The validator
    # raises ValueError listing offending keys/values; we let that
    # bubble up to register() and abort plugin load so the operator
    # sees the typo before any rescue runs.
    # Exact-budget contract (2026-08-29): validate excerpt_max_chars at
    # register time so a bad operator config fails LOUD at load rather
    # than silently clamping at rescue time.
    _exc = defaults.get("excerpt_max_chars", 8000)
    if not isinstance(_exc, int) or isinstance(_exc, bool) or _exc < 200:
        raise ValueError(
            f"excerpt_max_chars must be an integer >= 200, got {_exc!r}"
        )
    if __package__:
        from .labels import _parse_tool_label_map
    else:
        from labels import _parse_tool_label_map  # type: ignore[no-redef]
    _parse_tool_label_map(defaults.get("sensitivity_tool_labels"))
    # T3.1: validate the entity_registry the same way so a broken
    # pattern/regex/sensitivity never silently disables the governor
    # or leaks into a half-broken put().
    if __package__:
        from .entities import parse_entity_registry
        parse_entity_registry(defaults.get("entity_registry"))
    else:
        try:
            from entities import parse_entity_registry  # type: ignore[no-redef]
        except ModuleNotFoundError as exc:
            if exc.name != "entities":
                raise
        else:
            parse_entity_registry(defaults.get("entity_registry"))
    return defaults


def register(ctx) -> None:
    global _store, _cfg
    _cfg = _merge_cfg(_safe_cfg(ctx))
    # Copy rather than mutate the caller's list in place.
    excludes = list(_cfg.get("exclude_tools", []))
    for t in _UNCONDITIONAL_EXCLUDES:
        if t not in excludes:
            excludes.append(t)
    _cfg["exclude_tools"] = excludes
    try:
        _store = BlobStore(_cfg)
        # Phase 3 FIX-1 / FIX-3: backfill labels on every existing
        # index entry before the first sweep. Wrapped best-effort so a
        # single broken entry cannot break register() (the comment
        # on backfill_labels() documents the same posture).
        try:
            _store.backfill_labels()
        except Exception as e:
            logger.warning("toolaria: label backfill failed: %s", e)
        _store.lazy_sweep()
    except Exception as e:
        logger.warning("toolaria: blob store init failed, rescuing disabled: %s", e)
        _store = None

    ctx.register_hook("transform_tool_result", _on_transform)
    ctx.register_hook("on_session_start", _on_start)
    ctx.register_hook("on_session_end", _on_end)

    # Pass-by-reference: expand tla:<id> tokens in downstream tool args into
    # full blob content before the tool runs, so a rescued result can flow
    # tool to tool without ever re-entering the model's context. The
    # ``register_middleware`` contract is host-version dependent; older
    # hosts lack it entirely. Phase 0 (T0.4): on a missing register helper,
    # log a fail-loud WARNING and mark passref dead so subsequent rescue
    # handles do NOT advertise a tla:<id> instruction the host cannot
    # honour. A dead handle is worse than no rescue.
    global _passref_alive
    if hasattr(ctx, "register_middleware"):
        ctx.register_middleware(
            "tool_request",
            _make_passref_mw(lambda: _store, _cfg, _UNCONDITIONAL_EXCLUDES),
        )
        _passref_alive = bool(_cfg.get("passref_enabled", True))
    else:
        _passref_alive = False
        logger.warning(
            "toolaria: host lacks register_middleware; pass-by-reference "
            "expansion disabled. Rescue handles will omit the tla:<id> "
            "instruction (a dead handle is worse than no rescue). Upgrade "
            "the host or disable passref (passref_enabled: false) to "
            "silence this warning."
        )

    ctx.register_tool(
        name="rescuer_fetch",
        toolset=_RESCUER_TOOLSET,
        description=(
            "Fetch slices of a rescued oversized tool result. Modes: "
            "outline | search(query) | range(start,count) | grep(pattern) | "
            "chain(pattern, count) | stat | full"
        ),
        handler=_fetch,
        schema={
            "name": "rescuer_fetch",
            "description": (
                "Retrieve slices of a rescued tool result blob. Modes: "
                "outline | search(query) | range(start,count) | "
                "grep(pattern) | chain(pattern,count) | stat | full."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "Blob ID from the rescue handle block",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["outline", "search", "range", "grep",
                                 "chain", "stat", "full", "audit"],
                        "description": ("Retrieval mode (default: stat). "
                                        "'audit' returns the read-only "
                                        "expansion ledger summary."),
                    },
                    "start": {
                        "type": "integer",
                        "description": "Start line for range mode (default: 0)",
                    },
                    "count": {
                        "type": "integer",
                        "description": "Lines to return for range mode or "
                                       "context width for chain mode "
                                       "(default: 20)",
                    },
                    "pattern": {
                        "type": "string",
                        "description": "Regex for grep mode, also used by "
                                       "chain mode for the match step",
                    },
                    "query": {
                        "type": "string",
                        "description": "Natural-language query for search mode",
                    },
                },
                "required": ["id"],
            },
        },
    )

    # Availability symmetry: the rescue hook above fires UNGATED in every
    # session, so its inverse (rescuer_fetch) must be reachable in every
    # session too, or a rescued result becomes an unredeemable handle. Mark
    # the rescuer toolset ambient so the host always surfaces rescuer_fetch
    # regardless of a session's enabled_toolsets scope. Safe to expose
    # broadly because fetch() stays session-scoped at the data layer (a
    # session can only read blobs it rescued). On a host without ambient
    # support, fail LOUD rather than silently emit dead handles in
    # toolset-restricted sessions.
    _mark_rescuer_ambient()

    ctx.register_command(
        name="rescuer",
        handler=_status_cmd,
        description="Show Toolaria status: blob count, total size, sessions",
    )
    ctx.register_command(
        name="toolaria-rotate-key",
        handler=_rotate_key_cmd,
        description=("Rotate the Toolaria encryption key: "
                     "/toolaria-rotate-key <new-key-path>"),
    )


def _mark_rescuer_ambient() -> None:
    """Register the rescuer toolset as ambient with the host tool registry.

    Idempotent and host-agnostic: a host that predates ambient-toolset support
    simply lacks ``registry.mark_ambient``, in which case we emit a prominent
    warning so the operator enables the ``rescuer`` toolset for cron/profile
    sessions instead of discovering dead handles in the logs days later."""
    try:
        from tools.registry import registry
    except Exception as exc:
        logger.warning(
            "toolaria: tool registry unavailable, cannot guarantee rescuer_fetch "
            "availability (%s); rescue handles may be unredeemable in restricted "
            "sessions", exc,
        )
        return
    mark = getattr(registry, "mark_ambient", None)
    if not callable(mark):
        logger.warning(
            "toolaria: host lacks ambient-toolset support; rescuer_fetch will be "
            "UNAVAILABLE in toolset-restricted sessions (cron/profile) and rescue "
            "handles there cannot be fetched. Add 'rescuer' to those sessions' "
            "enabled_toolsets, or upgrade the host.",
        )
        return
    try:
        mark(_RESCUER_TOOLSET)
        logger.info(
            "toolaria: rescuer toolset marked ambient; rescuer_fetch is reachable "
            "in every session regardless of toolset scope",
        )
    except Exception as exc:
        logger.warning("toolaria: mark_ambient('rescuer') failed: %s", exc)


def _on_transform(
    tool_name: str = "",
    result: str = "",
    args: dict | None = None,
    session_id: str = "",
    **kwargs,
):
    """Replace oversized tool results with excerpt + rescue handle."""
    if not _is_rescuable(tool_name):
        return None
    if tool_name in _cfg.get("exclude_tools", []):
        return None
    if not result or not isinstance(result, str):
        return None
    if _store is None:
        # No durable storage means no handle can be honoured; pass the
        # result through untouched rather than destroy content.
        return None

    try:
        if len(result) > _cfg.get("max_result_chars", 12000):
            return _rescue(result, tool_name, args=args, session_id=session_id)
    except Exception as exc:
        logger.warning("toolaria: rescue failed for %s: %s", tool_name, exc)
    return None


def _fetch(args: dict | None = None, **kwargs) -> str:
    """Handle rescuer_fetch tool calls, dispatched by plugin tool registry.

    Reads session_id from kwargs when the dispatch layer forwards it; the
    store falls back to an all-session metadata search otherwise."""
    if args is None:
        args = {}
    if not _store:
        return "Error: rescuer store not initialised"

    bid = args.get("id", "")
    mode = args.get("mode", "stat")
    try:
        start = int(args.get("start", 0))
        count = int(args.get("count", 20))
    except (TypeError, ValueError):
        return "Error: start and count must be integers"
    pattern = args.get("pattern", "")
    query = args.get("query", "")
    session_id = kwargs.get("session_id", "")

    # T2.2: audit mode is a status query, not a blob retrieval. It
    # reads the expansion ledger directly (no blob bytes touched, no
    # fetch_count bumped). The blob id is accepted but unused, so a
    # operator can call audit with any well-formed handle from a recent
    # rescue without re-running the rescue.
    if mode == "audit":
        return _store._audit_summary(count)

    bid_raw = args.get("id", "")
    # T4.1: accept the ``<bid>[@<int>]`` grammar. The store's fetch
    # layer parses it; a malformed ref is rejected there too, so we
    # only check the bid half here to keep the legacy "12 hex chars"
    # error wording for callers that do not pass a ref at all.
    bid_only = bid_raw.split("@", 1)[0] if bid_raw else ""
    if not _BLOB_ID_RE.match(bid_only):
        return f"Error: invalid blob id '{bid_raw}' (expected 12 hex chars)"

    return _store.fetch(bid_raw, mode, start=start, count=count,
                        pattern=pattern, query=query, session_id=session_id)
