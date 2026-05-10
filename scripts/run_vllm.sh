#!/usr/bin/env bash
# Self-host Detector C: vLLM serving Qwen2.5-7B-Instruct AWQ-int4.
#
# Requires:
#   * NVIDIA GPU with >= 16 GB VRAM (L4 / A10 / better)
#   * NVIDIA Container Toolkit installed
#   * docker access to nvidia/cuda images
#
# After this is up, point the proxy at http://localhost:8000/v1 by setting:
#   GATEWAY_VLLM_BACKEND=http
#   GATEWAY_VLLM_URL=http://localhost:8000/v1
#   GATEWAY_VLLM_MODEL=Qwen/Qwen2.5-7B-Instruct-AWQ
set -euo pipefail

MODEL="${MODEL:-Qwen/Qwen2.5-7B-Instruct-AWQ}"
PORT="${PORT:-8000}"

docker run --rm --gpus all \
  -p "${PORT}:8000" \
  -v "${HOME}/.cache/huggingface:/root/.cache/huggingface" \
  --ipc=host \
  vllm/vllm-openai:latest \
  --model "${MODEL}" \
  --quantization awq \
  --gpu-memory-utilization 0.85 \
  --max-model-len 8192 \
  --enable-prefix-caching
