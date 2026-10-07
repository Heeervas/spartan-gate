"""Pinned native handle expander for inert authorization tests."""
def _expand_string(text: str, store, cfg: dict, stats: dict,
                   session_id: str = "", tool_name: str = "") -> tuple:
    """Expand tla:<id> tokens in *text*.

    Returns ``(new_text, dest_denied)``. When ``tool_name`` is on the
    external-destinations deny list, every token in this string is swapped
    for the destination-deny marker so the destination tool sees a refusal
    (it cannot accidentally proceed with the raw token as a payload).
    """
    if "tla:" not in text:
        return text, False
    denied = _external_destination_denied(tool_name, cfg) if tool_name else False
    if denied:
        # len() of finditer: TOKEN_RE now has optional @N groups (T4.1),
        # so findall would return tuples — the count is what matters.
        stats["dest_denied"] = stats.get("dest_denied", 0) + sum(
            1 for _ in TOKEN_RE.finditer(text))

    cap = int(cfg.get("passref_max_chars", 500000))
    total_cap = int(cfg.get("passref_total_max_chars", 2000000))

    def _sub(m: re.Match) -> str:
        if denied:
            # T1.5: every token denied by the destination-deny list emits
            # exactly one ledger line. The call is best-effort and never
            # raises into the expansion path.
            _log_expansion(cfg, sid=session_id, blob_id=m.group(1),
                           dst_tool=tool_name, chars=0, decision="dest_denied")
            return _DEST_DENY_MARKER_PREFIX
        blob_id = m.group(1)
        version_n = int(m.group(3) or 0)
        # T4.1: resolve ``tla:<bid>@<N>`` to the chain-specific bid in
        # the calling session's index. ``version_n == 0`` is the bare
        # bid form (no @N) — that path is byte-identical to pre-T4.1.
        idx: dict | None = None
        if version_n > 0:
            if session_id and store is not None:
                resolved: str | None = None
                try:
                    idx = store._load_idx(session_id)
                    resolved = store._resolve_version(
                        idx, blob_id, version_n)
                except Exception:
                    resolved = None
                blobs_idx: dict = idx.get("blobs", {}) if idx else {}
                if not resolved or resolved not in blobs_idx:
                    if stats.get("total", 0) >= total_cap:
                        _log_expansion(cfg, sid=session_id, blob_id=blob_id,
                                       dst_tool=tool_name, chars=0,
                                       decision="budget_capped")
                        return (f"[Toolaria: total expansion budget "
                                f"{total_cap:,} chars exceeded]")
                    _log_expansion(cfg, sid=session_id, blob_id=blob_id,
                                   dst_tool=tool_name, chars=0,
                                   decision="missing")
                    stats["missing"] = stats.get("missing", 0) + 1
                    return (f"{VERSION_REFUSE_MARKER_PREFIX}{version_n}"
                            f"{VERSION_REFUSE_MARKER_MIDDLE}{blob_id}"
                            f"{VERSION_REFUSE_MARKER_SUFFIX}")
                blob_id = resolved
            else:
                # Either no session scoped (empty-session fallback)
                # or no store at all: treat unknown @N the same way as
                # the rest of passref treats an unwalkable chain.
                stats["missing"] = stats.get("missing", 0) + 1
                return (f"{VERSION_REFUSE_MARKER_PREFIX}{version_n}"
                        f"{VERSION_REFUSE_MARKER_MIDDLE}{blob_id}"
                        f"{VERSION_REFUSE_MARKER_SUFFIX}")
        if stats.get("total", 0) >= total_cap:
            _log_expansion(cfg, sid=session_id, blob_id=blob_id,
                           dst_tool=tool_name, chars=0,
                           decision="budget_capped")
            return f"[Toolaria: total expansion budget {total_cap:,} chars exceeded]"
        # Session scoping: when the host forwards a session_id, a blob the
        # calling session does not reference is refused (it belongs to, or was
        # guessed against, another session). An empty session_id means vanilla
        # Hermes did not forward one, so we keep the global behaviour and let
        # single-session setups work, mirroring fetch's all-session fallback.
        if session_id and store and not store.session_references(blob_id, session_id):
            stats["denied"] = stats.get("denied", 0) + 1
            _log_expansion(cfg, sid=session_id, blob_id=blob_id,
                           dst_tool=tool_name, chars=0,
                           decision="session_denied")
            return f"[Toolaria: blob {blob_id} not available in this session]"
        content = store.blob_text(blob_id) if store else None
        if content is None:
            stats["missing"] = stats.get("missing", 0) + 1
            _log_expansion(cfg, sid=session_id, blob_id=blob_id,
                           dst_tool=tool_name, chars=0, decision="missing")
            return f"[Toolaria: blob {blob_id} unavailable; re-run the source tool]"
        # T2.3: credential-grade enforcement. With enforcement on, a
        # credential blob only expands into destinations on the
        # allowlist; everyone else sees the deterministic refusal
        # marker. The check happens after session-scope + existence so
        # a denied attempt still costs the same I/O as an allowed one
        # (no side-channel about whether the blob exists for the
        # calling session).
        label = _find_label(store, blob_id, session_id)
        if _credential_enforcement_active(cfg) and label == "credential":
            allow = _credential_destinations_allow(cfg)
            if tool_name not in allow:
                stats["denied"] = stats.get("denied", 0) + 1
                _log_expansion(cfg, sid=session_id, blob_id=blob_id,
                               dst_tool=tool_name, chars=0,
                               decision="credential_denied",
                               label=label)
                return (f"{CREDENTIAL_REFUSE_MARKER_PREFIX}{blob_id}"
                        f"{CREDENTIAL_REFUSE_MARKER_SUFFIX}")
        if len(content) > cap:
            content = (content[:cap] +
                       f"\n[Toolaria: truncated, blob is {len(content):,} chars "
                       f"> passref_max_chars {cap:,}]")
        stats["expanded"] = stats.get("expanded", 0) + 1
        stats["total"] = stats.get("total", 0) + len(content)
        _log_expansion(cfg, sid=session_id, blob_id=blob_id,
                       dst_tool=tool_name, chars=len(content),
                       decision="expanded", label=label)
        return content

    return TOKEN_RE.sub(_sub, text), denied

