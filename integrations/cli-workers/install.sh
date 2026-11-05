#!/usr/bin/env bash
set -euo pipefail
task_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
task_project="${1:-$PWD}"
task_policy="${2:-workspace}"
task_python="${DSH_WORKERS_PYTHON:-python3}"
"$task_python" -m venv "$task_root/.venv"
"$task_root/.venv/bin/python" -m pip install --constraint "$task_root/requirements.lock" "$task_root"
"$task_root/.venv/bin/python" -m dsh_cli_workers configure \
  --workspace "$task_project" --output-dir "$task_root/local" --policy "$task_policy"
"$task_root/.venv/bin/python" -m dsh_cli_workers doctor --config "$task_root/local/workers.toml"
printf '\nLaunch Harness: bash "%s/local/launch.sh"\n' "$task_root"
