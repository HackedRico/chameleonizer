#!/bin/sh
# Starts llama-server on loopback inside the dev-only agent image, waits for /health, records the
# cold-load time in /app/output/agent_load.json, then runs the given command (default: tools.agree doctor).
# MODEL and MMPROJ name GGUF files under /models. Development only; see docs/agentic-harness.md.
set -eu

: "${MODEL:?set MODEL to a GGUF file name under /models}"
: "${MMPROJ:?set MMPROJ to the matching mmproj GGUF under /models}"
THREADS="${THREADS:-4}"

start=$(date +%s.%N)
llama-server -m "/models/${MODEL}" --mmproj "/models/${MMPROJ}" --host 127.0.0.1 --port 8080 \
    -np 1 -c 8192 --image-max-tokens 256 --no-webui -t "${THREADS}" > /app/output/llama-server.log 2>&1 &
server=$!

waited=0
until curl -fsS http://127.0.0.1:8080/health > /dev/null 2>&1; do
    if ! kill -0 "$server" 2>/dev/null; then
        echo "llama-server exited; see output/llama-server.log" >&2
        exit 1
    fi
    if [ "$waited" -ge 120 ]; then
        echo "llama-server not healthy after 120 s; see output/llama-server.log" >&2
        exit 1
    fi
    sleep 1
    waited=$((waited + 1))
done
end=$(date +%s.%N)
python -c "import json,sys; json.dump({'cold_load_s': round(float(sys.argv[2]) - float(sys.argv[1]), 2), 'model': sys.argv[3]}, open('/app/output/agent_load.json', 'w'))" "$start" "$end" "$MODEL"

exec "$@"
