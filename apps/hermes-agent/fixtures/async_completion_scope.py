"""Pinned native delivery methods; inert regression fixture, no runtime imports."""
def _raw_process_event_session_id(evt: dict) -> str:
    """Recognize API routes, not malformed structured or partial messaging routes."""
    session_key = str(evt.get("session_key") or "").strip()
    platform = str(evt.get("platform") or "").strip().lower()
    if session_key.startswith("agent:") or platform not in {"", "api_server"}:
        return ""
    if not platform and any(evt.get(field) for field in ("chat_id", "chat_type", "thread_id")):
        return ""
    return str(evt.get("origin_session_id") or session_key or "").strip()

class Runner:
    async def _self_post_api_server(self, adapter, synth_text: str, raw_sid: str, evt: dict) -> bool:
        """Deliver to a non-push (api_server) session by raw session id.

        Async-delegation completions are persisted as a durable delivery row — after the parent
        turn's event.complete the CLIENT owns the next turn on this stateless surface, so never
        self-post them as a new role=user prompt. Other watch events wake the session via self-post.
        """
        from gateway.wake import deliver_wake, persist_delegation_delivery
        scope = contextlib.nullcontext()
        if evt.get("type") == "async_delegation":
            info = "Async delegation completion — persisting delivery row for api_server session %s (no wake turn)"
            fail = "Async delegation delivery persist failed for session %s: %s"
            deliver = lambda: persist_delegation_delivery(adapter, text=synth_text, session_id=raw_sid, evt=evt)  # noqa: E731
        else:
            info = "Watch pattern notification — waking api_server session %s via self-post"
            fail = "Watch notification self-post wake failed for session %s: %s"
            from agent.notification_presentation import diagnostic_process_event
            try:
                served = await asyncio.to_thread(self._served_api_server_wake_profile, evt, raw_sid)
            except LookupError as e:
                logger.warning(fail, raw_sid, e)
                return False
            if served:
                # The wake runs in the OWNING profile's scope, in-process (see ``deliver_wake``):
                # the raw event carries no profile, so a completion scope was never installed.
                from gateway.run import _async_profile_runtime_scope
                source = SessionSource(platform=Platform.API_SERVER, chat_id=raw_sid, profile=served)
                scope = _async_profile_runtime_scope(self._resolve_profile_home_for_source(source))
            deliver = lambda: deliver_wake(adapter, text=_mark_internal_notification(synth_text), session_id=raw_sid, profile=served,
                notification_category="diagnostic" if diagnostic_process_event(evt) else "result")  # noqa: E731
        try:
            logger.info(info, raw_sid)
            async with scope:
                await deliver()
            return True
        except Exception as e:
            logger.warning(fail, raw_sid, e)
            return False

    async def _inject_watch_notification(
        self, synth_text: str, evt: dict, *, raise_not_accepted: bool = False,
    ) -> Optional[bool]:
        """Inject a watch/completion notification as a synthetic message event.

        Routing comes from the queued event, never the active foreground message. Returns
        ``True`` on adapter acceptance, ``False`` on retryable adapter failure, ``None`` with no
        gateway route. Not transactional: a crash after acceptance can replay (at-least-once).
        """
        from gateway.wake import WakeNotAccepted, adapter_supports_push, admit_internal_event
        source = await asyncio.to_thread(self._build_process_event_source, evt)
        if not source:
            # API-server sessions bind the RAW X-Hermes-Session-Id key, not a structured ``agent:...`` key.
            raw_sid = _raw_process_event_session_id(evt)
            if raw_sid:
                adapter = self.adapters.get(Platform.API_SERVER)
                if adapter is not None and not adapter_supports_push(adapter):
                    return await self._self_post_api_server(adapter, synth_text, raw_sid, evt)
                logger.debug(
                    "Deferring watch notification for raw session %s: no api_server adapter to self-post through",
                    raw_sid,
                )
                return False
            logger.warning(
                "Dropping watch notification with no routing metadata for process %s",
                evt.get("session_id", "unknown"),
            )
            return None
        platform_name = source.platform.value if hasattr(source.platform, "value") else str(source.platform)
        adapter = self._resolve_injection_adapter(platform_name, source)
        if not adapter and platform_name == Platform.API_SERVER.value and getattr(source, "profile", None):
            # A route-only served profile owns no adapter map; the shared listener wakes exactly the
            # session that profile's store owns (proven in ``_self_post_api_server``), fail-closed.
            adapter = self.adapters.get(Platform.API_SERVER)
        if not adapter:
            return False
        if not adapter_supports_push(adapter):
            # Non-push adapter (api_server): its chat_id IS the raw session id, so handle_message would
            # key the wake under a build_session_key() that never matches — self-post instead.
            raw_sid = str(evt.get("origin_session_id") or "").strip() or str(source.chat_id or "")
            return await self._self_post_api_server(adapter, synth_text, raw_sid, evt)
        try:
            metadata = {"notification_origin": "process_registry_synthetic"}
            synth_text = _mark_internal_notification(synth_text)
            session_key = str(evt.get("session_key") or "").strip()
            from agent.notification_presentation import diagnostic_process_event
            if diagnostic_process_event(evt):
                metadata["notification_category"] = "diagnostic"
            if session_key.startswith("agent:"):
                metadata["gateway_session_key"] = session_key
            parent_session_id = str(evt.get("parent_session_id") or "").strip()
            if parent_session_id:
                metadata["gateway_session_id"] = parent_session_id
            # The queued event's ``message_id`` is the message that STARTED the process, and the
            # persisted origin carries the same stale id. A synthetic completion is not a reply to
            # it: by delivery time the user has often continued elsewhere, and an event anchored
            # there makes the finished job's reply quote that old message on every reply-anchoring
            # platform (#52694: a background completion visibly replying to a stale Discord DM
            # message). Routing is unaffected — topic lanes carry thread_id and the anchor-less
            # synthetic-send branches are covered (#87051); the original id rides metadata for
            # debugging only.
            trigger_message_id = str(evt.get("message_id") or "").strip() or None
            if trigger_message_id:
                metadata["original_trigger_message_id"] = trigger_message_id
            if getattr(source, "message_id", None):
                from gateway.session_identity import replace_source
                source = replace_source(source, message_id=None)
            synth_event = MessageEvent(
                text=synth_text, message_type=MessageType.TEXT, source=source, internal=True,
                metadata=metadata,
            )
            logger.info(
                "Watch pattern notification — injecting for %s chat=%s thread=%s",
                platform_name, source.chat_id, source.thread_id,
            )
            # Relay egress priming: post-restart routing caches are cold (they warm only on inbound), so
            # replies would egress without tenant discriminators and be declined by the connector.
            _prime = getattr(adapter, "prime_routing_cache", None)
            if callable(_prime):
                _prime(synth_event)
            await admit_internal_event(adapter, synth_event)
            return True
        except WakeNotAccepted:
            # Durable callers refund the claim; ordinary watch callers just requeue.
            if raise_not_accepted:
                raise
            return False
        except Exception as e:
            logger.error("Watch notification injection error: %s", e)
            return False

    async def _completion_delivery_ready(self, evt: dict) -> bool:
        """Unavailable owners/transports must not spend a durable delivery attempt."""
        from gateway.wake import adapter_supports_push

        parent_session_id = str(evt.get("parent_session_id") or "").strip()
        if parent_session_id:
            verdict = await self._classify_completion_target(parent_session_id)
            if verdict != "deliver":
                # Definitively closed targets still need the normal terminal disposition.
                return verdict == "terminal"
        source = await asyncio.to_thread(self._build_process_event_source, evt)
        if source is not None:
            platform = source.platform.value if hasattr(source.platform, "value") else str(source.platform)
            adapter = self._resolve_injection_adapter(platform, source)
        else:
            raw_sid = _raw_process_event_session_id(evt)
            adapter = self.adapters.get(Platform.API_SERVER) if raw_sid else None
            if adapter is not None and adapter_supports_push(adapter):
                return False
        if adapter is None:
            return False
        if not adapter_supports_push(adapter):
            ensure = getattr(adapter, "_ensure_session_db", None)
            try:
                if not callable(ensure) or await asyncio.to_thread(ensure) is None:
                    return False
            except Exception:
                logger.debug("Async-completion delivery DB unavailable", exc_info=True)
                return False
        return True

    def _completion_event_scope(self, evt: dict):
        """Profile runtime scope of the session a completion event targets (a no-op context when the
        event is the default profile's or the scope is already installed).

        The pre-flight (``_classify_completion_target`` → ``_session_db``) and every durable-ledger op
        (``tools.async_delegation`` → ``get_hermes_home()/state.db``) resolve from the ambient scope.
        The supervised ``_async_delegation_watcher`` and startup-recovered process watchers run under
        the ROOT scope, so a secondary profile's completion was looked up in the DEFAULT profile's
        state.db — classified ``terminal`` and dropped, its ledger row stranded ``pending`` forever."""
        from gateway.run import _async_profile_runtime_scope
        from hermes_constants import get_hermes_home_override
        source = self._build_process_event_source(evt)
        if source is None or not getattr(source, "profile", None):
            # No routed profile: the launch profile's own completion. Bind ITS scope once the
            # process multiplexes — unscoped, a fail-closed ledger read raises on a legitimate
            # launch-profile event (no-op while single-profile).
            from tui_gateway.launch_profile_policy import async_launch_profile_scope_if_multiplexed
            return async_launch_profile_scope_if_multiplexed()
        profile_home = self._resolve_profile_home_for_source(source)
        if get_hermes_home_override() == str(profile_home):
            return contextlib.nullcontext()  # already inside this profile's scope
        return _async_profile_runtime_scope(profile_home)

    async def _deliver_completion_notification(
        self, synth_text: str, evt: dict, *, sibling_claims=(),
    ) -> Optional[bool]:
        """Acknowledge one admitted batch, refund refusals, or release failed deliveries.

        True means adapter admission, not model execution; None means deduplicated or
        terminal. False remains retryable. Claims are settled together for every sibling.
        """
        async with self._completion_event_scope(evt):
            return await self._deliver_completion_notification_scoped(
                synth_text, evt, sibling_claims=sibling_claims)

    async def _deliver_completion_notification_scoped(
        self, synth_text: str, evt: dict, *, sibling_claims=(),
    ) -> Optional[bool]:
        from gateway.wake import WakeNotAccepted
        identity = self._completion_delivery_identity(evt)
        claim = self._CompletionClaim()
        accepted = identity_claimed = refused = False
        try:
            claim = await self._preflight_completion_delivery(evt)
            if not claim.proceed:
                return claim.early_result
            if identity is not None:
                if self._completion_identity_seen(identity, claim=True):
                    return None
                identity_claimed = True
            injection_result = await self._inject_watch_notification(synth_text, evt, raise_not_accepted=True)
            if injection_result is not True:
                return injection_result
            accepted = True
            if identity is not None:
                with self._completion_delivery_lock:
                    self._mark_completions_delivered_locked((identity,))
            return True
        except WakeNotAccepted:
            refused = True
            return False
        finally:
            if identity_claimed and not accepted:
                with self._completion_delivery_lock:
                    self._completion_deliveries_inflight.discard(identity)
            operation = "complete" if accepted else "defer" if refused else "release"
            if claim.claim_id:
                self._settle_durable_claim(operation, claim.delegation_id, claim.claim_id)
            for sibling, claim_id in sibling_claims:
                if claim_id:
                    self._settle_durable_claim(operation, sibling["delegation_id"], claim_id)
            if accepted and sibling_claims:
                self._record_coalesced_completion_siblings([event for event, _claim_id in sibling_claims])
