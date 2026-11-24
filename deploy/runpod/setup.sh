#!/usr/bin/env bash
# KimiLM on a RunPod GPU Pod: one-time setup (about 15-25 minutes).
#
#   bash /workspace/KimiLM/deploy/runpod/setup.sh
#
# Puts everything under /workspace (the Pod's volume disk), so it survives a Stop but not a Terminate:
#   /workspace/kimilm-venv    Python 3.12 with the engine's packages
#   /workspace/kimilm-tools   Node.js (only if the Pod's is missing or too old) and the llama.cpp source
#   /workspace/kimilm-data    KimiLM's data folder; the CUDA llama-server goes to runtimes/llama/<tag>/cuda,
#                             where the engine finds it on its own
#   /workspace/kimilm.env     paths read by start.sh
# Safe to run again: finished parts are skipped. Nothing in the repository is changed except app/dist (the
# built window) and app/node_modules.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORK="${KIMI_WORK:-/workspace}"
DATA="${KIMI_DATA:-$WORK/kimilm-data}"
VENV="${KIMI_VENV:-$WORK/kimilm-venv}"
TOOLS="$WORK/kimilm-tools"
CPU_ONLY="${KIMI_CPU_ONLY:-0}"            # 1 = build llama.cpp without CUDA (only for trying the script)
mkdir -p "$DATA" "$TOOLS"

step() { printf '\n== %s\n' "$*"; }
fail() { printf '\nSTOPPED: %s\n' "$*" >&2; exit 1; }
as_root() { if [ "$(id -u)" = 0 ]; then "$@"; else sudo "$@"; fi; }

# ------------------------------------------------------------------ the GPU
if [ "$CPU_ONLY" != 1 ]; then
  step "Graphics card"
  command -v nvidia-smi >/dev/null || fail "nvidia-smi not found. Deploy the Pod with an NVIDIA GPU."
  nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader
fi

# ------------------------------------------------------------------ system packages
step "System packages"
missing=()
for tool in git cmake make g++ curl xz; do command -v "$tool" >/dev/null || missing+=("$tool"); done
if [ "${#missing[@]}" -gt 0 ]; then
  echo "installing: ${missing[*]}"
  as_root apt-get update -qq
  as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git cmake build-essential curl xz-utils \
    ca-certificates >/dev/null
else
  echo "already here"
fi

# ------------------------------------------------------------------ Python 3.12 and the engine's packages
step "Python 3.12 and the engine's packages"
if ! python3 -m uv --version >/dev/null 2>&1; then
  python3 -m pip install -q --disable-pip-version-check uv || python3 -m pip install -q --break-system-packages uv
fi
# Keep uv's own Python under /workspace too: a venv only links to it, and the container disk is wiped on Stop.
export UV_PYTHON_INSTALL_DIR="$TOOLS/python" UV_CACHE_DIR="$TOOLS/uv-cache"
if [ ! -x "$VENV/bin/python" ] || ! "$VENV/bin/python" -c "" 2>/dev/null; then
  python3 -m uv venv --clear --python 3.12 "$VENV"  # uv fetches a standalone Python 3.12 if the Pod has none
fi
python3 -m uv pip install -q --python "$VENV/bin/python" -r "$REPO/engine/requirements.txt"
"$VENV/bin/python" --version

# ------------------------------------------------------------------ llama.cpp with CUDA, at the tag KimiLM pins
TAG="$("$VENV/bin/python" -c "import json,sys; print(json.load(open(sys.argv[1]))['llama_cpp_tag'])" \
  "$REPO/engine/kimilm/versions.json")"
BACKEND=cuda; [ "$CPU_ONLY" = 1 ] && BACKEND=cpu
DEST="$DATA/runtimes/llama/$TAG/$BACKEND"
step "llama.cpp $TAG ($BACKEND)"
if [ -x "$DEST/llama-server" ]; then
  echo "already built: $DEST/llama-server"
else
  cmake_flags=(-DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF -DLLAMA_CURL=OFF)
  if [ "$BACKEND" = cuda ]; then
    for dir in /usr/local/cuda/bin /usr/local/cuda-*/bin; do [ -x "$dir/nvcc" ] && PATH="$dir:$PATH"; done
    command -v nvcc >/dev/null || fail "The CUDA compiler (nvcc) is missing. Deploy the Pod from the official \
'Runpod PyTorch' template, which includes it."
    # Build only for this card: much faster than every architecture. compute_cap "8.9" -> "89".
    arch="$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader 2>/dev/null | head -1 | tr -d ' .')"
    [ -n "$arch" ] || arch="86;89;90"
    cmake_flags+=(-DGGML_CUDA=ON "-DCMAKE_CUDA_ARCHITECTURES=$arch")
    echo "CUDA architecture: $arch"
  fi
  SRC="$TOOLS/llama.cpp-$TAG"
  [ -d "$SRC/.git" ] || git -c advice.detachedHead=false clone -q --depth 1 --branch "$TAG" https://github.com/ggml-org/llama.cpp "$SRC"
  # The CUDA compiler needs about 2 GB per job; a Pod often shows the host's cores, not its own share.
  mem_gb="$(awk '/MemAvailable/ {print int($2/1048576)}' /proc/meminfo)"
  jobs="${KIMI_JOBS:-$(( mem_gb / 2 ))}"; [ "$jobs" -lt 1 ] && jobs=1; [ "$jobs" -gt 16 ] && jobs=16
  [ "$jobs" -gt "$(nproc)" ] && jobs="$(nproc)"
  echo "building with $jobs jobs (this is the slow part)"
  cmake -S "$SRC" -B "$SRC/build-$BACKEND" "${cmake_flags[@]}" >"$TOOLS/llama-configure.log" 2>&1 \
    || { tail -30 "$TOOLS/llama-configure.log"; fail "llama.cpp could not be configured"; }
  cmake --build "$SRC/build-$BACKEND" -j "$jobs" --target llama-server llama-bench >"$TOOLS/llama-build.log" 2>&1 \
    || { tail -30 "$TOOLS/llama-build.log"; fail "llama.cpp did not build (full log: $TOOLS/llama-build.log)"; }
  mkdir -p "$DEST"
  cp "$SRC/build-$BACKEND/bin/llama-server" "$SRC/build-$BACKEND/bin/llama-bench" "$DEST/"
  echo "built: $DEST/llama-server"
fi
if [ "$BACKEND" = cuda ]; then
  devices="$("$DEST/llama-server" --list-devices 2>&1 || true)"
  echo "$devices" | grep -E 'MiB free' || { echo "$devices" | tail -5; fail "llama-server sees no CUDA device."; }
fi

# ------------------------------------------------------------------ the window (served by the engine)
step "KimiLM's window"
node_major="$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)"
if [ "$node_major" -lt 20 ]; then
  if [ ! -x "$TOOLS/node/bin/node" ]; then
    echo "installing Node.js 22"
    base="https://nodejs.org/dist/latest-v22.x"
    file="$(curl -fsSL "$base/SHASUMS256.txt" | awk '/linux-x64\.tar\.xz$/ {print $2}')"
    sum="$(curl -fsSL "$base/SHASUMS256.txt" | awk '/linux-x64\.tar\.xz$/ {print $1}')"
    curl -fsSL "$base/$file" -o "$TOOLS/$file"
    echo "$sum  $TOOLS/$file" | sha256sum -c - >/dev/null || fail "Node.js download did not match its checksum"
    mkdir -p "$TOOLS/node" && tar -xJf "$TOOLS/$file" -C "$TOOLS/node" --strip-components=1 && rm "$TOOLS/$file"
  fi
  PATH="$TOOLS/node/bin:$PATH"
fi
echo "Node.js $(node --version)"
if [ ! -f "$REPO/app/dist/index.html" ] || [ "$REPO/app/src" -nt "$REPO/app/dist/index.html" ]; then
  # Only the window is built here: the desktop shell (Electron) is not needed, so its download is skipped.
  (cd "$REPO/app" && ELECTRON_SKIP_BINARY_DOWNLOAD=1 npm ci --no-audit --no-fund --loglevel=error \
    && npx vite build --logLevel error)
fi
[ -f "$REPO/app/dist/index.html" ] || fail "the window was not built"
echo "built: $REPO/app/dist"

# ------------------------------------------------------------------ remember the paths
cat >"$WORK/kimilm.env" <<EOF
KIMI_REPO=$REPO
KIMI_DATA=$DATA
KIMI_VENV=$VENV
KIMI_UI=$REPO/app/dist
KIMI_LLAMA=$DEST
EOF
step "Done"
echo "Next: bash $REPO/deploy/runpod/start.sh"
