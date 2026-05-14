#!/usr/bin/env bash

# Setup script for latest vLLM "nightly" on this system.
# Strategy:
# 1) Create/activate a Conda env
# 2) Install a CUDA-matched PyTorch wheel
# 3) Install vLLM nightly wheels (fast, preferred)
# 4) If wheels fail, optionally fall back to building from source (slow)

set -eo pipefail

ENV_NAME="${ENV_NAME:-vllm-dev}"
PYTHON_VERSION="${PYTHON_VERSION:-3.10}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="${VENV_DIR:-.venv-vllm}"

# CUDA variant candidates to try for vLLM nightly wheels / PyTorch.
# Note: vLLM cu126 wheels may be unavailable; cu130/cpu are common fallbacks.
CUDA_VARIANTS_DEFAULT=("cu126" "cu128" "cu130" "cu129" "cpu")

VLLM_NIGHTLY_BASE_URL="${VLLM_NIGHTLY_BASE_URL:-https://wheels.vllm.ai/nightly}"
VLLM_GIT_URL="${VLLM_GIT_URL:-https://github.com/vllm-project/vllm.git}"
VLLM_SRC_DIR="${VLLM_SRC_DIR:-./vllm-src}"

# vLLM 0.18.1 expects the torch 2.10.x stack.
TORCH_VERSION="${TORCH_VERSION:-2.10.0}"
TORCHVISION_VERSION="${TORCHVISION_VERSION:-0.25.0}"
TORCHAUDIO_VERSION="${TORCHAUDIO_VERSION:-2.10.0}"

USING_CONDA=0
TORCH_VARIANT_SELECTED=""

ARM_CONDA_SH="/scratch/x3326a02/miniconda3-arm/etc/profile.d/conda.sh"

try_source_conda_sh() {
  # On aarch64, always prefer the ARM conda over anything in PATH.
  if [[ "$(uname -m)" == "aarch64" ]] && [[ -f "$ARM_CONDA_SH" ]]; then
    # shellcheck disable=SC1090
    source "$ARM_CONDA_SH"
    return 0
  fi

  # Non-interactive shells often don't have conda in PATH.
  # We try common conda.sh locations and source them if found.
  local candidates=(
    "$HOME/miniconda3/etc/profile.d/conda.sh"
    "$HOME/anaconda3/etc/profile.d/conda.sh"
    "$HOME/Miniconda3/etc/profile.d/conda.sh"
    "/opt/conda/etc/profile.d/conda.sh"
    "/usr/local/miniconda3/etc/profile.d/conda.sh"
  )

  for f in "${candidates[@]}"; do
    if [[ -f "$f" ]]; then
      # shellcheck disable=SC1090
      source "$f"
      return 0
    fi
  done

  return 1
}

accept_conda_tos() {
  # Some conda versions/environments require explicit ToS acceptance.
  if command -v conda &>/dev/null; then
    if conda tos --help &>/dev/null; then
      echo "Accepting Conda Terms of Service for common channels..."
      set +u
      conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/main || true
      conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/r || true
      set -u
    fi
  fi
}

setup_env() {
  # If CONDA_EXE is set but conda not in PATH, try to add it.
  if [[ -z "$(command -v conda)" ]] && [[ -n "${CONDA_EXE:-}" ]]; then
    export PATH="$(dirname "${CONDA_EXE}"):${PATH}"
  fi

  # If user's interactive shell auto-initializes conda, non-interactive shells
  # (like this script) might not. Try sourcing common rc files first.
  if ! command -v conda &>/dev/null; then
    if [[ -f "$HOME/.bashrc" ]]; then
      # shellcheck disable=SC1090
      source "$HOME/.bashrc" || true
    fi
    if ! command -v conda &>/dev/null && [[ -f "$HOME/.profile" ]]; then
      # shellcheck disable=SC1090
      source "$HOME/.profile" || true
    fi
  fi

  if command -v conda &>/dev/null; then
    USING_CONDA=1
  else
    # If user's interactive shell auto-initializes conda, non-interactive shells
    # (like this script) might not. Try sourcing common rc files first.
    if ! command -v conda &>/dev/null; then
      if [[ -f "$HOME/.bashrc" ]]; then
        # shellcheck disable=SC1090
        source "$HOME/.bashrc" || true
      fi
      if ! command -v conda &>/dev/null && [[ -f "$HOME/.profile" ]]; then
        # shellcheck disable=SC1090
        source "$HOME/.profile" || true
      fi
    fi

    if command -v conda &>/dev/null; then
      USING_CONDA=1
    else
      # Try to activate conda by sourcing conda.sh if it exists.
      if try_source_conda_sh; then
        if command -v conda &>/dev/null; then
          USING_CONDA=1
        fi
      fi
    fi
  fi

  if [[ "$USING_CONDA" -eq 1 ]]; then
    echo "Using conda: activating '$ENV_NAME' ..."

    # Accept Terms of Service for default channels non-interactively.
    for ch in https://repo.anaconda.com/pkgs/main https://repo.anaconda.com/pkgs/r; do
      conda tos accept --override-channels --channel "$ch" 2>/dev/null || true
    done

    set +u
    if conda info --envs | awk '{print $1}' | grep -qx "$ENV_NAME"; then
      echo "Conda env '$ENV_NAME' already exists."
    else
      echo "Creating conda env '$ENV_NAME' with Python $PYTHON_VERSION ..."
      accept_conda_tos
      set +u
      conda create -y -n "$ENV_NAME" python="$PYTHON_VERSION"
      set -u
    fi
    # Make sure conda is usable in this non-interactive script.
    # conda activate scripts may reference unbound vars; suppress errors temporarily.
    set +e
    eval "$(conda shell.bash hook)"
    set +u
    conda activate "$ENV_NAME"
    set -eu
  else
    echo "Conda not found (and conda.sh not available). Creating venv at '$VENV_DIR' ..."
    if [[ ! -d "$VENV_DIR" ]]; then
      "$PYTHON_BIN" -m venv "$VENV_DIR"
    fi
    # shellcheck disable=SC1090
    source "$VENV_DIR/bin/activate"
  fi
}

install_build_deps() {
  if [[ "$USING_CONDA" -eq 1 ]]; then
    # Wheel path does not strictly need these, but source fallback does.
    set +u
    conda install -y cmake ninja setuptools-scm git
    set -u
  else
    echo "Skipping build deps (conda unavailable). Wheel install is expected to succeed."
  fi
}

install_torch_for_variant() {
  # $1: cuXXX or cpu
  local variant="$1"
  echo "Installing PyTorch for '$variant' ..."
  python -c "import sys; print('python', sys.version)"

  local torch_spec
  local vision_spec
  local audio_spec

  if [[ "$variant" == "cpu" ]]; then
    torch_spec="torch==${TORCH_VERSION}"
    vision_spec="torchvision==${TORCHVISION_VERSION}"
    audio_spec="torchaudio==${TORCHAUDIO_VERSION}"
    if pip install "$torch_spec" "$vision_spec" "$audio_spec"; then
      TORCH_VARIANT_SELECTED="$variant"
      return 0
    fi
    return 1
  fi

  local index_url="https://download.pytorch.org/whl/${variant}"
  torch_spec="torch==${TORCH_VERSION}+${variant}"
  vision_spec="torchvision==${TORCHVISION_VERSION}+${variant}"
  audio_spec="torchaudio==${TORCHAUDIO_VERSION}+${variant}"
  if pip install "$torch_spec" "$vision_spec" "$audio_spec" --index-url "$index_url"; then
    TORCH_VARIANT_SELECTED="$variant"
    return 0
  fi
  return 1
}

ensure_torch_cuda_runtime() {
  if [[ -z "$TORCH_VARIANT_SELECTED" || "$TORCH_VARIANT_SELECTED" == "cpu" ]]; then
    return 0
  fi

  echo "Re-checking CUDA-enabled torch runtime (${TORCH_VARIANT_SELECTED}) ..."
  local torch_index_url="https://download.pytorch.org/whl/${TORCH_VARIANT_SELECTED}"
  pip install --force-reinstall \
    "torch==${TORCH_VERSION}+${TORCH_VARIANT_SELECTED}" \
    "torchvision==${TORCHVISION_VERSION}+${TORCH_VARIANT_SELECTED}" \
    "torchaudio==${TORCHAUDIO_VERSION}+${TORCH_VARIANT_SELECTED}" \
    --index-url "$torch_index_url"
}

install_vllm_nightly_wheels() {
  # $1: variant list (space-separated)
  local candidates=("$@")
  local tried=0

  for variant in "${candidates[@]}"; do
    tried=$((tried + 1))
    local url
    if [[ "$variant" == "cpu" ]]; then
      url="${VLLM_NIGHTLY_BASE_URL}/cpu/"
    else
      url="${VLLM_NIGHTLY_BASE_URL}/${variant}"
    fi

    echo "Trying vLLM nightly wheels from: $url"
    if pip install --no-cache-dir "vllm" --pre --extra-index-url "$url"; then
      echo "vLLM nightly installed successfully (variant: $variant)."
      return 0
    fi
  done

  echo "Failed to install vLLM nightly wheels after trying ${tried} candidate(s)."
  return 1
}

clone_or_update_vllm_src() {
  if [[ -d "$VLLM_SRC_DIR/.git" ]]; then
    echo "Updating existing vLLM source: $VLLM_SRC_DIR"
    git -C "$VLLM_SRC_DIR" fetch --depth 1 origin main
    git -C "$VLLM_SRC_DIR" checkout -q main || true
    git -C "$VLLM_SRC_DIR" pull --ff-only || true
  else
    echo "Cloning vLLM source (nightly): $VLLM_GIT_URL -> $VLLM_SRC_DIR"
    if [[ -e "$VLLM_SRC_DIR" ]]; then
      local ts
      ts="$(date +%Y%m%d_%H%M%S)"
      echo "Warning: '$VLLM_SRC_DIR' already exists; moving to '${VLLM_SRC_DIR}.bak_${ts}'"
      mv "$VLLM_SRC_DIR" "${VLLM_SRC_DIR}.bak_${ts}" || true
    fi
    git clone --depth 1 --branch main "$VLLM_GIT_URL" "$VLLM_SRC_DIR"
  fi
}

patch_vllm_source_for_build() {
  # These patches reflect previous workaround logic.
  # They may or may not apply to the currently checked-out vLLM commit.
  local dir="$VLLM_SRC_DIR"
  cd "$dir"

  if [[ -f "setup.py" ]]; then
    echo "Patching $dir/setup.py (torch.version getattr safety + stable libtorch toggle) ..."
    python3 - <<'PY'
import pathlib

path = pathlib.Path("setup.py")
content = path.read_text()

repls = [
    ('if torch.version.cuda is not None:', 'if getattr(torch.version, "cuda", None) is not None:'),
    ('elif torch.version.hip is not None:', 'elif getattr(torch.version, "hip", None) is not None:'),
    ('elif torch.version.xpu is not None:', 'elif getattr(torch.version, "xpu", None) is not None:'),
]
for old, new in repls:
    if old in content:
        content = content.replace(old, new)

old_line = 'ext_modules.append(CMakeExtension(name="vllm._C_stable_libtorch"))'
if old_line in content:
    content = content.replace(
        old_line,
        '# ext_modules.append(CMakeExtension(name="vllm._C_stable_libtorch"))\n        pass'
    )

path.write_text(content)
PY
  else
    echo "Warning: setup.py not found; skipping setup.py patch."
  fi

  if [[ -f "CMakeLists.txt" ]]; then
    echo "Patching $dir/CMakeLists.txt (comment out _C_stable_libtorch block) ..."
    python3 - <<'PY'
import pathlib

path = pathlib.Path("CMakeLists.txt")
lines = path.read_text().splitlines(True)

start_block = -1
end_block = -1
for i, line in enumerate(lines):
    if 'if(VLLM_GPU_LANG STREQUAL "CUDA")' in line and i > 900:
        if i + 1 < len(lines) and '_C_stable_libtorch extension' in lines[i + 1]:
            start_block = i
            for j in range(i + 1, len(lines)):
                if lines[j].strip().startswith("endif()") or lines[j].strip() == "endif()":
                    end_block = j
                    break
            break

if start_block != -1 and end_block != -1:
    for k in range(start_block, end_block + 1):
        if not lines[k].lstrip().startswith("#"):
            lines[k] = "# " + lines[k]
    path.write_text("".join(lines))
    print("Commented out _C_stable_libtorch block in CMakeLists.txt")
else:
    print("Warning: Could not find _C_stable_libtorch block in CMakeLists.txt")
PY
  else
    echo "Warning: CMakeLists.txt not found; skipping CMakeLists patch."
  fi

  if [[ -f "vllm/platforms/cuda.py" ]]; then
    echo "Patching vllm/platforms/cuda.py (disable stable libtorch import) ..."
    sed -i 's/import vllm\._C_stable_libtorch/# import vllm._C_stable_libtorch/g' vllm/platforms/cuda.py || true
  else
    echo "Warning: vllm/platforms/cuda.py not found; skipping cuda.py patch."
  fi
}

build_vllm_from_source() {
  if ! command -v nvcc &>/dev/null; then
    echo "Error: nvcc not found. Cannot build vLLM from source without CUDA toolkit."
    echo "If nightly wheels failed, try a different CUDA variant (CUDA_VARIANTS) or install CUDA toolkit."
    return 1
  fi
  clone_or_update_vllm_src
  patch_vllm_source_for_build

  # Try to set CUDA_HOME if nvcc exists.
  if [[ -z "${CUDA_HOME:-}" ]]; then
    if command -v nvcc &>/dev/null; then
      # nvcc is typically at $CUDA_HOME/bin/nvcc
      CUDA_HOME="$(cd "$(dirname "$(readlink -f "$(command -v nvcc)")")/.." && pwd)"
      export CUDA_HOME
      export PATH="${CUDA_HOME}/bin:${PATH}"
      echo "Detected CUDA_HOME=$CUDA_HOME from nvcc."
    else
      echo "Warning: nvcc not found; source build may fail."
    fi
  fi

  echo "Installing vLLM from source (editable) ..."
  pip install -U pip setuptools wheel
  pip install -e . --no-build-isolation --verbose
}

main() {
  setup_env

  echo "=== Installing dependencies ==="
  pip install -U pip setuptools wheel
  install_build_deps

  # Choose CUDA variants to try (override via env if needed).
  # Override format: CUDA_VARIANTS="cu130 cu129 cpu"
  local variants=("${CUDA_VARIANTS_DEFAULT[@]}")
  if [[ -n "${CUDA_VARIANTS:-}" ]]; then
    IFS=' ' read -r -a variants <<< "${CUDA_VARIANTS}"
  fi

  # Install torch first.
  local torch_ok=1
  for v in "${variants[@]}"; do
    if install_torch_for_variant "$v"; then
      torch_ok=0
      break
    fi
  done
  if [[ "$torch_ok" -ne 0 ]]; then
    echo "Error: Failed to install PyTorch for variants: ${variants[*]}"
    exit 1
  fi

  echo "=== Installing vLLM nightly (wheels) ==="
  if ! install_vllm_nightly_wheels "${variants[@]}"; then
    echo "Falling back to source build (this may take a long time)."
    build_vllm_from_source
  fi
  ensure_torch_cuda_runtime

  echo "=== Installing ipykernel and pandas ==="
  pip install ipykernel pandas scikit-learn
  python -m ipykernel install --user --name "$ENV_NAME" --display-name "Python ($ENV_NAME)"

  echo "=== Verifying install ==="
  python - <<'PY'
import importlib.metadata as m
import os
import torch
print("vllm version:", m.version("vllm"))
print("torch version:", torch.__version__)
print("torch cuda:", getattr(torch.version, "cuda", None))
lib_dir = os.path.join(os.path.dirname(torch.__file__), "lib")
print("has libtorch_cuda.so:", os.path.exists(os.path.join(lib_dir, "libtorch_cuda.so")))
PY

  echo "=== Setup complete ==="
  echo "Next: run your vLLM scripts on GPU node."
  echo "Example: python -c \"from vllm import LLM; print('LLM import ok')\""
}

main "$@"