#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
assurance_dir="${BUILDER_ASSURANCE_DIR:-$HOME/.agents/skills/shared/builder-assurance}"
default_python=python3
default_env=
if [[ -x "$project_dir/.venv/quality/bin/python" ]]; then
  default_env=.venv/quality
elif [[ -x "$project_dir/.agents/quality-venv/bin/python" ]]; then
  # Transitional fallback until the controller migrates this checkout's environment.
  default_env=.agents/quality-venv
fi
if [[ -n "$default_env" ]]; then
  if [[ -z "${BUILDER_ASSURANCE_PYTHON:-}" ]]; then
    for path in "$project_dir/${default_env%/*}" "$project_dir/$default_env"; do
      [[ ! -L "$path" ]] || { printf '%s\n' "Refusing shared/symlinked $path" >&2; exit 1; }
    done
  fi
  default_python="$project_dir/$default_env/bin/python"
fi
assurance_python="${BUILDER_ASSURANCE_PYTHON:-$default_python}"
baseline=75651cc0c5baa1db5a6523b790564988698f2a43
cd "$project_dir"
"$assurance_python" "$assurance_dir/scripts/validate_quality_policy.py" .agents/quality.yaml
"$assurance_python" "$assurance_dir/scripts/collect_oxlint_metrics.py" --repo . --base "$baseline" --scope all --oxlint ./node_modules/.bin/oxlint --output .agents/oxlint-metrics.json
"$assurance_python" "$assurance_dir/scripts/quality_report.py" --repo . --base "$baseline" --scope all --level top --config .agents/quality.yaml --format json --output .agents/pi-route-quality.json
