#!/usr/bin/env bash
set -euo pipefail
# Keep the public alias stable; one owner coordinates the Google consumers.
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$script_dir/google_auth.py" "$@"
