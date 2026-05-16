# SHAPE-of-CoT Training

This directory contains the training workflow for SHAPE-of-CoT. The main flow is:

1. Set up the conda environment and install dependencies.
2. Prepare datasets with the scripts in `./data`.
3. Update local paths in the training config.
4. Launch GRPO training with `run_local_grpo.sh`.

## Conda Setup

Create and activate a Python 3.12 environment:

```bash
conda create -n shape python=3.12.3
conda activate shape
```

Install the required packages:

```bash
pip install torch==2.8.0 torchvision==0.23.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cu129

pip install -r requirements.txt

pip install -e .

pip install https://github.com/mjun0812/flash-attention-prebuild-wheels/releases/download/v0.7.11/flash_attn-2.8.3+cu129torch2.8-cp312-cp312-linux_x86_64.whl
```


## Data Preprocessing

Dataset preparation is documented in `./data/README.md`. 

For training datasets with both train and test splits, such as GSM8K:

```bash
uv run python ./data/prepare_math_dataset.py \
    --dataset_name gsm8k \
    --prompt_type ha-plan
```

This creates files such as:

- `datasets/math/gsm8k_ha_plan/train.parquet`
- `datasets/math/gsm8k_ha_plan/test.parquet`


## Config Setup

Before training, update the local paths in `./verl/trainer/config/user.yaml`.

Replace every `/path/to/...` placeholder with paths that match your machine:

```yaml
vars:
  dir: /path/to/SHAPE-of-CoT/train
  log_dir: /path/to/SHAPE-of-CoT/logs
  ckpt_dir: /path/to/SHAPE-of-CoT/ckpts

custom_reward_function:
  path: /path/to/SHAPE-of-CoT/verl/utils/reward_score/feedback/__init__.py
```


## Training

Launch GRPO training with:

```bash
./run_grpo_training.sh
```
