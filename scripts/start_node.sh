#!/usr/bin/env bash
# Start the node agent on this Mac.
#
# Required env vars:
#   MODEL_PATH        — path to your .gguf model file
#   ORCHESTRATOR_URL  — URL of the orchestrator (local or Railway)
#   API_KEY           — shared secret (must match orchestrator)
#   NODE_BASE_URL     — this machine's address as seen from the orchestrator
#                       e.g. http://192.168.1.42:8001 on LAN
#                       or   http://<tailscale-ip>:8001 over Tailscale
#
# Example:
#   MODEL_PATH=./models/mistral-7b-instruct-v0.2.Q4_K_M.gguf \
#   ORCHESTRATOR_URL=https://gpu-clusters-production.up.railway.app \
#   API_KEY=my-secret-key \
#   NODE_BASE_URL=http://192.168.1.42:8001 \
#   ./scripts/start_node.sh

set -euo pipefail

if [ -z "${MODEL_PATH:-}" ]; then
  echo "Error: MODEL_PATH is not set."
  echo "Run: python scripts/download_model.py  to get the model first."
  exit 1
fi

if [ ! -f "$MODEL_PATH" ]; then
  echo "Error: Model file not found at $MODEL_PATH"
  exit 1
fi

# Install llama-cpp-python with Metal backend if not already present
if ! python -c "from llama_cpp import Llama" 2>/dev/null; then
  echo "Installing llama-cpp-python with Metal backend…"
  CMAKE_ARGS="-DGGML_METAL=on" uv pip install llama-cpp-python
fi

echo "Starting node agent…"
echo "  MODEL_PATH:       $MODEL_PATH"
echo "  ORCHESTRATOR_URL: ${ORCHESTRATOR_URL:-http://localhost:8000}"
echo "  NODE_BASE_URL:    ${NODE_BASE_URL:-http://localhost:8001}"

export MODEL_PATH
export ORCHESTRATOR_URL="${ORCHESTRATOR_URL:-http://localhost:8000}"
export API_KEY="${API_KEY:-}"
export NODE_BASE_URL="${NODE_BASE_URL:-http://localhost:8001}"

uvicorn node_agent.main:app --host 0.0.0.0 --port 8001
