#!/usr/bin/env bash
#
# Launch several `ollama serve` instances, one per model slot from config.cfg.
#
# A single `ollama serve` keeps exactly one runner per model, so to run
# genuinely independent instances we start several servers, each on its own
# port (STARTING_PORT + i). The number of servers comes from the MODELS value
# in config.cfg; THREADS_PER_MODEL maps to OLLAMA_NUM_PARALLEL on each server.
#
# Servers are spread evenly across CUDA devices 0 and 1 (server i uses GPU
# i % 2), and the total is capped at MAX_MODELS (8) regardless of config.
#
# Usage:
#   scripts/serve_ollama.sh           # read MODELS / STARTING_PORT from config
#
set -euo pipefail

# Hard cap on the number of servers, and the GPUs to round-robin across.
MAX_MODELS=8
GPUS=(0 1)

# Resolve repo root from this script's location so it works from any CWD.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="${SCRIPT_DIR}/../src/config/config.cfg"

if [[ ! -f "${CONFIG_FILE}" ]]; then
    echo "Config not found: ${CONFIG_FILE}" >&2
    exit 1
fi

# Read a KEY=VALUE entry from the config, returning a default if it's missing
# or blank. Ignores comments and surrounding whitespace.
read_config() {
    local key="$1" default="${2:-}"
    local value
    value="$(grep -E "^[[:space:]]*${key}=" "${CONFIG_FILE}" | tail -n1 | cut -d= -f2- | tr -d '[:space:]')"
    echo "${value:-${default}}"
}

MODELS="$(read_config MODELS 1)"
STARTING_PORT="$(read_config STARTING_PORT 11434)"
THREADS_PER_MODEL="$(read_config THREADS_PER_MODEL 4)"

if ! [[ "${MODELS}" =~ ^[0-9]+$ ]] || (( MODELS < 1 )); then
    echo "MODELS must be a positive integer, got: '${MODELS}'" >&2
    exit 1
fi

if (( MODELS > MAX_MODELS )); then
    echo "MODELS=${MODELS} exceeds the cap; clamping to ${MAX_MODELS}." >&2
    MODELS="${MAX_MODELS}"
fi

echo "Launching ${MODELS} ollama server(s) from port ${STARTING_PORT}, spread across GPUs ${GPUS[*]}"
echo "(OLLAMA_NUM_PARALLEL=${THREADS_PER_MODEL} per server)"

pids=()

# Terminate every server we started when this script exits or is interrupted.
cleanup() {
    echo
    echo "Stopping ${#pids[@]} ollama server(s)..."
    for pid in "${pids[@]}"; do
        kill "${pid}" 2>/dev/null || true
    done
    wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

for (( i = 0; i < MODELS; i++ )); do
    port=$(( STARTING_PORT + i ))
    gpu="${GPUS[i % ${#GPUS[@]}]}"
    echo "  -> ollama serve on 127.0.0.1:${port} (GPU ${gpu})"
    CUDA_VISIBLE_DEVICES="${gpu}" \
    OLLAMA_HOST="127.0.0.1:${port}" \
    OLLAMA_NUM_PARALLEL="${THREADS_PER_MODEL}" \
        ollama serve &
    pids+=("$!")
done

# Keep the script alive so the trap can clean up on Ctrl-C; exit if any
# server dies on its own.
wait -n
