#!/usr/bin/env bash
# Log the graphics card every 2 seconds while you test, then summarise.
#
#   bash gpu-watch.sh start     # start.sh does this for you
#   bash gpu-watch.sh report    # peak memory, average and peak use, time logged
#   bash gpu-watch.sh stop
#
# Log: /workspace/kimilm-gpu.csv (time, card, memory used MiB, memory total MiB, GPU use %, power W).
set -euo pipefail
WORK="${KIMI_WORK:-/workspace}"
CSV="$WORK/kimilm-gpu.csv"
PIDFILE="$WORK/kimilm-gpu.pid"

case "${1:-report}" in
  start)
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then echo "already logging to $CSV"; exit 0; fi
    nohup nvidia-smi --query-gpu=timestamp,name,memory.used,memory.total,utilization.gpu,power.draw \
      --format=csv,noheader,nounits -l 2 >>"$CSV" 2>/dev/null &
    echo $! >"$PIDFILE"
    echo "logging to $CSV"
    ;;
  stop)
    [ -f "$PIDFILE" ] && kill "$(cat "$PIDFILE")" 2>/dev/null || true
    rm -f "$PIDFILE"
    echo "stopped"
    ;;
  report)
    [ -s "$CSV" ] || { echo "nothing logged yet ($CSV)"; exit 0; }
    awk -F', ' '
      { n++; card=$2; total=$4; if ($3>peak) {peak=$3; at=$1}; use+=$5; if ($5>maxuse) maxuse=$5; first=(n==1?$1:first); last=$1 }
      END {
        printf "card:              %s\n", card
        printf "samples:           %d (every 2 s, %s to %s)\n", n, first, last
        printf "peak memory:       %d of %d MiB (%.0f%%) at %s\n", peak, total, 100*peak/total, at
        printf "GPU use:           average %.0f%%, peak %d%%\n", use/n, maxuse
      }' "$CSV"
    ;;
  *) echo "usage: gpu-watch.sh start|stop|report" >&2; exit 2 ;;
esac
