"""Persisted, named context metadata aliases, independent of upstream IDs."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace

from .config import ModelEntry, SpartanRouteConfig
from .util import stable_json

VARIANT_LIKE_PATTERN = re.compile(r"-[^/]*context$")
PROVIDERS = frozenset({"codex", "openai", "openrouter", "ollama"})
WARNING = "Stored model variants could not be loaded; managed variants are disabled."
IDENTIFIER = re.compile(r"[A-Za-z0-9._:/-]{1,272}", re.ASCII)
SPECIAL_MODEL = re.compile(r"(?:^|[/.-])(?:image|realtime)(?:$|[/.-])")
# Rows are (stable alias ID, upstream base model ID, context metadata).
VariantRow = tuple[str, str, int]


def variant_id(model: str, context: int) -> str:
    suffix = f"{context // 1000}kcontext" if context % 1000 == 0 else f"{context}context"
    return f"{model}-{suffix}"


def valid_alias(alias: object) -> bool:
    return bool(isinstance(alias, str) and IDENTIFIER.fullmatch(alias)
                and not alias.startswith(("/", "custom-1/", "spartan-route/"))
                and not alias.endswith("/") and "//" not in alias
                and not any(part in {".", ".."} for part in alias.split("/"))
                and alias not in {"auto", "clawroute/auto"}
                and not SPECIAL_MODEL.search(alias))


def valid_base(model: object) -> bool:
    return bool(valid_alias(model) and len(model) <= 256 and not VARIANT_LIKE_PATTERN.search(model))


def base_eligible(entry: ModelEntry) -> bool:
    return bool(entry.enabled and entry.tool_capable and entry.provider in PROVIDERS and valid_base(entry.id))


def parse_rows(rows: object, *, version: int | None = None) -> tuple[VariantRow, ...]:
    if not isinstance(rows, list):
        raise ValueError("variants must be a list")
    result = []
    seen = set()
    for row in rows:
        allowed = {"model", "contextWindow"}
        if version == 2:
            allowed.add("id")
        if (not isinstance(row, dict)
                or (set(row) != allowed if version is not None else set(row) not in (allowed, allowed | {"id"}))):
            raise ValueError("each variant must contain model, contextWindow and optional id")
        model, context = row["model"], row["contextWindow"]
        if (not valid_base(model) or type(context) is not int or not 1 <= context <= 2_000_000):
            raise ValueError("invalid model or context window")
        alias = row.get("id", variant_id(model, context))
        if not valid_alias(alias):
            raise ValueError("invalid or reserved variant id")
        if alias in seen:
            raise ValueError("duplicate model variant id")
        seen.add(alias)
        result.append((alias, model, context))
    return tuple(sorted(result))


class VariantCatalog(dict[str, ModelEntry]):
    """One derived catalog carries metadata, exact base resolution, and denials."""
    def __init__(self, entries: dict[str, ModelEntry], bases: dict[str, str], denied: frozenset[str], *, deny_unknown: bool = False):
        super().__init__(entries)
        self.bases = bases
        self.denied = denied
        self.deny_unknown = deny_unknown


@dataclass(frozen=True)
class VariantPolicy:
    # None means the user has never saved; () is an authoritative empty list.
    rows: tuple[VariantRow, ...] | None = None
    updated_at: str | None = None
    warning: str | None = None
    retired_ids: frozenset[str] = frozenset()

    @classmethod
    def load(cls, store) -> VariantPolicy:
        document = None
        try:
            document = store.model_variant_config()
            if document is None:
                return cls()
            version = document.get("version")
            keys = {"version", "variants", "updatedAt"} | ({"retiredIds"} if version == 2 else set())
            if (set(document) != keys or type(version) is not int or version not in {1, 2}
                    or not isinstance(document["updatedAt"], str) or not document["updatedAt"]):
                raise ValueError("invalid stored variant document")
            rows = parse_rows(document["variants"], version=version)
            retired = document.get("retiredIds", [])
            if (not isinstance(retired, list) or not all(valid_alias(alias) for alias in retired)
                    or len(set(retired)) != len(retired)
                    or set(retired) & {alias for alias, _, _ in rows}):
                raise ValueError("invalid retired variant ids")
            return cls(rows, document["updatedAt"], retired_ids=frozenset(retired))
        except Exception:
            # Recover syntactically safe names only for denial; never activate
            # any part of a corrupt document or regenerate initial defaults.
            denied = set()
            if isinstance(document, dict):
                for row in document.get("variants", []) if isinstance(document.get("variants"), list) else []:
                    if isinstance(row, dict) and valid_alias(row.get("id")):
                        denied.add(row["id"])
                retired = document.get("retiredIds", [])
                if isinstance(retired, list):
                    denied.update(alias for alias in retired if valid_alias(alias))
            return cls((), warning=WARNING, retired_ids=frozenset(denied))

    def effective_rows(self, config: SpartanRouteConfig) -> tuple[VariantRow, ...]:
        if self.rows is not None:
            return self.rows
        return tuple(sorted((variant_id(entry.id, 282_000), entry.id, 282_000)
                            for entry in config.models.values()
                            if base_eligible(entry) and entry.max_context >= 282_000))

    def snapshot(self, config: SpartanRouteConfig, *, reserved_ids: frozenset[str] = frozenset()) -> tuple[list[dict], VariantCatalog]:
        rows, active, bases = [], {}, {}
        configured = self.effective_rows(config)
        for alias, model, context in configured:
            entry = config.models.get(model)
            reason = ("model_missing" if entry is None else
                      "model_not_eligible" if not base_eligible(entry) else
                      "context_exceeds_maximum" if context > entry.max_context else
                      "model_id_collision" if alias in config.models else
                      "model_id_reserved" if alias in reserved_ids else None)
            rows.append({"model": model, "contextWindow": context, "id": alias,
                         "available": reason is None, "unavailableReason": reason})
            if reason is None:
                active[alias] = replace(entry, id=alias, max_context=context)
                bases[alias] = model
        denied = self.retired_ids | {alias for alias, _, _ in configured}
        return rows, VariantCatalog(active, bases, frozenset(denied), deny_unknown=self.warning is not None)

    def validate(self, config: SpartanRouteConfig, body: dict, *, reserved_ids: frozenset[str] = frozenset()) -> tuple[VariantRow, ...]:
        if set(body) != {"variants"}:
            raise ValueError("configuration must contain only variants")
        rows = parse_rows(body["variants"])
        existing = set(self.effective_rows(config))
        for alias, model, context in rows:
            entry = config.models.get(model)
            if alias in reserved_ids:
                raise ValueError("variant id is reserved")
            # Only the entire unchanged unavailable row can be retained.
            if (alias, model, context) in existing:
                continue
            if (entry is None or not base_eligible(entry) or context > entry.max_context or alias in config.models):
                raise ValueError("variant base, id or context window is not eligible")
        return rows

    def retirement_after(self, config: SpartanRouteConfig, rows: tuple[VariantRow, ...]) -> frozenset[str]:
        previous = {alias for alias, _, _ in self.effective_rows(config)}
        current = {alias for alias, _, _ in rows}
        return frozenset((self.retired_ids | previous) - current)

    def etag(self, config: SpartanRouteConfig, *, reserved_ids: frozenset[str] = frozenset()) -> str:
        rows, _ = self.snapshot(config, reserved_ids=reserved_ids)
        catalog = [(e.id, e.provider, e.max_context, base_eligible(e)) for e in sorted(config.models.values(), key=lambda e: e.id)]
        digest = hashlib.sha256(stable_json({"rows": rows, "catalog": catalog,
            "reservedIds": sorted(reserved_ids), "retiredIds": sorted(self.retired_ids), "configured": self.rows is not None,
            "updatedAt": self.updated_at, "warning": self.warning}).encode()).hexdigest()
        return f'"{digest}"'
