#!/bin/bash

# Configuration for Local Hugging Face Batch Processing
# Use this script to run labeling across multiple directories using local GPUs.

# List of directories containing chunked JSON files.
# Each entry can be either:
#   (a) a direct chunk directory (original behaviour), or
#   (b) a parent directory whose immediate sub-directories are chunk dirs.
# Sub-directories are expanded automatically at runtime.
DIRS=(
    "./data/content_unit/thinkarm"
)

# Expand any parent directory that contains only sub-directories (not JSON files directly).
# This allows entries like "./data/content_unit" to be treated as a collection of chunk dirs.
EXPANDED_DIRS=()
for entry in "${DIRS[@]}"; do
    if [ ! -d "$entry" ]; then
        EXPANDED_DIRS+=("$entry")
        continue
    fi
    # Check whether the directory itself contains JSON files
    json_count=$(find "$entry" -maxdepth 1 -name "*.json" | wc -l)
    if [ "$json_count" -gt 0 ]; then
        # Has JSON files directly → treat as a chunk dir
        EXPANDED_DIRS+=("$entry")
    else
        # No JSON files at top level → expand one level of sub-directories
        while IFS= read -r subdir; do
            EXPANDED_DIRS+=("$subdir")
        done < <(find "$entry" -mindepth 1 -maxdepth 1 -type d | sort)
    fi
done

# List of Hugging Face Model IDs
MODELS=(
    "Qwen/Qwen3.5-27B"
)

RAW_DIR="./data/raw"

# vLLM engine options
GPU_MEMORY_UTILIZATION=0.9
MAX_MODEL_LEN=16384

# vLLM stability flags:
# DeepGEMM (FP8 warmup) may be optional and can fail on some CUDA/vLLM combos.
# Disable by default for reliability; enable only if deep_gemm is known-good.
VLLM_USE_DEEP_GEMM_FLAG=0

# Sampling parameters
TEMPERATURE=0.7
MAX_TOKENS=4096
TOP_P=0.8
TOP_K=20
MIN_P=0.0
PRESENCE_PENALTY=1.5
REPETITION_PENALTY=1.0

OUTPUT_BASE_DIR="./data/llm-label-heuristics/thinkarm"
LOG_DIR="./logs/hf_stateless_heuristics/thinkarm"

# Use the environment with torch and transformers.
# Prefer the python already active in the current shell (e.g. after `conda activate vllm-dev`).
# Fall back to the vllm-dev env inside  the user's own miniconda installation.
if command -v python &>/dev/null; then
    PYTHON_BIN="$(command -v python)"
else
    PYTHON_BIN="$(conda run -n vllm-dev which python 2>/dev/null || echo "")"
    if [ -z "$PYTHON_BIN" ]; then
        echo "Error: Could not find a usable python. Please activate the vllm-dev conda environment first."
        exit 1
    fi
fi

mkdir -p "$LOG_DIR"
mkdir -p "$OUTPUT_BASE_DIR"

echo "Starting Sequential Hugging Face annotation jobs..."
echo "Using Python: $PYTHON_BIN"

# Make sure CUDA runtime libs bundled in the Python env are visible to vLLM.
if [ "$VLLM_USE_DEEP_GEMM_FLAG" = "0" ]; then
    export VLLM_USE_DEEP_GEMM=0
    export VLLM_USE_DEEP_GEMM_E8M0=0
fi

if [ -n "$PYTHON_BIN" ]; then
    CUDA_LIB_PATHS="$("$PYTHON_BIN" -c 'import glob, os, site
paths = []
for base in site.getsitepackages():
    paths.extend(glob.glob(os.path.join(base, "nvidia", "cu13", "lib")))
    paths.extend(glob.glob(os.path.join(base, "nvidia", "cuda_runtime", "lib")))
print(":".join([p for p in paths if os.path.isdir(p)]))')"
    if [ -n "$CUDA_LIB_PATHS" ]; then
        export LD_LIBRARY_PATH="$CUDA_LIB_PATHS:${LD_LIBRARY_PATH:-}"
    fi
fi

echo "VLLM_USE_DEEP_GEMM: ${VLLM_USE_DEEP_GEMM:-}"
echo "LD_LIBRARY_PATH (prefixed): ${LD_LIBRARY_PATH:-}"

for dir in "${EXPANDED_DIRS[@]}"; do
    if [ ! -d "$dir" ]; then
        echo "Error: Directory not found: $dir"
        continue
    fi

    base_name=$(basename "$dir")

    # Try to find corresponding raw file:
    #   1. <RAW_DIR>/<base_name minus _chunked>.json  (original behaviour)
    #   2. <RAW_DIR>/<base_name>.json                 (no _chunked suffix)
    #   3. Recursive search under RAW_DIR             (for sub-directory entries)
    raw_base="${base_name%_chunked}"
    raw_file="$RAW_DIR/$raw_base.json"

    if [ ! -f "$raw_file" ]; then
        raw_file="$RAW_DIR/$base_name.json"
    fi

    if [ ! -f "$raw_file" ]; then
        found=$(find "$RAW_DIR" -name "${raw_base}.json" | head -1)
        [ -z "$found" ] && found=$(find "$RAW_DIR" -name "${base_name}.json" | head -1)
        raw_file="$found"
    fi

    if [ ! -f "$raw_file" ]; then
        echo "Warning: Raw file not found for $base_name. Skipping."
        continue
    fi

    for model in "${MODELS[@]}"; do
        sanitized_model="${model//\//-}"
        log_file="$LOG_DIR/${sanitized_model}_${base_name}.log"
        
        echo "--------------------------------------------------------"
        echo "Processing: $model on $base_name"
        echo "Log: $log_file"
        
        # NOTE: We run these SEQUENTIALLY (without &) to avoid GPU OOM.
        # --vllm_batch: all chunks in a sample are sent to vLLM in one generate() call,
        # maximising GPU utilisation via continuous batching.
        CUDA_VISIBLE_DEVICES=0 $PYTHON_BIN utils/method/label_stateless.py \
            --annotate_model "$model" \
            --backend vllm \
            --vllm_batch \
            --chunk_dir "$dir" \
            --raw_file "$raw_file" \
            --output_dir "$OUTPUT_BASE_DIR" \
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
            
        echo "Finished $model on $base_name"
    done
done

echo "--------------------------------------------------------"
echo "All local HF batch jobs completed."