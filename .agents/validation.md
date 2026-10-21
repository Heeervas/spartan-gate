# Native external skill links validation

Scope: the external-skill sync build-time patch, its focused regression tests, and Dockerfile integration. TOP quality floor; immutable baseline `75651cc0c5baa1db5a6523b790564988698f2a43`. This scoped policy does not claim repository-wide enforcement.

The external canonical measurement dependency is builder-assurance v1.1.0, resolved at `$HOME/.agents/skills/shared/builder-assurance` after canonical activation. Before activation its unchanged measurement implementation resolves at `$HOME/.agents/skills/shared-builder-assurance`; use that existing path for preparation checks. Its schema is vendored unchanged. Run from the repository root after activation:

```sh
python3 "$HOME/.agents/skills/shared/builder-assurance/scripts/validate_quality_policy.py" .agents/quality.yaml
python3 "$HOME/.agents/skills/shared/builder-assurance/scripts/quality_report.py" --repo . --base 75651cc0c5baa1db5a6523b790564988698f2a43 --scope all --level top --config .agents/quality.yaml --format json
python3 -m unittest discover -s apps/hermes-agent -p test_external_skill_links_patch.py
git diff --check
```

Behavior tests require `HERMES_SYNC_SOURCE` pointing to the unmodified supported upstream `tools/skills_sync.py`, and a native Hermes checkout on `PYTHONPATH`. They run actual patched upstream code with disposable profile, bundled and external trees; no live home writes. The patch CLI defaults to `/opt/hermes/tools/skills_sync.py` and accepts an explicit disposable target path. Build/recreate acceptance remains separate.
