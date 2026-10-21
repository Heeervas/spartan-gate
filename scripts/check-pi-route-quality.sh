#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
assurance_dir="${BUILDER_ASSURANCE_DIR:-$HOME/.agents/skills/shared/builder-assurance}"
assurance_python="${BUILDER_ASSURANCE_PYTHON:-python3}"
baseline=75651cc0c5baa1db5a6523b790564988698f2a43
cd "$project_dir"
"$assurance_python" "$assurance_dir/scripts/validate_quality_policy.py" .agents/quality.yaml
"$assurance_python" "$assurance_dir/scripts/collect_oxlint_metrics.py" --repo . --base "$baseline" --scope all --oxlint ./node_modules/.bin/oxlint --output .agents/oxlint-metrics.json
"$assurance_python" "$assurance_dir/scripts/quality_report.py" --repo . --base "$baseline" --scope all --level top --config .agents/quality.yaml --format json --output .agents/pi-route-quality.json
