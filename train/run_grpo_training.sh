#!/bin/bash

# Usage: ./run_local_grpo.sh

CONFIG_NAME="${CONFIG_NAME:-baseline_grpo}"
DATA_PATH="${DATA_PATH:-}"

TRAIN_BATCH_SIZE="${TRAIN_BATCH_SIZE:-32}"
ROLLOUT_BATCH_SIZE="${ROLLOUT_BATCH_SIZE:-4}"
MINI_BATCH_SIZE="${MINI_BATCH_SIZE:-4}"
LR="${LR:-1e-6}"
MODEL_PATH="${MODEL_PATH:-Qwen/Qwen3-1.7B-Base}"

ENABLE_THINKING=false

export PROJECT_ROOT="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

MODEL_NAME=$(basename "$MODEL_PATH")
EXP_TAG="${EXP_TAG:-w_heuristic_planning}"
EXP_NAME="GRPO-${MODEL_NAME}-${EXP_TAG}-$(date +%Y%m%d_%H%M%S)"

ARGS="data.train_batch_size=$TRAIN_BATCH_SIZE \
trainer.experiment_name=$EXP_NAME \
actor_rollout_ref.actor.optim.lr_warmup_steps=10 \
actor_rollout_ref.rollout.n=$ROLLOUT_BATCH_SIZE \
actor_rollout_ref.actor.optim.lr=$LR \
actor_rollout_ref.actor.ppo_mini_batch_size=$MINI_BATCH_SIZE \
actor_rollout_ref.model.path=$MODEL_PATH \
data.apply_chat_template_kwargs.enable_thinking=$ENABLE_THINKING \
algorithm.rollout_correction.rollout_is=token \
trainer.total_training_steps=200 \
actor_rollout_ref.rollout.val_kwargs.n=16"

echo "----------------------------------------------------------------"
echo "Starting Local GRPO Training"
echo "Experiment: $EXP_NAME"
echo "Data: $DATA_PATH"
echo "Model: $MODEL_PATH"
echo "Thinking mode: $ENABLE_THINKING"
echo "----------------------------------------------------------------"

bash "$PROJECT_ROOT/training/verl_training.sh" "$EXP_NAME" "$CONFIG_NAME" "$DATA_PATH" $ARGS
