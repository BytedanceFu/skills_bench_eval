#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

START="${START:-1}"
END_TOTAL="${END_TOTAL:-69}"
BATCH_SIZE="${BATCH_SIZE:-3}"

BASE_URL="${OPENCLAW_BASE_URL:-http://127.0.0.1:18789}"
TOKEN="${OPENCLAW_GATEWAY_TOKEN:-}"
if [[ -z "${TOKEN}" ]]; then
  echo "OPENCLAW_GATEWAY_TOKEN is required"
  exit 1
fi

OUT_DIR="${OUT_DIR:-}"
if [[ -z "${OUT_DIR}" ]]; then
  if [[ -d "output" ]]; then
    OUT_DIR="output"
  elif [[ -d "bench_output" ]]; then
    OUT_DIR="bench_output"
  else
    OUT_DIR="output"
  fi
fi

task_name_by_index() {
  local idx="$1"
  uv run python - "$idx" <<'PY'
import sys
from skill_bench_eval import get_available_tasks

idx = int(sys.argv[1])
tasks = get_available_tasks()
if idx < 1 or idx > len(tasks):
    raise SystemExit(f"invalid task index: {idx} (available: 1..{len(tasks)})")
print(tasks[idx - 1].name)
PY
}

wait_for_result_json() {
  local task_name="$1"
  local timeout_s="${2:-60}"
  local end_ts="$(( $(date +%s) + timeout_s ))"
  local p="${OUT_DIR}/${task_name}/result.json"
  while [[ "$(date +%s)" -lt "${end_ts}" ]]; do
    if [[ -f "${p}" ]]; then
      return 0
    fi
    sleep 2
  done
  return 1
}

s="${START}"
while [[ "${s}" -le "${END_TOTAL}" ]]; do
  e="$(( s + BATCH_SIZE - 1 ))"
  if [[ "${e}" -gt "${END_TOTAL}" ]]; then
    e="${END_TOTAL}"
  fi

  end_task="$(task_name_by_index "${e}")"
  echo "=== Running tasks ${s}-${e} (end task: ${end_task}) ==="
  uv run skill_bench_eval.py run --start "${s}" --end "${e}" --token "${TOKEN}" --base-url "${BASE_URL}"

  if [[ -d "${OUT_DIR}" ]]; then
    du -sh "${OUT_DIR}" || true
  fi

  if wait_for_result_json "${end_task}" 60; then
    echo "=== Completed batch ${s}-${e} ==="
  else
    echo "=== Batch ${s}-${e} finished but ${OUT_DIR}/${end_task}/result.json not found ==="
    exit 2
  fi

  s="$(( e + 1 ))"
done

echo "=== Done: tasks ${START}-${END_TOTAL} ==="
