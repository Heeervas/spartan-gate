"""Persisted context metadata aliases. Aliases never change upstream model IDs."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace

from .config import ModelEntry, SpartanRouteConfig
from .util import stable_json

VARIANT_PATTERN = re.compile(r"-[0-9]+(?:k)?context$")
VARIANT_LIKE_PATTERN = re.compile(r"-[^/]*context$")
PROVIDERS = frozenset({"codex", "openai", "openrouter", "ollama"})
WARNING = "Stored model variants could not be loaded; managed variants are disabled."


def variant_id(model: str, context: int) -> str:
    suffix = f"{context // 1000}kcontext" if context % 1000 == 0 else f"{context}context"
    return f"{model}-{suffix}"


def base_eligible(entry: ModelEntry) -> bool:
    return bool(entry.enabled and entry.tool_capable and entry.provider in PROVIDERS
                and entry.id.removeprefix("custom-1/").removeprefix("spartan-route/") not in {"auto", "clawroute/auto"}
                and not VARIANT_LIKE_PATTERN.search(entry.id)
                and not re.search(r"(?:^|[/.-])(?:image|realtime)(?:$|[/.-])", entry.id))


def parse_rows(rows: object) -> tuple[tuple[str, int], ...]:
    if not isinstance(rows, list):
        raise ValueError("variants must be a list")
    result = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"model", "contextWindow"}:
            raise ValueError("each variant must contain only model and contextWindow")
        model, context = row["model"], row["contextWindow"]
        if (not isinstance(model, str) or not model or model != model.strip() or len(model) > 256
                or type(context) is not int or not 1 <= context <= 2_000_000):
            raise ValueError("invalid model or context window")
        if (model, context) in seen:
            raise ValueError("duplicate model variant")
        seen.add((model, context))
        result.append((model, context))
    return tuple(sorted(result))


@dataclass(frozen=True)
class VariantPolicy:
    # None means the user has never saved; () is an authoritative empty list.
    rows: tuple[tuple[str, int], ...] | None = None
    updated_at: str | None = None
    warning: str | None = None

    @classmethod
    def load(cls, store) -> VariantPolicy:
        try:
            document = store.model_variant_config()
            if document is None:
                return cls()
            if (not isinstance(document, dict) or set(document) != {"version", "variants", "updatedAt"}
                    or type(document["version"]) is not int or document["version"] != 1
                    or not isinstance(document["updatedAt"], str) or not document["updatedAt"]):
                raise ValueError("invalid stored variant document")
            rows = parse_rows(document["variants"])
            if any(VARIANT_LIKE_PATTERN.search(model) or model in {"auto", "clawroute/auto"} for model, _ in rows):
                raise ValueError("invalid stored variant base")
            return cls(rows, document["updatedAt"])
        except Exception:
            return cls((), warning=WARNING)

    def effective_rows(self, config: SpartanRouteConfig) -> tuple[tuple[str, int], ...]:
        if self.rows is not None:
            return self.rows
        return tuple(sorted((entry.id, 282_000) for entry in config.models.values()
                            if base_eligible(entry) and entry.max_context >= 282_000))

    def snapshot(self, config: SpartanRouteConfig) -> tuple[list[dict], dict[str, ModelEntry]]:
        rows, active = [], {}
        for model, context in self.effective_rows(config):
            alias = variant_id(model, context)
            entry = config.models.get(model)
            reason = ("model_missing" if entry is None else
                      "model_not_eligible" if not base_eligible(entry) else
                      "context_exceeds_maximum" if context > entry.max_context else
                      "model_id_collision" if alias in config.models else None)
            rows.append({"model": model, "contextWindow": context, "id": alias,
                         "available": reason is None, "unavailableReason": reason})
            if reason is None:
                active[alias] = replace(entry, id=alias, max_context=context)
        return rows, active

    def validate(self, config: SpartanRouteConfig, body: dict) -> tuple[tuple[str, int], ...]:
        if set(body) != {"variants"}:
            raise ValueError("configuration must contain only variants")
        rows = parse_rows(body["variants"])
        existing = set(self.effective_rows(config))
        for model, context in rows:
            entry = config.models.get(model)
            # Unchanged unavailable rows can be retained while editing others.
            if (model, context) in existing:
                continue
            if (entry is None or not base_eligible(entry) or context > entry.max_context
                    or variant_id(model, context) in config.models):
                raise ValueError("variant base or context window is not eligible")
        return rows

    def etag(self, config: SpartanRouteConfig) -> str:
        rows, _ = self.snapshot(config)
        catalog = [(e.id, e.provider, e.max_context, base_eligible(e)) for e in sorted(config.models.values(), key=lambda e: e.id)]
        digest = hashlib.sha256(stable_json({"rows": rows, "catalog": catalog,
            "configured": self.rows is not None, "updatedAt": self.updated_at, "warning": self.warning}).encode()).hexdigest()
        return f'"{digest}"'
