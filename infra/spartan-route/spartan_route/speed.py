"""Bounded passive service-tier telemetry; never routing or latency policy."""
from typing import Any


SERVICE_TIERS = frozenset({"auto", "default", "flex", "priority", "scale", "ultrafast"})


def service_tier(value: Any) -> str | None:
    # The OpenAI fast wire alias is priority; normalize telemetry only.
    if value == "fast":
        return "priority"
    # Unknown provider/client values must not become unbounded telemetry labels.
    return value if isinstance(value, str) and value in SERVICE_TIERS else None


def capture_service_tier(observation: Any, payload: Any, event: str | None = None) -> None:
    if not isinstance(payload, dict):
        return
    # Responses creation echoes are not evidence of the tier ultimately served.
    event = event or payload.get("type")
    if event is not None and event != "response.completed":
        return
    response = payload.get("response")
    source = response if isinstance(response, dict) else payload
    tier = service_tier(source.get("service_tier"))
    if tier is not None or event == "response.completed":
        observation.served_service_tier = tier


def speed_context(body: dict[str, Any], served: str | None) -> dict[str, str]:
    return {
        "requested": (service_tier(body["service_tier"]) or "unknown") if "service_tier" in body else "unspecified",
        "served": service_tier(served) or "unknown",
    }


def add_speed(metrics: dict[str, Any], context: dict[str, Any]) -> None:
    speed = context.get("speed")
    speed = speed if isinstance(speed, dict) else {}
    for side in ("requested", "served"):
        value = speed.get(side)
        tier = "unspecified" if side == "requested" and value == "unspecified" else service_tier(value) or "unknown"
        counts = metrics["speed"][side]
        counts[tier] = counts.get(tier, 0) + 1
