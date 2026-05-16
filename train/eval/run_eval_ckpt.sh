#!/usr/bin/env bash
set -euo pipefail

DATASET_PATH="${DATASET_PATH:-/path/to/json}"
BENCHMARK_NAME="${BENCHMARK_NAME:-$(basename "$DATASET_PATH" .json)}"
CKPT_ROOT="${CKPT_ROOT:-/path/to/ckpt}"
CKPT_NAME="$(basename "$CKPT_ROOT")"

TEMPERATURE="${TEMPERATURE:-1.0}"
TOP_P="${TOP_P:-1.0}"
MAX_NEW_TOKENS="${MAX_NEW_TOKENS:-4096}"
N_SAMPLES="${N_SAMPLES:-64}"
SEED="${SEED:-42}"

STEPS="${STEPS:-25}"
MERGED_MODEL_ROOT="${MERGED_MODEL_ROOT:-ckpts/merged/${CKPT_NAME}}"
SAVE_PREDICTIONS="${SAVE_PREDICTIONS:-false}"
SAVE_STEP_RESULTS="${SAVE_STEP_RESULTS:-true}"

OUTPUT_ROOT="${OUTPUT_ROOT:-eval/results/${BENCHMARK_NAME}}"

to_bool() {
  local v="${1,,}"
  [[ "$v" == "1" || "$v" == "true" || "$v" == "yes" || "$v" == "y" || "$v" == "on" ]]
}

# Auto-detect ThinkARM raw datasets (e.g., ThinkARM/data/raw/<model>.json):
# their problem text lives under "Instruction" / "Correct Answer" and lacks the
# boxed-answer instruction the math scorer expects. Override via THINKARM=true/false.
if [[ -z "${THINKARM:-}" && "$DATASET_PATH" == *"ThinkARM/data"* ]]; then
  THINKARM="true"
fi
if to_bool "${THINKARM:-false}"; then
  : "${PROMPT_KEY:=Instruction}"
  : "${ANSWER_KEY:=Correct Answer}"
  : "${PROMPT_SUFFIX:=$'\n\nPlease reason step by step, and put your final answer within \\boxed{}.'}"
fi

CMD=(
  python eval/eval_benchmark.py
  --dataset-path "$DATASET_PATH"
  --benchmark-name "$BENCHMARK_NAME"
  --ckpt-root "$CKPT_ROOT"
  --temperature "$TEMPERATURE"
  --top-p "$TOP_P"
  --max-new-tokens "$MAX_NEW_TOKENS"
  --n-samples "$N_SAMPLES"
  --seed "$SEED"
  --merged-model-root "$MERGED_MODEL_ROOT"
  --output-root "$OUTPUT_ROOT"
  --no-latest-summary
  --auto-merge-fsdp
  --show-progress
)

if [[ -n "${MODEL_NAME:-}" ]]; then
  CMD+=(--model-name "$MODEL_NAME")
fi
if [[ -n "${TRAIN_DATASET:-}" ]]; then
  CMD+=(--train-dataset "$TRAIN_DATASET")
fi
if [[ -n "${RUN_NAME:-}" ]]; then
  CMD+=(--run-name "$RUN_NAME")
fi

if [[ -n "${PROMPT_KEY:-}" ]]; then
  CMD+=(--prompt-key "$PROMPT_KEY")
fi
if [[ -n "${ANSWER_KEY:-}" ]]; then
  CMD+=(--answer-key "$ANSWER_KEY")
fi
if [[ -n "${PROMPT_SUFFIX:-}" ]]; then
  CMD+=(--prompt-suffix "$PROMPT_SUFFIX")
fi

if [[ -n "$STEPS" ]]; then
  read -r -a STEP_ARR <<< "$STEPS"
  CMD+=(--steps "${STEP_ARR[@]}")
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
