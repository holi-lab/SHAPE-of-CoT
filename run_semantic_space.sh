#!/bin/bash

# Semantic Space labeling using a local vLLM model.
# Structure mirrors run_stateless_hf_batch.sh.
#
# Run this script from the project root directory:
#   bash run_semantic_space_hf_all.sh

# Resolve the project root as the directory containing this script,
# so the script works regardless of where it is invoked from.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# List of directories to process.
# Each entry can be:
#   (a) a direct chunk directory containing JSON files,
#   (b) a parent directory whose immediate sub-directories are chunk dirs
#       (structure: <entry>/<chunk_dir>/), or
#   (c) a grandparent directory with structure <entry>/<dataset>/<model>/
#       where only the sub-directory matching HEURISTIC_MODEL is used.
DIRS=(
    "$SCRIPT_DIR/data/llm-label-heuristics/thinkarm"
)

# When DIRS contains a grandparent (case c), only process model sub-dirs
# whose basename matches this value. Leave empty to accept all model names.
HEURISTIC_MODEL="Qwen-Qwen3.5-27B"

# Expand DIRS into concrete chunk directories (one-shot, supports up to 3 levels).
EXPANDED_DIRS=()
for entry in "${DIRS[@]}"; do
    if [ ! -d "$entry" ]; then
        echo "Warning: DIRS entry not found, skipping: $entry"
        continue
    fi

    # Level 0: entry itself contains JSON files → direct chunk dir
    json_count=$(find "$entry" -maxdepth 1 -name "*.json" 2>/dev/null | wc -l)
    if [ "$json_count" -gt 0 ]; then
        EXPANDED_DIRS+=("$entry")
        continue
    fi

    # Level 1: check whether immediate sub-directories contain JSON files
    level1_has_json=false
    while IFS= read -r subdir; do
        sub_json=$(find "$subdir" -maxdepth 1 -name "*.json" 2>/dev/null | wc -l)
        if [ "$sub_json" -gt 0 ]; then
            level1_has_json=true
            break
        fi
    done < <(find "$entry" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | head -5)

    if $level1_has_json; then
        # <entry>/<chunk_dir>/ structure
        while IFS= read -r subdir; do
            EXPANDED_DIRS+=("$subdir")
        done < <(find "$entry" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | sort)
        continue
    fi

    # Level 2: <entry>/<dataset>/<model>/ structure — filter by HEURISTIC_MODEL
    while IFS= read -r model_dir; do
        model_name=$(basename "$model_dir")
        if [ -n "$HEURISTIC_MODEL" ] && [ "$model_name" != "$HEURISTIC_MODEL" ]; then
            continue
        fi
        EXPANDED_DIRS+=("$model_dir")
    done < <(find "$entry" -mindepth 2 -maxdepth 2 -type d 2>/dev/null | sort)
done

RAW_DIR="$SCRIPT_DIR/data/raw"
OUTPUT_DIR="$SCRIPT_DIR/data/llm-label-semantic-space/thinkarm"
PYTHON_SCRIPT="$SCRIPT_DIR/utils/method/label_semantic_space.py"

# Single Hugging Face model to run
MODEL="Qwen/Qwen3.5-27B"

# GPU device to use (comma-separated for multi-GPU, e.g. "0,1")
CUDA_VISIBLE_DEVICES=2

# vLLM engine options
GPU_MEMORY_UTILIZATION=0.9
MAX_MODEL_LEN=16384

# Disable DeepGEMM FP8 kernels — `deep_gemm` is not installed in the
# `vllm-dev` env, so the kernel warmup raises "DeepGEMM backend is not
# available or outdated". vLLM falls back to a non-DeepGEMM FP8 path.
export VLLM_USE_DEEP_GEMM=0
export VLLM_MOE_USE_DEEP_GEMM=0

# Sampling parameters
TEMPERATURE=0.7
MAX_TOKENS=4096
TOP_P=0.8
TOP_K=20
MIN_P=0.0
PRESENCE_PENALTY=1.5
REPETITION_PENALTY=1.0

LOG_DIR="$SCRIPT_DIR/logs/hf_semantic_space"

# Use the python already active in the current shell (e.g. after `conda activate vllm-dev`).
if command -v python &>/dev/null; then
    PYTHON_BIN="$(command -v python)"
else
    PYTHON_BIN="$(conda run -n vllm-dev which python 2>/dev/null || echo "")"
    if [ -z "$PYTHON_BIN" ]; then
        echo "Error: Could not find a usable python. Please activate the vllm-dev conda environment first."
        exit 1
    fi
fi

mkdir -p "$OUTPUT_DIR"
mkdir -p "$LOG_DIR"

echo "Starting vLLM Semantic Space labeling"
echo "Model: $MODEL"
echo "Using Python: $PYTHON_BIN"

if [ "${#EXPANDED_DIRS[@]}" -eq 0 ]; then
    echo "Error: No chunk directories found."
    exit 1
fi

echo "Found ${#EXPANDED_DIRS[@]} chunk director(y/ies) to process."

for dataset_path in "${EXPANDED_DIRS[@]}"; do
    if [ ! -d "$dataset_path" ]; then
        echo "Error: Directory not found: $dataset_path"
        continue
    fi

    dir_name=$(basename "$dataset_path")

    # When the chunk dir is a model-named subdirectory (case c),
    # the raw file corresponds to the parent (dataset) directory, not the model dir.
    # Detect this by checking whether the basename matches HEURISTIC_MODEL.
    if [ -n "$HEURISTIC_MODEL" ] && [ "$dir_name" = "$HEURISTIC_MODEL" ]; then
        dataset_name=$(basename "$(dirname "$dataset_path")")
    else
        dataset_name="$dir_name"
    fi

    # Raw file lookup (same fallback logic as run_stateless_hf_batch.sh)
    raw_base="${dataset_name%_chunked}"
    raw_file="$RAW_DIR/$raw_base.json"

    if [ ! -f "$raw_file" ]; then
        raw_file="$RAW_DIR/$dataset_name.json"
    fi

    if [ ! -f "$raw_file" ]; then
        found=$(find "$RAW_DIR" -name "${raw_base}.json" 2>/dev/null | head -1)
        [ -z "$found" ] && found=$(find "$RAW_DIR" -name "${dataset_name}.json" 2>/dev/null | head -1)
        raw_file="$found"
    fi

    if [ ! -f "$raw_file" ]; then
        echo "========================================================"
        echo "Warning: Raw file not found for $dataset_name. Skipping."
        echo "========================================================"
        continue
    fi

    sanitized_model="${MODEL//\//-}"
    log_file="$LOG_DIR/${sanitized_model}_${dataset_name}.log"

    echo "========================================================"
    echo "Processing: $MODEL on $dataset_name"
    echo "Raw file:   $raw_file"
    echo "Log:        $log_file"
    echo "========================================================"

    # Run sequentially to avoid GPU OOM (vLLM occupies the full GPU).
    CUDA_VISIBLE_DEVICES=$CUDA_VISIBLE_DEVICES $PYTHON_BIN "$PYTHON_SCRIPT" \
        --annotate_model "$MODEL" \
        --backend vllm \
        --chunk_dir "$dataset_path" \
        --raw_file "$raw_file" \
        --output_dir "$OUTPUT_DIR" \
        --gpu_memory_utilization "$GPU_MEMORY_UTILIZATION" \
        --max_model_len "$MAX_MODEL_LEN" \
        --temperature "$TEMPERATURE" \
        --max_tokens "$MAX_TOKENS" \
        --top_p "$TOP_P" \
        --top_k "$TOP_K" \
        --min_p "$MIN_P" \
        --presence_penalty "$PRESENCE_PENALTY" \
        --repetition_penalty "$REPETITION_PENALTY" \
        2>&1 | tee "$log_file"

    echo "Completed: $MODEL on $dataset_name"
done

echo "========================================================"
echo "All datasets processed. Results are in $OUTPUT_DIR"