#!/bin/sh
# Runs breeze-server (GGUF engine, internal only) and the compatibility API
# (public, port 7860) in one container. If either process exits, the container
# exits and Docker's restart policy brings both back clean.
set -eu

MODEL="${BREEZE_MODEL:-/models/breeze-tts-2-q8_0.gguf}"
ENGINE_PORT="${BREEZE_ENGINE_PORT:-8080}"

stdbuf -oL -eL breeze-server "$MODEL" --host 127.0.0.1 --port "$ENGINE_PORT" --ws-port -1 \
    --voices-dir /tmp/engine-voices ${BREEZE_ENGINE_ARGS:-} &
ENGINE_PID=$!

cd /opt/breeze
BREEZE_ENGINE_URL="http://127.0.0.1:$ENGINE_PORT" python3 compat_api.py &
API_PID=$!

trap 'kill $ENGINE_PID $API_PID 2>/dev/null' INT TERM

# Exit as soon as either process dies.
while kill -0 $ENGINE_PID 2>/dev/null && kill -0 $API_PID 2>/dev/null; do
    sleep 2
done
kill -0 $ENGINE_PID 2>/dev/null || { rc=0; wait $ENGINE_PID || rc=$?; echo "breeze-server exited with status $rc" >&2; }
kill -0 $API_PID 2>/dev/null || { rc=0; wait $API_PID || rc=$?; echo "compat API exited with status $rc" >&2; }
echo "a process exited; stopping container" >&2
kill $ENGINE_PID $API_PID 2>/dev/null || true
wait || true
exit 1
