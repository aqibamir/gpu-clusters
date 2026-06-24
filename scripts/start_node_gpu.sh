#!/usr/bin/env bash
# Start a GPU cluster node on a cloud NVIDIA VM (RunPod / Lambda / Vast.ai).
#
# Prerequisites on the VM:
#   - Docker installed
#   - NVIDIA Container Toolkit installed (nvidia-docker2)
#
# Required env vars:
#   ORCHESTRATOR_URL  — e.g. https://your-app.up.railway.app
#   API_KEY           — shared secret (get this from the cluster operator)
#   NODE_BASE_URL     — this VM's public address, e.g. http://123.45.67.89:8001
#
# Optional:
#   MODEL_NAME          — HuggingFace model ID (default: Mistral-7B-Instruct-v0.2)
#   TENSOR_PARALLEL_SIZE — number of GPUs on this VM (default: 1)
#   HF_TOKEN            — HuggingFace token for gated models (Llama-3 etc.)
#
# Example:
#   ORCHESTRATOR_URL=https://gpu-clusters.up.railway.app \
#   API_KEY=my-secret \
#   NODE_BASE_URL=http://$(curl -s ifconfig.me):8001 \
#   MODEL_NAME=meta-llama/Meta-Llama-3-8B-Instruct \
#   HF_TOKEN=hf_... \
#   ./scripts/start_node_gpu.sh

set -euo pipefail

: "${ORCHESTRATOR_URL:?Error: ORCHESTRATOR_URL is not set}"
: "${API_KEY:?Error: API_KEY is not set}"
: "${NODE_BASE_URL:?Error: NODE_BASE_URL is not set}"

MODEL_NAME="${MODEL_NAME:-mistralai/Mistral-7B-Instruct-v0.2}"
TENSOR_PARALLEL="${TENSOR_PARALLEL_SIZE:-1}"
GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1 || echo "nvidia-gpu")

echo "Starting GPU cluster node..."
echo "  Model:       $MODEL_NAME"
echo "  GPUs:        $TENSOR_PARALLEL × $GPU_NAME"
echo "  Orchestrator: $ORCHESTRATOR_URL"
echo "  Node URL:    $NODE_BASE_URL"

docker run -d \
  --gpus all \
  --restart unless-stopped \
  --name gpu-cluster-node \
  -p 8001:8001 \
  -e MODEL_NAME="$MODEL_NAME" \
  -e TENSOR_PARALLEL_SIZE="$TENSOR_PARALLEL" \
  -e ORCHESTRATOR_URL="$ORCHESTRATOR_URL" \
  -e API_KEY="$API_KEY" \
  -e NODE_BASE_URL="$NODE_BASE_URL" \
  -e GPU_NAME="$GPU_NAME" \
  -e HF_TOKEN="${HF_TOKEN:-}" \
  -v hf-cache:/root/.cache/huggingface \
  ghcr.io/aqibamir/gpu-clusters-node:latest

echo ""
echo "Node started. Check status with:"
echo "  docker logs -f gpu-cluster-node"
echo "  curl http://localhost:8001/health"
