# KimiLM on a RunPod GPU Pod (proof of concept)

Scripts to run KimiLM's engine and window on a rented NVIDIA GPU and measure it. Nothing in the app or engine is
changed: the engine runs exactly as the desktop app starts it, and you open its window in your own browser through
an SSH tunnel.

| Script | What it does |
|---|---|
| `setup.sh` | Once per Pod (15-25 min): Python 3.12 + the engine's packages, llama.cpp at the pinned tag built with CUDA for this card only (into the data folder, where the engine finds it as the `cuda` runtime), and the window (`app/dist`, without Electron). Everything under `/workspace`. Re-runnable. |
| `start.sh` | Starts the engine on `127.0.0.1:8188` with a token kept in `/workspace/kimilm-token`, starts the GPU log, arms an automatic Pod stop after `KIMI_STOP_AFTER_HOURS` (default 3; `0` = off; needs `runpodctl` to reach the Pod), and prints the tunnel command and the address. |
| `gpu-watch.sh` | `start` / `stop` / `report`: nvidia-smi every 2 s to `/workspace/kimilm-gpu.csv`; the report gives peak memory and GPU use. |
| `bench.sh` | `llama-bench` (pp512, tg128, all layers on the GPU) for every downloaded model, appended to `/workspace/kimilm-bench.md`. |

Quick start on the Pod (template: **Runpod PyTorch**, volume disk 40 GB, public IP, TCP 22):

```bash
cd /workspace
git clone https://github.com/kiplabat/KimiLM.git
bash KimiLM/deploy/runpod/setup.sh
bash KimiLM/deploy/runpod/start.sh
```

On your computer: `ssh -N -L 8188:127.0.0.1:8188 root@<pod ip> -p <ssh port> -i ~/.ssh/id_ed25519`, then open the
printed `http://localhost:8188/?token=…`.

Notes:
- Secrets you add in the app are kept in `<data>/secrets.json` (`DVA_SECRETS=file`): a Pod has no keyring.
- Browser tools (Playwright) are not set up; KimiOS's own services (devices, snapshots) are not present.
- `KIMI_CPU_ONLY=1 KIMI_WORK=/tmp/x bash setup.sh` builds a CPU runtime, for trying the scripts without a GPU.
- **Stop** keeps `/workspace` (volume disk billed while stopped); **Terminate** deletes it and ends all charges.
