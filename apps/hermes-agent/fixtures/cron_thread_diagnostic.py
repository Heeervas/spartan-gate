"""Pinned native diagnostic prologue; delivery continuation excluded from fixture."""
def _prepare_target_delivery(
    job: dict, target: dict, *, adapters, loop, config, notify_delivery: bool, mirror_enabled: bool,
    mirror_text: str, delivery_errors: list,
) -> Optional[_TargetDelivery]:
    """Per-target prologue of ``_deliver_result``: origin/mirror/in_channel gates, transport
    resolution, continuable-thread open. None (error noted in ``delivery_errors``) if unservable."""
    from gateway.config import Platform
    platform_name = target["platform"]
    chat_id = target["chat_id"]
    thread_id = target.get("thread_id")

    origin = _resolve_origin(job) or {}
    origin_thread = origin.get("thread_id")
    if origin_thread and not thread_id:
        logger.warning(
            "Job '%s': origin has thread_id=%s but delivery target lost it (deliver=%s, target=%s)",
            job["id"], origin_thread, job.get("deliver", "local"), target)
    elif thread_id:
        logger.debug(
            "Job '%s': delivering to %s:%s thread_id=%s",
            job["id"], platform_name, chat_id, thread_id)

    return None
