from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .util import parse_bool, parse_int

AUTO_ROUTING_TIERS = (
    "heartbeat",
    "simple",
    "moderate",
    "complex",
)
TIERS = (*AUTO_ROUTING_TIERS,
    "frontier-sonnet",
    "frontier-opus",
)


@dataclass(frozen=True)
class ModelEntry:
    id: str
    provider: str
    input_cost_per_1m: float = 0.0
    output_cost_per_1m: float = 0.0
    max_context: int = 128_000
    tool_capable: bool = True
    multimodal: bool = False
    enabled: bool = True


@dataclass(frozen=True)
class SpartanRouteConfig:
    host: str
    port: int
    token: str | None
    enabled: bool
    dry_run: bool
    debug: bool
    log_content: bool
    db_path: str
    config_dir: Path
    web_dir: Path
    provider_profile: str | None
    baseline_model: str
    tier_models: dict[str, dict[str, str]]
    models: dict[str, ModelEntry]
    telemetry_queue_capacity: int = 1024
    telemetry_retention_batch_size: int = 256
    telemetry_shutdown_timeout_ms: int = 5000
    codex_transport: str = "http"

    def api_key(self, provider: str) -> str | None:
        keys = {
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "google": "GOOGLE_API_KEY",
            "deepseek": "DEEPSEEK_API_KEY",
            "openrouter": "OPENROUTER_API_KEY",
            "x-ai": "XAI_API_KEY",
            "stepfun": "STEPFUN_API_KEY",
        }
        name = keys.get(provider)
        value = os.environ.get(name or "")
        return value.strip() if value and value.strip() else None


DEFAULT_TIER_MODELS: dict[str, dict[str, str]] = {
    "heartbeat": {"primary": "codex/gpt-5.4-mini", "fallback": "codex/gpt-5.4-mini"},
    "simple": {"primary": "codex/gpt-5.4-mini", "fallback": "codex/gpt-5.4-mini"},
    "moderate": {"primary": "codex/gpt-5.5", "fallback": "codex/gpt-5.4"},
    "complex": {"primary": "codex/gpt-5.5", "fallback": "codex/gpt-5.4"},
    "frontier-sonnet": {"primary": "codex/gpt-5.5", "fallback": "codex/gpt-5.4"},
    "frontier-opus": {"primary": "codex/gpt-5.5", "fallback": "codex/gpt-5.4"},
}


DEFAULT_MODELS: dict[str, ModelEntry] = {
    "codex/gpt-6-astra": ModelEntry("codex/gpt-6-astra", "codex", max_context=872_000, tool_capable=True, multimodal=True),
    "codex/gpt-6-sol": ModelEntry("codex/gpt-6-sol", "codex", max_context=872_000, tool_capable=True, multimodal=True),
    "codex/gpt-6-luna": ModelEntry("codex/gpt-6-luna", "codex", max_context=872_000, tool_capable=True, multimodal=True),
    "codex/gpt-5.6": ModelEntry("codex/gpt-5.6", "codex", max_context=1_050_000, tool_capable=True, multimodal=True),
    "codex/gpt-5.6-sol": ModelEntry("codex/gpt-5.6-sol", "codex", max_context=1_050_000, tool_capable=True, multimodal=True),
    "codex/gpt-5.6-terra": ModelEntry("codex/gpt-5.6-terra", "codex", max_context=1_050_000, tool_capable=True, multimodal=True),
    "codex/gpt-5.6-luna": ModelEntry("codex/gpt-5.6-luna", "codex", max_context=1_050_000, tool_capable=True, multimodal=True),
    "codex/gpt-5.5": ModelEntry("codex/gpt-5.5", "codex", max_context=1_050_000, tool_capable=True, multimodal=True),
    "codex/gpt-5.4": ModelEntry("codex/gpt-5.4", "codex", max_context=1_050_000, tool_capable=True, multimodal=True),
    "codex/gpt-5.4-mini": ModelEntry("codex/gpt-5.4-mini", "codex", max_context=400_000, tool_capable=True, multimodal=True),
    "openai/gpt-image-2": ModelEntry("openai/gpt-image-2", "openai", max_context=32_000, tool_capable=False, multimodal=True),
    "gpt-image-2": ModelEntry("gpt-image-2", "openai", max_context=32_000, tool_capable=False, multimodal=True),
    "openrouter/anthropic/claude-sonnet-4.6": ModelEntry("openrouter/anthropic/claude-sonnet-4.6", "openrouter"),
}


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        parsed = json.load(handle)
    if not isinstance(parsed, dict):
        raise ValueError(f"Config file must contain a JSON object: {path}")
    return parsed


def _merge_tiers(*sources: dict[str, Any]) -> dict[str, dict[str, str]]:
    merged = {tier: dict(DEFAULT_TIER_MODELS[tier]) for tier in TIERS}
    for source in sources:
        models = source.get("models")
        if not isinstance(models, dict):
            continue
        for tier in TIERS:
            value = models.get(tier)
            if isinstance(value, dict):
                primary = value.get("primary")
                fallback = value.get("fallback")
                if isinstance(primary, str) and primary:
                    merged[tier]["primary"] = primary
                if isinstance(fallback, str) and fallback:
                    merged[tier]["fallback"] = fallback
    return merged


def _provider_for_model(model_id: str) -> str:
    if model_id.startswith("codex/"):
        return "codex"
    if model_id.startswith("openai/") or model_id.startswith("gpt-"):
        return "openai"
    if model_id.startswith("openrouter/"):
        return "openrouter"
    if model_id.startswith("anthropic/"):
        return "anthropic"
    if model_id.startswith("google/"):
        return "google"
    if model_id.startswith("deepseek/"):
        return "deepseek"
    if model_id.startswith("x-ai/"):
        return "x-ai"
    if model_id.startswith("stepfun/"):
        return "stepfun"
    if model_id.startswith("ollama/"):
        return "ollama"
    return "openai"


def _build_model_catalog(tier_models: dict[str, dict[str, str]], baseline_model: str) -> dict[str, ModelEntry]:
    models = dict(DEFAULT_MODELS)
    for model_id in {baseline_model, *(value for row in tier_models.values() for value in row.values())}:
        if model_id not in models:
            models[model_id] = ModelEntry(model_id, _provider_for_model(model_id))
    return models


def load_config(env: dict[str, str] | None = None) -> SpartanRouteConfig:
    env = env or os.environ
    transport = env.get("SPARTAN_ROUTE_CODEX_TRANSPORT", "http").strip().lower()
    if transport not in {"http", "auto", "websocket"}:
        raise ValueError("SPARTAN_ROUTE_CODEX_TRANSPORT must be http, auto, or websocket")
    config_dir = Path(env.get("SPARTAN_ROUTE_CONFIG_DIR") or env.get("CLAWROUTE_CONFIG_DIR") or "/app/config")
    if not config_dir.exists():
        config_dir = Path("config/clawroute")
    web_dir = Path(env.get("SPARTAN_ROUTE_WEB_DIR") or Path(__file__).resolve().parents[1] / "web")

    default_json = _load_json(config_dir / "default.json")
    user_json = _load_json(config_dir / "clawroute.json")
    provider_profile = (
        env.get("CLAWROUTE_PROVIDER")
        or user_json.get("providerProfile")
        or default_json.get("providerProfile")
    )
    provider_profile = str(provider_profile) if provider_profile else None
    profile_json = _load_json(config_dir / "providers" / f"{provider_profile}.json") if provider_profile else {}

    tier_models = _merge_tiers(default_json, profile_json, user_json)
    baseline_model = str(
        env.get("CLAWROUTE_BASELINE_MODEL")
        or user_json.get("baselineModel")
        or profile_json.get("baselineModel")
        or default_json.get("baselineModel")
        or "codex/gpt-5.5"
    )
    db_path = str(
        env.get("SPARTAN_ROUTE_DB_PATH")
        or (default_json.get("logging") if isinstance(default_json.get("logging"), dict) else {}).get("dbPath")
        or "./data/spartan-route.db"
    )
    if db_path.endswith("clawroute.db") and not env.get("SPARTAN_ROUTE_DB_PATH"):
        db_path = db_path.replace("clawroute.db", "spartan-route.db")

    return SpartanRouteConfig(
        host=env.get("CLAWROUTE_HOST", "0.0.0.0"),
        port=parse_int(env.get("CLAWROUTE_PORT"), 18790, 1, 65535),
        token=(env.get("CLAWROUTE_TOKEN") or "").strip() or None,
        enabled=parse_bool(env.get("CLAWROUTE_ENABLED"), bool(default_json.get("enabled", True))),
        dry_run=parse_bool(env.get("CLAWROUTE_DRY_RUN"), bool(default_json.get("dryRun", False))),
        debug=parse_bool(env.get("CLAWROUTE_DEBUG"), False),
        log_content=parse_bool(env.get("CLAWROUTE_LOG_CONTENT"), False),
        db_path=db_path,
        config_dir=config_dir,
        web_dir=web_dir,
        provider_profile=provider_profile,
        baseline_model=baseline_model,
        tier_models=tier_models,
        models=_build_model_catalog(tier_models, baseline_model),
        codex_transport=transport,
        telemetry_queue_capacity=parse_int(
            env.get("SPARTAN_ROUTE_TELEMETRY_QUEUE_CAPACITY"),
            1024,
            1,
            8192,
        ),
        telemetry_retention_batch_size=parse_int(
            env.get("SPARTAN_ROUTE_TELEMETRY_RETENTION_BATCH_SIZE"),
            256,
            1,
            1024,
        ),
        telemetry_shutdown_timeout_ms=parse_int(
            env.get("SPARTAN_ROUTE_TELEMETRY_SHUTDOWN_TIMEOUT_MS"), 5000, 100, 60_000
        ),
    )
