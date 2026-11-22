#!/usr/bin/env bash
# Measure raw speed of every model you downloaded in KimiLM, on this Pod's card, with llama.cpp's llama-bench.
#
#   bash bench.sh                 # every .gguf under the data folder's models
#   bash bench.sh path/to/x.gguf  # one model
#
# Reading speed (pp512: tokens/s reading a 512-token prompt) and writing speed (tg128: tokens/s writing 128
# tokens), all layers on the GPU. Results are added to /workspace/kimilm-bench.md, so runs on different cards
# can be compared. Takes about a minute per model. Close KimiLM's loaded models first, so memory is free.
set -euo pipefail
WORK="${KIMI_WORK:-/workspace}"
# shellcheck disable=SC1091
. "$WORK/kimilm.env"
OUT="$WORK/kimilm-bench.md"
BENCH="$KIMI_LLAMA/llama-bench"
[ -x "$BENCH" ] || { echo "llama-bench not found at $BENCH (run setup.sh)" >&2; exit 1; }

if [ $# -gt 0 ]; then
  models=("$@")
else
  mapfile -t models < <(find "$KIMI_DATA/models" -name '*.gguf' ! -iname '*mmproj*' 2>/dev/null | sort)
fi
[ "${#models[@]}" -gt 0 ] || { echo "No models yet. Download one in KimiLM (Models) first."; exit 0; }

card="$(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null | head -1 || echo unknown)"
printf '\n## %s, %s\n\n' "$(date '+%Y-%m-%d %H:%M %Z')" "$card" >>"$OUT"
for model in "${models[@]}"; do
  case "$model" in *-0000[2-9]-of-*) continue ;; esac     # split models: the first part loads the rest
  echo "== $(basename "$model")"
  printf '### %s\n\n' "$(basename "$model")" >>"$OUT"
  "$BENCH" -m "$model" -ngl 99 -p 512 -n 128 -r 3 -o md 2>/dev/null | tee -a "$OUT" \
    || echo "| $(basename "$model") | did not run (too big for this card?) |" | tee -a "$OUT"
done
echo
echo "Saved to $OUT"
