#!/usr/bin/env bash
# Bash 전용(배열 등). sh/dash로 호출되면 bash로 다시 실행합니다.
if [ -z "${BASH_VERSION:-}" ]; then
    exec /usr/bin/env bash "$0" "$@"
fi

# Configuration for Batch Preprocessing
# Use this script to run run_preprocessing.py across multiple folders sequentially.

# List of folders containing files to preprocess
FOLDERS=(
    "./data/raw/thinkarm"
    # "./data/raw/week1"
    # "./data/raw/week2"
)

# File extensions to look for (comma-separated)
EXT="*.json,*.jsonl"

# Output directories
LABEL_DIR="./data/content_unit"
RAW_DIR="./data/raw"

# Inference backend: "openrouter" or "vllm"
BACKEND="vllm"

# Model to use for LLM chunking
MODEL="Qwen/Qwen3.5-27B"

# Concurrency
WORKERS=10

# Flags (set to "--flag-name" to enable, or "" to disable)
SKIP_LLM=""        # "--skip-llm"
RAW_ONLY=""        # "--raw-only"
ALL_TRIALS="--all-trials"      # "--all-trials"
RESUME="--resume"  # "--resume" or ""

# vLLM-specific options (only used when BACKEND=vllm)
GPU_MEMORY_UTILIZATION=0.9
MAX_MODEL_LEN=16384
TEMPERATURE=0.7
MAX_TOKENS=4096
TOP_P=0.8
TOP_K=20
MIN_P=0.0
PRESENCE_PENALTY=1.5
REPETITION_PENALTY=1.0
CUDA_DEVICE=1

# Python binary
if command -v python &>/dev/null; then
    PYTHON_BIN="$(command -v python)"
else
    PYTHON_BIN="$(conda run -n vllm-dev which python 2>/dev/null || echo "")"
    if [ -z "$PYTHON_BIN" ]; then
        echo "Error: Could not find a usable python. Please activate the vllm-dev conda environment first."
        exit 1
    fi
fi

# Log directory
LOG_DIR="./logs/preprocessing_batch"

# -----------------------------------------------------------------------

mkdir -p "$LOG_DIR"
mkdir -p "$LABEL_DIR"

echo "Starting Sequential Preprocessing jobs..."
echo "Using Python: $PYTHON_BIN"
echo "Backend: $BACKEND"
echo "Model: $MODEL"

for folder in "${FOLDERS[@]}"; do
    if [ ! -d "$folder" ]; then
        echo "Error: Folder not found: $folder"
        continue
    fi

    base_name=$(basename "$folder")
    log_file="$LOG_DIR/${base_name}.log"

    echo "--------------------------------------------------------"
    echo "Processing folder: $folder"
    echo "Log: $log_file"

    CMD="$PYTHON_BIN utils/content_unit/run_preprocessing.py \
        --folder \"$folder\" \
        --ext \"$EXT\" \
        --label-dir \"$LABEL_DIR\" \
        --raw-dir \"$RAW_DIR\" \
        --backend \"$BACKEND\" \
        --model \"$MODEL\" \
        --workers $WORKERS" 

    # Append optional flags
    [ -n "$SKIP_LLM" ]    && CMD="$CMD $SKIP_LLM"
    [ -n "$RAW_ONLY" ]    && CMD="$CMD $RAW_ONLY"
    [ -n "$ALL_TRIALS" ]  && CMD="$CMD $ALL_TRIALS"
    [ -n "$RESUME" ]      && CMD="$CMD $RESUME"

    # Append vLLM-specific options
    if [ "$BACKEND" = "vllm" ]; then
        CMD="$CMD \
            --gpu-memory-utilization $GPU_MEMORY_UTILIZATION \
            --max-model-len $MAX_MODEL_LEN \
            --temperature $TEMPERATURE \
            --max-tokens $MAX_TOKENS \
            --top-p $TOP_P \
            --top-k $TOP_K \
            --min-p $MIN_P \
            --presence-penalty $PRESENCE_PENALTY \
            --repetition-penalty $REPETITION_PENALTY"
        CMD="CUDA_VISIBLE_DEVICES=$CUDA_DEVICE $CMD"
    fi

    eval "$CMD" 2>&1 | tee "$log_file"

    echo "Finished folder: $folder"
done

echo "--------------------------------------------------------"
echo "All preprocessing jobs completed."
