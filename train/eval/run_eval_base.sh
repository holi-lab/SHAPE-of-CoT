#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

DATASET_PATH="${DATASET_PATH:-/path/to/json}"
BENCHMARK_NAME="${BENCHMARK_NAME:-$(basename "$DATASET_PATH" .json)}"
BASE_MODEL_PATH="${BASE_MODEL_PATH:-Qwen/Qwen3-1.7B-Base}"
BASE_STEP_NAME="${BASE_STEP_NAME:-global_step_0}"

TEMPERATURE="${TEMPERATURE:-1.0}"
TOP_P="${TOP_P:-1.0}"
MAX_NEW_TOKENS="${MAX_NEW_TOKENS:-4096}"
N_SAMPLES="${N_SAMPLES:-16}"
SEED="${SEED:-42}"
SAVE_PREDICTIONS="${SAVE_PREDICTIONS:-false}"
SAVE_STEP_RESULTS="${SAVE_STEP_RESULTS:-true}"

MODEL_NAME="$(basename "$BASE_MODEL_PATH")"
CKPT_NAME="${CKPT_NAME:-$MODEL_NAME}"
RUN_NAME="${RUN_NAME:-$CKPT_NAME}"
OUTPUT_ROOT="${OUTPUT_ROOT:-eval/results/${BENCHMARK_NAME}/}"

to_bool() {
  local v="${1,,}"
  [[ "$v" == "1" || "$v" == "true" || "$v" == "yes" || "$v" == "y" || "$v" == "on" ]]
}

CMD=(
  python eval/eval_benchmark.py
  --dataset-path "$DATASET_PATH"
  --benchmark-name "$BENCHMARK_NAME"
  --include-base-model
  --base-model-path "$BASE_MODEL_PATH"
  --base-step-name "$BASE_STEP_NAME"
  --temperature "$TEMPERATURE"
  --top-p "$TOP_P"
  --max-new-tokens "$MAX_NEW_TOKENS"
  --n-samples "$N_SAMPLES"
  --seed "$SEED"
  --output-root "$OUTPUT_ROOT"
  --run-name "$RUN_NAME"
  --no-latest-summary
  --show-progress
)

if [[ -n "${MODEL_NAME:-}" ]]; then
  CMD+=(--model-name "$MODEL_NAME")
fi
if [[ -n "${TRAIN_DATASET:-}" ]]; then
  CMD+=(--train-dataset "$TRAIN_DATASET")
fi

if to_bool "$SAVE_PREDICTIONS"; then
  CMD+=(--save-predictions)
fi
if to_bool "$SAVE_STEP_RESULTS"; then
  CMD+=(--save-step-results)
else
  CMD+=(--no-save-step-results)
fi

"${CMD[@]}" "$@"
