#!/usr/bin/env bash
# Start KimiLM's engine on the Pod and print how to open it from your own computer.
#
#   bash /workspace/KimiLM/deploy/runpod/start.sh            # starts it (or shows how to reach it if running)
#   KIMI_STOP_AFTER_HOURS=2 bash .../start.sh                # stop the Pod by itself after 2 hours (default 3)
#   KIMI_STOP_AFTER_HOURS=0 bash .../start.sh                # no automatic stop
#
# The engine listens on 127.0.0.1 only and needs its token, as on your laptop. You reach it through an SSH
# tunnel, so nothing is exposed to the internet. It keeps running when you close the terminal.
set -euo pipefail

WORK="${KIMI_WORK:-/workspace}"
[ -f "$WORK/kimilm.env" ] || { echo "Run setup.sh first." >&2; exit 1; }
# shellcheck disable=SC1091
. "$WORK/kimilm.env"
PORT="${KIMI_PORT:-8188}"
HOURS="${KIMI_STOP_AFTER_HOURS:-3}"
LOG="$WORK/kimilm-engine.log"
PIDFILE="$WORK/kimilm-engine.pid"
TOKENFILE="$WORK/kimilm-token"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

running() { [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; }

show_access() {
  local token ip port
  token="$(cat "$TOKENFILE")"
  ip="${RUNPOD_PUBLIC_IP:-POD_IP}"; port="${RUNPOD_TCP_PORT_22:-SSH_PORT}"
  cat <<EOF

KimiLM is running on the Pod (port $PORT, log: $LOG).

1. On YOUR computer, open a terminal and keep this running (it is the tunnel):

   ssh -N -L $PORT:127.0.0.1:$PORT root@$ip -p $port -i ~/.ssh/id_ed25519

2. Then open this address in your browser:

   http://localhost:$PORT/?token=$token

EOF
  if [ "$ip" = POD_IP ]; then
    echo "(POD_IP and SSH_PORT: see the Pod's Connect > 'Direct TCP Ports' line for :22.)"
  fi
}

if running; then
  echo "Already running."
  show_access
  exit 0
fi

# A token for this Pod, kept between restarts so a saved browser link still works.
if [ ! -s "$TOKENFILE" ]; then
  ( umask 077; "$KIMI_VENV/bin/python" -c "import secrets; print(secrets.token_urlsafe(32))" >"$TOKENFILE" )
fi

# KIMILM_SECRETS=file: a Pod has no keyring; keys you add are kept in the data folder, readable only by root.
(
  cd "$KIMI_REPO/engine"
  KIMILM_TOKEN="$(cat "$TOKENFILE")" KIMILM_UI_DIR="$KIMI_UI" KIMILM_SECRETS=file \
    nohup "$KIMI_VENV/bin/python" -m kimilm serve --port "$PORT" --data-dir "$KIMI_DATA" >"$LOG" 2>&1 &
  echo $! >"$PIDFILE"
)
for _ in $(seq 1 120); do
  grep -q "KIMILM_READY" "$LOG" 2>/dev/null && break
  running || { tail -30 "$LOG"; echo "The engine stopped while starting (log above)." >&2; exit 1; }
  sleep 0.5
done
grep -q "KIMILM_READY" "$LOG" || { tail -30 "$LOG"; echo "The engine did not start within a minute." >&2; exit 1; }

# GPU log for the whole session (see gpu-watch.sh report).
bash "$HERE/gpu-watch.sh" start >/dev/null 2>&1 || true

# Safety net for the budget: stop the Pod by itself after HOURS. Stopping ends the GPU charge; the volume disk
# is still charged (twice the running rate) until you Terminate.
if [ "$HOURS" != 0 ]; then
  # Arm it only when runpodctl can actually reach this Pod (it needs a RunPod API key).
  if [ -n "${RUNPOD_POD_ID:-}" ] && command -v runpodctl >/dev/null \
     && { runpodctl pod get "$RUNPOD_POD_ID" >/dev/null 2>&1 || runpodctl get pod "$RUNPOD_POD_ID" >/dev/null 2>&1; }; then
    secs="$(awk -v h="$HOURS" 'BEGIN {print int(h * 3600)}')"
    nohup bash -c "sleep $secs; runpodctl pod stop $RUNPOD_POD_ID || runpodctl stop pod $RUNPOD_POD_ID" \
      >"$WORK/kimilm-autostop.log" 2>&1 &
    echo "Automatic stop armed: the Pod stops itself in $HOURS h ($(date -d "+$secs seconds" '+%H:%M %Z'))."
  else
    echo "NOTE: automatic stop is NOT armed (runpodctl cannot reach this Pod). Stop the Pod yourself."
  fi
fi
show_access
