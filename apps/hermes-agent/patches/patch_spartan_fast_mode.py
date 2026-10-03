#!/usr/bin/env python3
"""Permit upstream Hermes fast tiers on explicit models at Spartan Route only."""
from __future__ import annotations

import argparse
import ast
from pathlib import Path

MARKER = "# spartan-gate: trusted fast-mode route v1"
HELPER = '''# spartan-gate: trusted fast-mode route v1
def _spartan_fast_mode_route_supported(model_id, provider, base_url):
    # Exact endpoint and explicit model ownership; never trust an arbitrary proxy.
    route = str(base_url or "").rstrip("/")
    model = str(model_id or "").strip().lower()
    return (
        provider == "openai-api"
        and route == "http://spartan-route:18790/v1"
        and model.startswith(("codex/", "openai/"))
        and _is_openai_fast_model(model_id)
    )


'''
GUARD = '''    if _spartan_fast_mode_route_supported(model_id, provider, base_url):
        return True
'''


def patch_source(source: str) -> str:
    tree = ast.parse(source)
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_fast_mode_route_supported"]
    if len(nodes) != 1:
        raise ValueError("Hermes fast-mode route gate changed")
    node = nodes[0]
    if [a.arg for a in node.args.args] != ["model_id", "provider", "base_url"]:
        raise ValueError("Hermes fast-mode route signature changed")
    lines = source.splitlines(keepends=True)
    block = "".join(lines[node.lineno - 1:node.end_lineno])
    if MARKER in source:
        if source.count(HELPER) != 1 or block.count(GUARD) != 1:
            raise ValueError("Hermes fast-mode patch fingerprint changed")
        return source
    if "_spartan_fast_mode_route_supported" in source:
        raise ValueError("Hermes fast-mode helper conflicts with existing source")
    first = node.body[0]
    is_doc = isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str)
    insert_at = first.end_lineno if is_doc else first.lineno - 1
    result = "".join(lines[:node.lineno - 1]) + HELPER + "".join(lines[node.lineno - 1:insert_at]) + GUARD + "".join(lines[insert_at:])
    compile(result, "hermes_cli/models.py", "exec")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("/opt/hermes"))
    args = parser.parse_args()
    path = args.root / "hermes_cli/models.py"
    source = path.read_text()
    result = patch_source(source)
    if result != source:
        path.write_text(result)


if __name__ == "__main__":
    main()
