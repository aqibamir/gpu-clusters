#!/usr/bin/env bash
# Join an existing Ray cluster as a worker node for 70B+ distributed inference.
#
# Run this on ADDITIONAL VMs after the primary node is already running.
# The primary node runs start_node_gpu.sh with TENSOR_PARALLEL_SIZE > 1.
#
# Usage:
#   RAY_HEAD_IP=<primary-vm-ip> ./scripts/start_ray_worker.sh
#
# The worker contributes its GPU memory to the Ray cluster. vLLM on the
# primary node automatically distributes model layers across all workers.
# Workers do NOT run uvicorn or the node agent — just Ray.

set -euo pipefail

: "${RAY_HEAD_IP:?Error: RAY_HEAD_IP is not set (IP of the primary VM)}"
RAY_PORT="${RAY_PORT:-6379}"

GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1 || echo "nvidia-gpu")
GPU_COUNT=$(nvidia-smi --list-gpus 2>/dev/null | wc -l || echo "1")

echo "Joining Ray cluster at $RAY_HEAD_IP:$RAY_PORT"
echo "  This VM: $GPU_COUNT × $GPU_NAME"

docker run -d \
  --gpus all \
  --restart unless-stopped \
  --name ray-worker \
  --network host \
  -e RAY_HEAD_IP="$RAY_HEAD_IP" \
  -e RAY_PORT="$RAY_PORT" \
  vllm/vllm-openai:latest \
  bash -c "ray start --address=$RAY_HEAD_IP:$RAY_PORT --num-gpus=$GPU_COUNT && sleep infinity"

echo ""
echo "Ray worker joined. The primary node will auto-detect this GPU."
echo "Check with: docker logs ray-worker"
