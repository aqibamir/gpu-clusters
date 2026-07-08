#!/usr/bin/env bash
# Register this Mac as a CGN node and start serving jobs.
#
# The node generates its own keypair locally (never transmitted) and enrolls
# with a single-use token minted by the operator dashboard ("+ Add node").
#
# Required:
#   ORCHESTRATOR_URL — the orchestrator base URL (local or Railway)
#   ENROLL_TOKEN     — single-use enrollment token from the dashboard
# Optional:
#   MODELS           — space-separated model names this node serves
#                      (default: mistral-7b-instruct-v0.2.Q4_K_M)
#   KEY_DIR          — where the node key is stored (default: ~/.cmndr-node)
#
# Example:
#   ORCHESTRATOR_URL=https://cmndr-production.up.railway.app \
#   ENROLL_TOKEN=xxxx ./scripts/start_node.sh

set -euo pipefail

: "${ORCHESTRATOR_URL:?set ORCHESTRATOR_URL}"
: "${ENROLL_TOKEN:?set ENROLL_TOKEN (from the operator dashboard '+ Add node')}"

MODELS="${MODELS:-mistral-7b-instruct-v0.2.Q4_K_M}"
KEY_DIR="${KEY_DIR:-$HOME/.cmndr-node}"

# Install llama-cpp-python with Metal if absent (falls back to EchoBackend if the
# GGUF model is missing — useful for a wiring smoke test).
if ! python -c "from llama_cpp import Llama" 2>/dev/null; then
  echo "Installing llama-cpp-python with Metal backend…"
  CMAKE_ARGS="-DGGML_METAL=on" uv pip install llama-cpp-python
fi

echo "Enrolling node with $ORCHESTRATOR_URL (models: $MODELS)…"
exec uv run python -m cgn.node.worker \
  --orchestrator "$ORCHESTRATOR_URL" \
  --token "$ENROLL_TOKEN" \
  --models $MODELS \
  --key-dir "$KEY_DIR"
