"""Stand-in for stable-diffusion.cpp's sd-cli in the tests. It accepts exactly the options KimiLM may pass (as
read from the program's source at the pinned release), so an option that does not exist there fails here too, and
it writes real PNG files whose text records what it was asked, for the tests to read.

Marks in the prompt: "out of memory" fails the way the real program does when memory runs out; "slow picture"
takes long enough to be stopped; "long steps" is silent for half a minute a step and says a step takes 223 s, as
a processor does (for looking at what the windows say meanwhile)."""
import json
import sys
import time
from pathlib import Path

from PIL import Image, PngImagePlugin

VALUE = {"-M": "mode", "--mode": "mode", "-m": "model", "--model": "model", "--diffusion-model": "diffusion_model",
         "--vae": "vae", "--llm": "llm", "--clip_l": "clip_l", "--clip_g": "clip_g", "--t5xxl": "t5xxl", "--tae": "tae",
         "-p": "prompt", "--prompt": "prompt", "-n": "negative", "--negative-prompt": "negative", "-W": "width",
         "--width": "width", "-H": "height", "--height": "height", "--steps": "steps", "--cfg-scale": "cfg",
         "--guidance": "guidance", "-s": "seed", "--seed": "seed", "-b": "count", "--batch-count": "count",
         "-o": "output", "--output": "output", "-i": "init", "--init-img": "init", "--mask": "mask",
         "--strength": "strength", "--sampling-method": "sampler", "--scheduler": "scheduler", "-t": "threads",
         "--threads": "threads", "--backend": "backend", "--max-vram": "max_vram", "--ref-image-args": "ref_args",
         "--upscale-model": "upscale_model", "--lora-model-dir": "lora_dir", "--log-level": "log_level",
         "--video-frames": "frames", "--fps": "fps", "--flow-shift": "flow_shift", "--clip_vision": "clip_vision"}
MANY = {"-r": "refs", "--ref-image": "refs"}
FLAGS = {"--offload-to-cpu", "--vae-tiling", "--auto-fit", "--mmap", "-v", "--disable-image-metadata"}
FILES = ("model", "diffusion_model", "vae", "llm", "clip_l", "clip_g", "t5xxl", "tae", "init", "mask")


def fail(text, code=1):
    print(f"[ERROR] main.cpp:1 - {text}", flush=True)
    sys.exit(code)


def main(argv):
    a, i = {"refs": [], "flags": []}, 0
    while i < len(argv):
        arg = argv[i]
        if arg in VALUE or arg in MANY:
            if i + 1 >= len(argv):
                fail(f'error: missing parameter for argument "{arg}"')
            if arg in MANY:
                a[MANY[arg]].append(argv[i + 1])
            else:
                a[VALUE[arg]] = argv[i + 1]
            i += 2
        elif arg in FLAGS:
            a["flags"].append(arg)
            i += 1
        else:
            fail(f"error: unknown argument: {arg}")
    if not a.get("prompt"):
        fail("error: the following arguments are required: prompt")
    if not (a.get("model") or a.get("diffusion_model")):
        fail("error: the following arguments are required: model or diffusion-model")
    for key in FILES:
        if a.get(key) and not Path(a[key]).is_file():
            fail(f"failed to open '{a[key]}'")
    for ref in a["refs"]:
        if not Path(ref).is_file():
            fail(f"failed to open '{ref}'")
    print("[INFO ] stable-diffusion.cpp stand-in", flush=True)
    if "out of memory" in a["prompt"]:
        print("[ERROR] ggml.c:1 - ggml_aligned_malloc: failed to allocate 9000.00 MB", flush=True)
        sys.exit(1)
    steps, count = int(a.get("steps", 20)), int(a.get("count", 1))
    width, height = int(a.get("width", 512)), int(a.get("height", 512))
    pause = 0.5 if "slow picture" in a["prompt"] else 30 if "long steps" in a["prompt"] else 0.01
    seed0 = int(a.get("seed", 42))
    # What the real program prints (copied from a run of the pinned release on Windows, 18 Nov 2026): bars in
    # MB/s while it loads a model, then for each picture a line "generating image" and one bar line a step.
    # A redraw does fewer steps than were asked for: 6 steps at strength 0.6 are 4.
    for done in (16, 66, 106):
        print(f"|{'#' * (done * 50 // 106):<50}| {done}/106 - {90 + done:.2f}MB/s", flush=True)
    print("[I] loading tensors completed, taking 0.79s (read: 0.64s, memcpy: 0.00s, convert: 0.04s, "
          "copy_to_backend: 0.00s) --- model_loader.cpp:1442", flush=True)
    total = steps if not a.get("init") else int(steps * float(a.get("strength", 0.75))) + 1
    if a.get("init"):
        print("[I] IMG2IMG --- image.cpp:201", flush=True)
        print(f"[I] target t_enc is {total - 1} steps --- image.cpp:245", flush=True)
    rate = "223.44s/it" if "long steps" in a["prompt"] else "1.20s/it"
    for n in range(count):
        if a.get("mode") != "vid_gen":
            print(f"[I] generating image: {n + 1}/{count} - seed {seed0 + n} --- image.cpp:1005", flush=True)
        for step in range(1, total + 1):
            time.sleep(pause)
            bar = ("=" * (step * 50 // total) + ">").ljust(50)[:50]
            print(f"|{bar}| {step}/{total} - {rate} - ETA 00:0{total - step}", flush=True)
    out = a.get("output", "./output.png")
    seed = int(a.get("seed", 42))
    if a.get("mode") == "vid_gen":           # a clip: the real program writes .avi (or .webm) frames, as here
        import av
        path = Path(out)
        if path.suffix.lower() not in (".avi", ".webm"):
            path = path.with_suffix(".avi")
        path.parent.mkdir(parents=True, exist_ok=True)
        (path.parent / "asked.json").write_text(json.dumps(a))
        Path(a.get("diffusion_model")).with_name("asked-video.json").write_text(json.dumps(a))
        frames, fps = int(a.get("frames", 33)), int(a.get("fps", 24))
        with av.open(str(path), "w") as box:
            stream = box.add_stream("mjpeg", rate=fps)
            stream.width, stream.height, stream.pix_fmt = width, height, "yuvj420p"
            for n in range(frames):
                frame = av.VideoFrame.from_image(Image.new("RGB", (width, height), ((seed + n * 7) % 256, (n * 5) % 256, 120)))
                for packet in stream.encode(frame):
                    box.mux(packet)
            for packet in stream.encode(None):
                box.mux(packet)
        print(f"[INFO ] main.cpp:560 - save result video to '{path}' (success)", flush=True)
        return 0
    for n in range(count):
        path = Path(out % n if "%" in out else out)
        path.parent.mkdir(parents=True, exist_ok=True)
        info = PngImagePlugin.PngInfo()
        info.add_text("asked", json.dumps(a))
        colour = ((seed + n) * 53 % 256, (seed + n) * 97 % 256, (seed + n) * 151 % 256)
        Image.new("RGB", (width, height), colour).save(path, pnginfo=info)
        print(f"[INFO ] main.cpp:501 - save result image {n} to '{path}' (success)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
