# Installation Instructions

## System Requirements
*   **Hardware:** NVIDIA GPUs (CUDA compatible)
*   **Python:** 3.12 (Tested on 3.12.3)
*   **CUDA Driver:** Compatible with the PyTorch version installed (see below).

## Core Installation


### Local Python Environment (Recommended)
This is the standard approach for local workstations (e.g., NVIDIA A100).

**1. Install PyTorch:**
```bash
# Install PyTorch 2.8.0 (Stable for CUDA 12.9)
pip install torch==2.8.0 torchvision==0.23.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cu129
```

**2. Install Dependences:**
```bash
# Install dependencies
pip install -r requirements.txt

pip install -e .

pip install flash-attn --no-build-isolation
```

