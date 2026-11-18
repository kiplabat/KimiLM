"""Video (2.14.0): make videos, change them, and keep them.

Four ways to a video, all of them tasks with a plan and a trail (see jobs.py):

  assembled   The video KimiLM builds itself, entirely on this computer: scenes, each a picture (made by the image
              model, given as a file, or a title card when there is neither) with a narration (the voices of Sound),
              slow movement over the picture, captions in step with the voice, and music underneath. A text model
              can write the scenes from an idea or a document (`storyboard`). No video model is needed.
  edited      An existing video cut to a part, joined with others, given another sound or music under its own, and
              given subtitles (written by the transcription model from what is said in it).
  model-made  A short clip a video model makes from a description: on this computer with stable-diffusion.cpp
              (`sd-cli -M vid_gen`, a Wan model: slow without a graphics card), or online with Google
              (gemini-omni-1.1-flash on the interactions API). OpenAI's video service (Sora) ended in September
              2026 and Google's Veo previews in October 2026: neither is offered.
  recorded    From the camera or the screen, in the Video window; kept in the library like the others.

Encoding is PyAV (the FFmpeg libraries it ships: H.264 video, AAC sound, in an .mp4)."""
import asyncio
import base64
import json
import os
import random
import re
import shlex
import shutil
import time
from fractions import Fraction
from pathlib import Path

import httpx

from .db import new_id, now
from .jobs import turn
from .providers import cloud_id, month_key
from .sound import SoundError, read_pcm, slug, srt

RATE = 44100
FPS = 24
SHAPES = {"landscape": (1280, 720), "portrait": (720, 1280), "square": (960, 960)}
VIDEO_EXT = (".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v")
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
GOOGLE_MODEL = "gemini-omni-1.1-flash"
NO_WINDOW = 0x08000000 if os.name == "nt" else 0
# stable-diffusion.cpp options of a video model's files (read from the pinned release's own notes on Wan)
SLOT_FLAGS = {"diffusion_model": "--diffusion-model", "vae": "--vae", "t5xxl": "--t5xxl", "clip_vision": "--clip_vision"}


class VideoError(Exception):
    pass


class Stopped(Exception):
    pass


# ---------------------------------------------------------------------- drawing
def font(size):
    from PIL import ImageFont
    for path in ("C:/Windows/Fonts/seguisb.ttf", "C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/arial.ttf",
                 "/System/Library/Fonts/Helvetica.ttc", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def wrap(draw, text, fnt, width):
    lines, line = [], ""
    for word in str(text).split():
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=fnt) <= width or not line:
            line = trial
        else:
            lines.append(line)
            line = word
    return lines + ([line] if line else [])


def caption_image(text, size):
    """The caption as a see-through picture of the frame's size: white words on a dark rounded band, near the bottom."""
    from PIL import Image, ImageDraw
    w, h = size
    fnt = font(max(18, round(min(w, h) / 19)))
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    lines = wrap(draw, text, fnt, int(w * 0.84))[:3]
    if not lines:
        return layer
    line_h = int(fnt.size * 1.28)
    box_w = int(max(draw.textlength(ln, font=fnt) for ln in lines)) + int(fnt.size * 1.1)
    box_h = line_h * len(lines) + int(fnt.size * 0.5)
    left, top = (w - box_w) // 2, h - box_h - int(h * 0.07)
    draw.rounded_rectangle((left, top, left + box_w, top + box_h), radius=int(fnt.size * 0.45), fill=(0, 0, 0, 165))
    for i, ln in enumerate(lines):
        draw.text((w / 2, top + int(fnt.size * 0.25) + i * line_h + line_h / 2), ln, font=fnt, fill=(255, 255, 255, 255),
                  anchor="mm")
    return layer


def card(text, size, index=0):
    """A scene without a picture: its words on a calm background."""
    from PIL import Image, ImageDraw
    w, h = size
    tones = [((28, 36, 54), (64, 46, 86)), ((22, 48, 52), (30, 76, 70)), ((58, 36, 30), (110, 56, 40)), ((30, 30, 36), (70, 70, 84))]
    a, b = tones[index % len(tones)]
    small = Image.new("RGB", (2, 2))
    small.putdata([a, a, b, b])
    img = small.resize(size, Image.BILINEAR)
    draw = ImageDraw.Draw(img)
    fnt = font(max(24, round(min(w, h) / 11)))
    lines = wrap(draw, text, fnt, int(w * 0.8))[:5]
    line_h = int(fnt.size * 1.3)
    top = (h - line_h * len(lines)) / 2
    for i, ln in enumerate(lines):
        draw.text((w / 2, top + i * line_h + line_h / 2), ln, font=fnt, fill=(255, 255, 255), anchor="mm")
    return img


def cover(img, size):
    """The picture filling the frame (cut at the sides or at top and bottom, never stretched)."""
    from PIL import Image
    w, h = size
    img = img.convert("RGB")
    scale = max(w / img.width, h / img.height)
    img = img.resize((max(w, round(img.width * scale)), max(h, round(img.height * scale))), Image.LANCZOS)
    left, top = (img.width - w) // 2, (img.height - h) // 2
    return img.crop((left, top, left + w, top + h))


def contain(img, size):
    """The picture whole inside the frame, with black bars where the shapes differ."""
    from PIL import Image
    w, h = size
    img = img.convert("RGB")
    if img.size == (w, h):
        return img
    scale = min(w / img.width, h / img.height)
    small = img.resize((max(2, round(img.width * scale)), max(2, round(img.height * scale))), Image.BILINEAR)
    out = Image.new("RGB", size, (0, 0, 0))
    out.paste(small, ((w - small.width) // 2, (h - small.height) // 2))
    return out


def caption_chunks(text, start, end, words=9):
    """A sentence cut into captions that can be read at a glance, each with its share of the time."""
    parts, chunk = [], []
    for word in str(text).split():
        chunk.append(word)
        if len(chunk) >= words or (len(chunk) >= 4 and word[-1:] in ".!?;:,"):
            parts.append(" ".join(chunk))
            chunk = []
    if chunk:
        if parts and len(chunk) < 3:
            parts[-1] += " " + " ".join(chunk)
        else:
            parts.append(" ".join(chunk))
    total = sum(len(p) for p in parts) or 1
    out, at = [], float(start)
    for p in parts:
        length = (end - start) * len(p) / total
        out.append({"start": round(at, 2), "end": round(at + length, 2), "text": p})
        at += length
    return out


# ---------------------------------------------------------------------- frames
def scene_frames(scenes, size, fps, motion=True):
    """Every frame of an assembled video: a slow move over each scene's picture, and a short blend between scenes."""
    from PIL import Image
    w, h = size
    room = 1.12 if motion else 1.0
    previous = None
    for i, scene in enumerate(scenes):
        if scene.get("image") and Path(scene["image"]).is_file():
            with Image.open(scene["image"]) as img:
                base = cover(img, (round(w * room), round(h * room)))
        else:
            base = cover(card(scene.get("card") or scene.get("caption") or scene.get("narration") or "", size, i),
                         (round(w * room), round(h * room)))
        count = max(1, round(float(scene["seconds"]) * fps))
        blend = min(round(0.4 * fps), count // 3) if previous is not None else 0
        still = None
        for n in range(count):
            if motion:
                p = n / max(1, count - 1)
                p = p if i % 2 == 0 else 1 - p                      # in on one scene, out on the next
                cw, ch = base.width / (1 + (room - 1) * p), base.height / (1 + (room - 1) * p)
                drift = (base.width - cw) * (0.5 + 0.18 * (p - 0.5) * (1 if i % 4 < 2 else -1))
                box = (drift, (base.height - ch) / 2, drift + cw, (base.height - ch) / 2 + ch)
                frame = base.resize(size, Image.BILINEAR, box=box)
            else:
                still = still or base.resize(size, Image.BILINEAR)
                frame = still
            if n < blend:
                frame = Image.blend(previous, frame, (n + 1) / (blend + 1))
            yield frame
        previous = frame


def with_captions(frames, captions, size, fps):
    """Lay each caption over the frames of its time."""
    made, n, at = {}, 0, 0
    captions = sorted(captions or [], key=lambda c: c["start"])
    for frame in frames:
        t = n / fps
        while at < len(captions) and captions[at]["end"] <= t:
            at += 1
        if at < len(captions) and captions[at]["start"] <= t:
            text = captions[at]["text"]
            if text not in made:
                made.clear()                                        # one caption's picture at a time
                made[text] = caption_image(text, size)
            frame = frame.copy()
            frame.paste(made[text], (0, 0), made[text])
        yield frame
        n += 1


def info(path):
    """What a video file is: its length, size, frames a second, and whether it has sound."""
    import av
    try:
        with av.open(str(path)) as box:
            vs = next((s for s in box.streams if s.type == "video"), None)
            if vs is None:
                raise VideoError(f"{Path(path).name} has no picture in it")
            seconds = float(box.duration or 0) / 1_000_000 or float((vs.duration or 0) * (vs.time_base or 0))
            if seconds <= 0:                 # a recording from the browser often does not say how long it is: look
                for packet in box.demux(vs):
                    if packet.pts is not None and vs.time_base:
                        seconds = max(seconds, float(packet.pts * vs.time_base))
            rate = float(vs.average_rate or vs.guessed_rate or FPS)
            if not 1 <= rate <= 240:
                rate = FPS
            return {"seconds": round(seconds, 2), "width": vs.codec_context.width, "height": vs.codec_context.height,
                    "fps": round(rate, 2), "audio": any(s.type == "audio" for s in box.streams)}
    except VideoError:
        raise
    except Exception as exc:
        raise VideoError(f"{Path(path).name} could not be read as a video ({type(exc).__name__})") from exc


def sample(path, start, end, fps, size, fit=contain):
    """The frames of a part of a video file, at an even rate: at each moment the newest frame the file has."""
    import av
    with av.open(str(path)) as box:
        vs = next(s for s in box.streams if s.type == "video")
        if start > 2:
            box.seek(int((start - 1) * 1_000_000))
        n, held, picture = 0, None, None
        for frame in box.decode(vs):
            t = frame.time
            if t is None:
                continue
            while held is not None and start + n / fps < t:
                if start + n / fps >= end:
                    return
                picture = picture or fit(held.to_image(), size)
                yield picture
                n += 1
            if t >= end:
                return
            held, picture = frame, None
        last = (held.time or 0) + 1 / fps if held is not None else 0
        while held is not None and start + n / fps < min(end, last):   # the file's last frame, for its own time
            picture = picture or fit(held.to_image(), size)
            yield picture
            n += 1


def write_video(dest, frames, fps, size, audio=None, total=0, progress=None, stop=None):
    """Encode frames (PIL pictures) and sound (float32 samples (n, 2) at 44.1 kHz, or None) into an .mp4."""
    import av
    import numpy as np
    w, h = size
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    sound_on = audio is not None and len(audio) > 0
    with av.open(str(dest), "w", options={"movflags": "faststart"}) as box:
        vs = box.add_stream("libx264", rate=int(fps))
        vs.width, vs.height, vs.pix_fmt = w, h, "yuv420p"
        vs.options = {"crf": "21", "preset": "veryfast"}
        vs.codec_context.time_base = Fraction(1, int(fps))
        if sound_on:
            ast = box.add_stream("aac", rate=RATE, layout="stereo")
            ast.bit_rate = 160000
            ast.codec_context.time_base = Fraction(1, RATE)
            fifo = av.AudioFifo()
            resampler = av.AudioResampler(format=ast.codec_context.format.name, layout="stereo", rate=RATE)
            pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2")
        sent, played = 0, 0

        def push(until, last=False):
            nonlocal sent, played
            until = min(int(until), len(pcm))
            if until > sent:
                block = av.AudioFrame.from_ndarray(np.ascontiguousarray(pcm[sent:until].reshape(1, -1)), format="s16",
                                                   layout="stereo")
                block.sample_rate = RATE
                for part in resampler.resample(block):
                    part.pts = None
                    fifo.write(part)
                sent = until
            step = ast.codec_context.frame_size or 1024
            while fifo.samples >= step or (last and fifo.samples):
                part = fifo.read(min(step, fifo.samples))
                part.sample_rate, part.pts, part.time_base = RATE, played, Fraction(1, RATE)
                played += part.samples
                for packet in ast.encode(part):
                    box.mux(packet)
        n = 0
        for img in frames:
            if stop and stop():
                raise Stopped()
            frame = av.VideoFrame.from_image(img if img.size == (w, h) else img.resize((w, h)))
            frame.pts, frame.time_base = n, Fraction(1, int(fps))
            for packet in vs.encode(frame):
                box.mux(packet)
            n += 1
            if sound_on and n % int(fps) == 0:
                push(n / fps * RATE)
            if progress and n % 6 == 0:
                progress(n, total)
        for packet in vs.encode(None):
            box.mux(packet)
        if sound_on:
            push(len(pcm), last=True)
            for packet in ast.encode(None):
                box.mux(packet)
    if progress:
        progress(n, max(total, n))
    return n


def even(n):
    return max(2, int(n) // 2 * 2)


def place(track, samples, at, volume=1.0):
    """Add samples into the track from second `at` (the track grows when they reach past its end)."""
    import numpy as np
    start = int(at * RATE)
    need = start + len(samples)
    if need > len(track):
        track = np.concatenate([track, np.zeros((need - len(track), 2), dtype="float32")])
    track[start:need] += samples * volume
    return track


# ---------------------------------------------------------------------- the model-made clip on this computer
RECIPES = [
    {"key": "wan21-t2v-small", "name": "Wan 2.1 (text to video, small)", "approx_gb": 7.3, "min_ram_gb": 16,
     "about": "The smallest open video model: a clip of about two seconds at 832 × 480 from a description. On a "
              "graphics card it takes minutes; on the processor alone it can take an hour or more a clip. For "
              "explainers and stories the assembled video (pictures, voice and music) is far quicker and needs no "
              "video model.",
     "licence": "Apache-2.0", "page": "https://huggingface.co/Wan-AI/Wan2.1-T2V-1.3B",
     "defaults": {"video": True, "steps": 20, "cfg": 6.0, "width": 832, "height": 480, "frames": 33, "fps": 16,
                  "flow_shift": 3.0, "args": "--offload-to-cpu"},
     "parts": [
         {"slot": "diffusion_model", "sources": [{"repo": "Comfy-Org/Wan_2.1_ComfyUI_repackaged",
                                                  "match": r"diffusion_models/wan2\.1_t2v_1\.3B_fp16\.safetensors$"}]},
         {"slot": "vae", "sources": [{"repo": "Comfy-Org/Wan_2.1_ComfyUI_repackaged", "match": r"vae/wan_2\.1_vae\.safetensors$"}]},
         {"slot": "t5xxl", "sources": [{"repo": "city96/umt5-xxl-encoder-gguf", "match": r"\.gguf$"}],
          "prefer": ["Q5_K_M", "Q4_K_M", "Q5_K_S", "Q4_K_S", "Q6_K", "Q8_0"]}]},
]


class Video:
    def __init__(self, app):
        self.app = app
        self.db = app.db
        self.jobs = {}       # the Video window's own view of its requests (the tasks themselves are in app.jobs)
        self.tasks = {}
        self.downloads = {}

    # ------------------------------------------------------------------ what there is
    def folder(self):
        folder = self.app.paths.data / "video"
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def models(self):
        """Video models on this computer: kept with the image models (the same engine runs them), marked as video."""
        rows = [r for r in self.db.all("SELECT * FROM image_models ORDER BY added") if (r.get("settings") or {}).get("video")]
        for r in rows:
            r["missing"] = [slot for slot, path in (r.get("files") or {}).items() if not Path(path).exists()]
            r["ready"] = not r["missing"] and bool(self.app.images.engines())
        return rows

    def online(self):
        on = bool(self.db.setting("video_online", False))
        return {"provider": "google", "name": "Google", "model": self.db.setting("video_online_model", GOOGLE_MODEL),
                "key": self.app.providers.ready("google"), "on": on, "ready": on and self.app.providers.ready("google"),
                "cost": float(self.db.setting("video_clip_cost", 1.0))}

    def can(self):
        sound, images = self.app.sound.can(), self.app.images.available()
        local = any(m["ready"] for m in self.models())
        return {"assemble": True, "voice": bool(sound.get("tts")), "pictures": bool(images), "music": bool(sound.get("music")),
                "subtitles": bool(sound.get("stt")), "clip_local": local, "clip_online": self.online()["ready"],
                "clip": local or self.online()["ready"], "write": bool(self.app.router.pick("worker")[0])}

    def recipes(self):
        try:
            import psutil
            ram_gb = psutil.virtual_memory().total / 2**30
        except Exception:
            ram_gb = 0
        have = {m.get("recipe") for m in self.models()}
        return [{k: r[k] for k in ("key", "name", "about", "approx_gb", "min_ram_gb", "licence", "page")}
                | {"installed": r["key"] in have, "fits": not ram_gb or ram_gb >= r["min_ram_gb"] * 0.93,
                   "download": {k: v for k, v in (self.downloads.get(r["key"]) or {}).items() if k != "cancel"} or None}
                for r in RECIPES]

    def status(self):
        return {"can": self.can(), "online": self.online(), "models": self.models(), "recipes": self.recipes(),
                "engine": bool(self.app.images.engines()), "jobs": list(self.jobs.values())[-8:],
                "shapes": {k: list(v) for k, v in SHAPES.items()},
                "timeout_minutes": self.db.setting("video_timeout_minutes", 240),
                "notes": {"openai": "OpenAI's video service (Sora) ended in September 2026: it is not offered.",
                          "cost": "KimiLM does not know Google's price for a clip. Each clip is counted against the "
                                  "monthly online budget with the amount set here until you correct it."}}

    def library(self, limit=60, chat_id=None):
        sql, args = "SELECT * FROM media WHERE kind='video'", []
        if chat_id:
            sql += " AND chat_id=?"
            args.append(chat_id)
        rows = self.db.all(sql + " ORDER BY created DESC LIMIT ?", [*args, limit])
        return [{**r, "name": Path(r["path"]).name, "exists": Path(r["path"]).exists()} for r in rows]

    def _record(self, path, prompt, model_name, params, chat_id, source):
        row = {"id": new_id("med_"), "kind": "video", "path": str(path), "chat_id": chat_id, "prompt": str(prompt)[:4000],
               "model": model_name, "model_name": model_name, "source": source, "params": params, "created": now()}
        self.db.insert("media", row)
        self.app.bus.publish("video_changed")
        return {**row, "name": Path(path).name}

    def forget(self, media_id, delete_file=False):
        row = self.db.get("media", media_id)
        if not row:
            raise KeyError(media_id)
        if delete_file:
            Path(row["path"]).unlink(missing_ok=True)
            Path(row["path"]).with_suffix(".srt").unlink(missing_ok=True)
        self.db.delete("media", media_id)
        self.app.bus.publish("video_changed")

    def keep(self, path, name=None, source="recorded", chat_id=None):
        """A video that came from outside (a recording from the camera or the screen, a file): put it in the library."""
        path = Path(path)
        facts = info(path)
        return self._record(path, name or path.stem, "", {"mode": source, **facts}, chat_id, source)

    def set_online(self, on=None, model=None, cost=None):
        if on is not None:
            self.db.set_setting("video_online", bool(on))
        if model:
            self.db.set_setting("video_online_model", str(model).strip().removeprefix("models/"))
        if cost is not None:
            self.db.set_setting("video_clip_cost", max(0.0, float(cost)))
        self.app.bus.publish("video_changed")

    # ------------------------------------------------------------------ a video model for this computer
    def start_download(self, key):
        recipe = next((r for r in RECIPES if r["key"] == key), None)
        if not recipe:
            raise KeyError(key)
        state = self.downloads.get(key)
        if state and state["state"] in ("starting", "downloading"):
            return state
        state = self.downloads[key] = {"key": key, "name": recipe["name"], "state": "starting", "done": 0, "total": 0,
                                       "file": "", "error": None, "cancel": False}

        async def run():
            # the image module's own download: the same files, folders and retries; the model is marked as video
            await self.app.images._download(recipe, state)
            self.app.bus.publish("video_changed")
        self.app.jobs.follow("download", f"Download: {recipe['name']} (video model)", run(), lambda: state,
                             stop=lambda: state.update(cancel=True), payload={"what": "video model", "key": key})
        return state

    def cancel_download(self, key):
        if key in self.downloads:
            self.downloads[key]["cancel"] = True

    def add_local(self, name, files, settings=None):
        """A video model whose files are already on this computer (used in place)."""
        files = {slot: str(Path(path)) for slot, path in (files or {}).items() if path and slot in SLOT_FLAGS}
        if "diffusion_model" not in files:
            raise ValueError("Give the video model's files: the diffusion model, its VAE and its text encoder (t5xxl)")
        before = {m["id"] for m in self.models()}
        self.app.images.add_local(name, files, {**RECIPES[0]["defaults"], **(settings or {}), "video": True}, edit="none")
        self.app.bus.publish("video_changed")
        return next(m for m in self.models() if m["id"] not in before)

    def remove_model(self, model_id):
        row = next((m for m in self.models() if m["id"] == model_id), None)
        if not row:
            raise KeyError(model_id)
        self.db.delete("image_models", model_id)
        self.app.bus.publish("video_changed")

    # ------------------------------------------------------------------ 1. the scenes, written by a text model
    async def storyboard(self, *, idea="", sources=(), text="", minutes=1.0, style="", model=None, job_id=None,
                         on_status=None):
        """Scenes for an assembled video, from an idea or from documents: {"title", "scenes": [{"narration",
        "picture" (what the scene shows, for the image model), "caption"}]}."""
        from .knowledge import extract_text
        status, track = self.app.jobs.reporter(on_status, job_id)
        track.stages(["Read", "Write the scenes"])
        track.at("Read")
        docs, names = [], []
        for path in sources or ():
            path = Path(path)
            if not path.is_file():
                raise VideoError(f"There is no file at {path}")
            status(f"Reading {path.name}…")
            try:
                docs.append(await asyncio.to_thread(extract_text, path))
            except ValueError as exc:
                raise VideoError(f"{path.name}: {exc}") from exc
            names.append(path.name)
        if text and text.strip():
            docs.append(text.strip())
            names.append("the text you gave")
        full = re.sub(r"[ \t\xa0]+", " ", "\n\n".join(docs)).strip()
        idea = (idea or "").strip()
        if not idea and len(full) < 100:
            raise VideoError("Say what the video is about, or give a document to make it from.")
        material = full
        if len(full) > 9000:                # the beginning, the middle and the end, as much as a small model can hold
            third = 3000
            mid = len(full) // 2
            material = full[:third] + "\n[…]\n" + full[mid - third // 2: mid + third // 2] + "\n[…]\n" + full[-third:]
            track.note(f"The material is long ({len(full):,} characters): the scenes are written from its beginning, "
                       "middle and end (9,000 characters).")
        minutes = max(0.25, min(10.0, float(minutes or 1)))
        count = max(2, min(16, round(minutes * 60 / 9)))
        model_id = model if model and model != "auto" else self.app.router.pick("worker")[0]
        if not model_id:
            raise VideoError("No text model is installed to write the scenes. Download one in Models, or write the "
                             "scenes yourself.")
        writer = (self.app.models.get_any(model_id) or {}).get("name") or model_id
        track.plan(storyboard={"about": idea[:300] or None, "documents": names or None, "minutes": minutes, "scenes": count,
                               "style": style or None, "writer": writer})
        track.at("Write the scenes")
        status(f"Starting the text model ({writer})…")
        written = [0, 0.0]

        def token(kind, value):
            written[0] += len(str(value))
            if time.monotonic() - written[1] > 1.0:
                written[1] = time.monotonic()
                status.beat(f"Writing the scenes · about {written[0] // 6} words {'thought' if kind == 'reasoning' else 'written'} so far")
        prompt = (
            f"Plan a short video of about {minutes * 60:.0f} seconds as {count} scenes"
            + (f" about: {idea}." if idea else " about the material below.")
            + (f" Style: {style}." if style else "")
            + " For each scene give: narration (what the voice says: one or two short spoken sentences, about 20 words, "
              "plain language, numbers the way people say them), picture (what the scene shows, described for an image "
              "model in one sentence: the subject, the setting, the light, the style; no text or letters in the picture), "
              "and caption (at most six words). The first scene opens the subject, the last one closes it. "
            + ("Use only what the material says; do not invent facts, names or numbers.\n\nMATERIAL:\n" + material if material
               else "") + "\n\nAlso give the video a short title. JSON only.")
        schema = {"type": "object", "required": ["title", "scenes"], "properties": {
            "title": {"type": "string"},
            "scenes": {"type": "array", "minItems": 1, "maxItems": 16, "items": {
                "type": "object", "required": ["narration", "picture"], "properties": {
                    "narration": {"type": "string"}, "picture": {"type": "string"}, "caption": {"type": "string"}}}}}}
        async with self.app.runtime.lease(model_id, as_loaded=True) as server:
            status("Writing the scenes…")
            got = await self.app.llm.json(
                [{"role": "system", "content": "You plan short videos: clear, concrete, made to be watched. You are accurate."},
                 {"role": "user", "content": prompt}], schema, max_tokens=count * 130 + 300, temperature=0.6,
                server=server, on_token=token)
        scenes = [{"narration": str(s.get("narration") or "").strip()[:600], "picture": str(s.get("picture") or "").strip()[:500],
                   "caption": str(s.get("caption") or "").strip()[:80]}
                  for s in got.get("scenes") or [] if str(s.get("narration") or s.get("picture") or "").strip()][:16]
        if not scenes:
            raise VideoError("The model wrote no scenes. Try again, or use a larger text model.")
        title = str(got.get("title") or idea or (names[0] if names else "Video")).strip()[:120]
        track.plan(scenes=[{"narration": s["narration"], "picture": s["picture"]} for s in scenes])
        track.note(f"Wrote “{title}” in {len(scenes)} scenes.")
        return {"title": title, "scenes": scenes, "sources": names, "writer": writer}

    # ------------------------------------------------------------------ 1. the assembled video
    async def make(self, scenes, *, out_dir, title="", shape="landscape", voice=None, music=None, captions=True,
                   motion=True, chat_id=None, job_id=None, on_status=None):
        """Build a video from scenes: [{"narration", "picture" (a description: the image model makes it), "image" (a
        picture file, used as it is), "caption", "seconds" (for a scene without narration)}]. `music` is a
        recording's path, a description (the music model makes it), or None. Returns the video's record."""
        import numpy as np
        status, track = self.app.jobs.reporter(on_status, job_id)
        scenes = [dict(s) for s in scenes or [] if isinstance(s, dict)
                  and (str(s.get("narration") or "").strip() or s.get("image") or str(s.get("picture") or "").strip()
                       or str(s.get("caption") or "").strip())][:40]
        if not scenes:
            raise VideoError("A video needs at least one scene: a narration, a picture, or both.")
        size = SHAPES.get(shape) or SHAPES["landscape"]
        can = self.can()
        speak = can["voice"] and any(str(s.get("narration") or "").strip() for s in scenes)
        draw = can["pictures"] and any(str(s.get("picture") or "").strip() and not s.get("image") for s in scenes)
        tune = bool(music) and (Path(str(music)).is_file() or can["music"])
        title = (title or str(scenes[0].get("caption") or scenes[0].get("narration") or "Video"))[:120]
        track.stages((["Make the pictures"] if draw else []) + (["Record the narration"] if speak else [])
                     + (["Make the music"] if tune and not Path(str(music)).is_file() else []) + ["Put it together", "Save"])
        missing = []
        if not can["voice"] and any(str(s.get("narration") or "").strip() for s in scenes):
            missing.append("no voice is set up (Sound → Voices & models): the narration is shown as captions only")
        if not can["pictures"] and any(str(s.get("picture") or "").strip() and not s.get("image") for s in scenes):
            missing.append("no image model is set up (Images): scenes without a picture file become title cards")
        if music and not tune:
            missing.append("no music model is set up (Sound): the video has no music")
        track.plan(video={"title": title, "scenes": len(scenes), "shape": f"{shape} ({size[0]} × {size[1]})",
                          "narration": "the voices of Sound" if speak else "none", "pictures": "made by the image model" if draw
                          else "the files given, or title cards", "music": (Path(str(music)).name if Path(str(music)).is_file()
                                                                           else "made by the music model") if tune else "none",
                          "captions": bool(captions), "movement": bool(motion), "missing": missing or None},
                   scenes=[{"narration": str(s.get("narration") or "")[:300], "picture": str(s.get("picture") or s.get("image") or "")[:200]}
                           for s in scenes])
        for note in missing:
            track.note("Note: " + note + ".")
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        work = self.app.paths.data / "tmp" / new_id("vid_")
        work.mkdir(parents=True)
        started = time.monotonic()
        made_pictures = []
        try:
            # pictures
            if draw:
                track.at("Make the pictures")
            for i, s in enumerate(scenes):
                given = str(s.get("image") or "").strip()
                if given:
                    if not Path(given).is_file():
                        raise VideoError(f"Scene {i + 1}: there is no picture at {given}")
                    continue
                wanted = str(s.get("picture") or "").strip()
                if wanted and draw:
                    status(f"Picture {i + 1} of {len(scenes)}: {wanted[:70]}")
                    try:
                        records = await self.app.images.generate(wanted, out_dir=out_dir / "scenes", width=even(size[0] * 0.8) // 16 * 16,
                                                                 height=even(size[1] * 0.8) // 16 * 16, chat_id=chat_id,
                                                                 job_id=job_id, on_status=on_status)
                        s["image"] = records[0]["path"]
                        made_pictures.append(records[0]["name"])
                    except Exception as exc:                 # one picture that fails does not lose the video
                        track.note(f"Scene {i + 1}: the picture could not be made ({str(exc)[:200]}); a title card is used.")
            if made_pictures:                                # the plan keeps the sum, not the last picture's details
                track.plan(picture=None, picture_run=None,
                           pictures={"made": len(made_pictures), "model": (self.app.images.default_model() or {}).get("name")})
            # narration
            audio = np.zeros((0, 2), dtype="float32")
            lines, at, voices = [], 0.0, []
            if speak:
                track.at("Record the narration")
            for i, s in enumerate(scenes):
                said = str(s.get("narration") or "").strip()
                length = float(s.get("seconds") or 0)
                s["_start"], s["_captions"] = at, []
                if said and speak:
                    status(f"Narration {i + 1} of {len(scenes)}…")
                    rec = await self.app.sound.speak(said, out_dir=work, voice=voice, fmt="wav", name=f"scene-{i + 1}",
                                                     chat_id=None, job_id=job_id, on_status=on_status)
                    samples = await asyncio.to_thread(read_pcm, rec["path"], RATE, 2)
                    cast = rec["params"].get("voices") or []
                    voices = voices or [str(v) for v in (cast.values() if isinstance(cast, dict) else cast)] or [rec.get("model_name") or ""]
                    self.app.sound.forget(rec["id"], delete_file=True)       # the scene's voice lives on in the video
                    lead = 0.3
                    audio = place(audio, samples, at + lead)
                    spoken = len(samples) / RATE
                    marks = [m for m in rec["params"].get("segments") or [] if str(m.get("text") or "").strip()]
                    if len(marks) > 1 and marks[-1]["end"] <= spoken + 0.5:
                        for m in marks:
                            s["_captions"] += caption_chunks(m["text"], at + lead + m["start"], at + lead + m["end"])
                    else:
                        s["_captions"] = caption_chunks(said, at + lead, at + lead + spoken)
                    length = max(length, spoken + lead + 0.55)
                elif said:                                   # no voice: the words are read, about 2.6 a second
                    length = max(length, 2.0 + len(said.split()) / 2.6)
                    s["_captions"] = caption_chunks(said, at + 0.2, at + length - 0.2)
                else:
                    length = length or 4.0
                    if str(s.get("caption") or "").strip():
                        s["_captions"] = [{"start": at + 0.2, "end": at + length - 0.2, "text": str(s["caption"]).strip()}]
                s["seconds"] = round(max(1.5, min(120.0, length)), 2)
                lines.append({"start": at, "text": said})
                at += s["seconds"]
            total = at
            if speak:
                track.plan(speech=None, narration={"voice": ", ".join(voices) or "the default voice", "scenes_spoken": sum(
                    1 for s in scenes if str(s.get("narration") or "").strip()), "words": sum(len(str(s.get("narration") or "").split()) for s in scenes)})
            # music
            music_name = None
            if tune:
                tune_path = Path(str(music))
                if not tune_path.is_file():
                    track.at("Make the music")
                    status("Making the music…")
                    try:
                        rec = await self.app.sound.music(str(music), out_dir=work, seconds=min(60, max(15, int(total))),
                                                         instrumental=True, chat_id=None, job_id=job_id, on_status=on_status)
                        tune_path, music_name = Path(rec["path"]), "made for this video"
                        self.app.db.delete("media", rec["id"])
                    except Exception as exc:
                        tune_path = None
                        track.note(f"The music could not be made ({str(exc)[:200]}); the video has none.")
                else:
                    music_name = tune_path.name
                if tune_path:
                    bed = await asyncio.to_thread(read_pcm, tune_path, RATE, 2)
                    if len(bed):
                        need = int(total * RATE)
                        bed = np.concatenate([bed] * (need // len(bed) + 1))[:need].copy()
                        level = 0.16 if speak else 0.6
                        fade_in, fade_out = min(len(bed), int(0.6 * RATE)), min(len(bed), int(2.0 * RATE))
                        bed[:fade_in] *= np.linspace(0, 1, fade_in, dtype="float32")[:, None]
                        bed[-fade_out:] *= np.linspace(1, 0, fade_out, dtype="float32")[:, None]
                        audio = place(audio, bed, 0.0, level)
            if len(audio):
                need = int(total * RATE)
                audio = audio[:need] if len(audio) >= need else np.concatenate([audio, np.zeros((need - len(audio), 2), dtype="float32")])
            # together
            track.at("Put it together")
            dest = out_dir / f"{slug(title)[:40]}-{time.strftime('%Y%m%d-%H%M%S')}.mp4"
            frames_total = sum(max(1, round(s["seconds"] * FPS)) for s in scenes)
            all_captions = [c for s in scenes for c in s["_captions"]]
            track.plan(video_run={"length": f"{total:.1f} seconds", "frames": frames_total, "frames_a_second": FPS,
                                  "captions": len(all_captions) if captions else 0, "file": dest.name,
                                  "encoder": "H.264 video, AAC sound (.mp4)"})
            status(f"Putting {len(scenes)} scenes together ({total:.0f} seconds)…")
            await self._encode(dest, lambda: with_captions(scene_frames(scenes, size, FPS, motion), all_captions if captions else [],
                                                           size, FPS),
                               FPS, size, audio if len(audio) else None, frames_total, status, job_id)
            track.at("Save")
            if all_captions:
                dest.with_suffix(".srt").write_text(srt(all_captions), encoding="utf-8")
        finally:
            shutil.rmtree(work, ignore_errors=True)
        params = {"mode": "assembled", "title": title, "seconds": round(total, 2), "width": size[0], "height": size[1],
                  "scenes": [{"narration": str(s.get("narration") or ""), "picture": str(s.get("picture") or ""),
                              "image": s.get("image"), "caption": str(s.get("caption") or ""), "seconds": s["seconds"]} for s in scenes],
                  "voices": voices, "music": music_name, "captions": bool(captions and all_captions),
                  "subtitles": str(dest.with_suffix(".srt")) if all_captions else None, "pictures_made": made_pictures,
                  "took": round(time.monotonic() - started, 1)}
        track.note(f"Saved {dest.name}: {total:.1f} seconds, {len(scenes)} scenes, after {params['took']:g} seconds of work.")
        return self._record(dest, title, "assembled on this computer", params, chat_id, "local")

    async def _encode(self, dest, frames, fps, size, audio, total, status, job_id):
        """Run the encoder in a thread, with its progress as the task's sign of life; stopping the task stops it."""
        loop = asyncio.get_running_loop()
        stop = {"now": False}
        last = [0.0]

        def progress(n, of):
            if time.monotonic() - last[0] > 0.5 or n >= of:
                last[0] = time.monotonic()
                text = f"Encoding: frame {n:,} of {of:,}"
                loop.call_soon_threadsafe(lambda: (status.beat(text),
                                                   job_id and self.app.jobs.progress(job_id, text, fraction=n / max(1, of))))

        def work():
            try:
                write_video(dest, frames(), fps, size, audio, total, progress, lambda: stop["now"])
            except Stopped:
                Path(dest).unlink(missing_ok=True)       # a stopped video leaves no half file
        try:
            await asyncio.to_thread(work)
        except asyncio.CancelledError:
            stop["now"] = True
            raise

    # ------------------------------------------------------------------ 2. changing a video
    async def edit(self, source=None, *, clips=None, out_dir, start=None, end=None, mute=False, sound=None,
                   sound_volume=None, subtitles=None, shape=None, name=None, chat_id=None, job_id=None, on_status=None):
        """Cut a video to a part (`start`, `end` in seconds), join several (`clips`: paths, or {"path", "start",
        "end"}), take its sound away (`mute`), lay a recording over or under it (`sound`, at `sound_volume`), and
        burn subtitles in: `subtitles` is "auto" (written from what is said in the video), a .srt file, or None."""
        import numpy as np
        status, track = self.app.jobs.reporter(on_status, job_id)
        parts = [c if isinstance(c, dict) else {"path": c} for c in (clips or ([{"path": source, "start": start, "end": end}] if source else []))]
        if not parts:
            raise VideoError("Choose the video to change")
        facts = []
        for p in parts:
            path = Path(str(p.get("path") or ""))
            if not path.is_file():
                raise VideoError(f"There is no video at {path}")
            f = await asyncio.to_thread(info, path)
            a = max(0.0, float(p.get("start") or 0))
            b = min(f["seconds"] or 1e9, float(p["end"])) if p.get("end") not in (None, "") else (f["seconds"] or 1e9)
            if b - a < 0.2:
                raise VideoError(f"{path.name}: the part from {a:g} to {b:g} seconds is empty (the video is {f['seconds']:g} seconds long)")
            facts.append({**f, "path": path, "start": a, "end": b})
        if sound and not Path(str(sound)).is_file():
            raise VideoError(f"There is no recording at {sound}")
        first = facts[0]
        size = SHAPES.get(shape) if shape else (even(first["width"]), even(first["height"]))
        fps = max(12, min(60, round(first["fps"] or FPS)))
        total = sum(f["end"] - f["start"] for f in facts)
        auto = subtitles == "auto"
        track.stages((["Write down what is said"] if auto else []) + ["Prepare the sound", "Encode", "Save"])
        track.plan(edit={"videos": [f"{f['path'].name} ({f['start']:g}–{f['end']:g} s of {f['seconds']:g} s)" for f in facts],
                         "length": f"{total:.1f} seconds", "size": f"{size[0]} × {size[1]}", "frames_a_second": fps,
                         "own_sound": "taken away" if mute else "kept", "added_sound": Path(str(sound)).name if sound else None,
                         "subtitles": "written from the video's speech" if auto else Path(str(subtitles)).name if subtitles else None})
        started = time.monotonic()
        captions = []
        if auto:
            if not self.app.sound.can().get("stt"):
                raise VideoError("Subtitles need a transcription model: download Whisper in Sound → Voices & models.")
            track.at("Write down what is said")
            at = 0.0
            for i, f in enumerate(facts):
                if f["audio"]:
                    status(f"Writing down what is said in {f['path'].name}…")
                    heard = await self.app.sound.transcribe(f["path"], job_id=job_id, on_status=on_status)
                    for seg in heard.get("segments") or []:
                        a, b = max(seg["start"], f["start"]), min(seg["end"], f["end"])
                        if b - a > 0.15 and str(seg.get("text") or "").strip():
                            captions += caption_chunks(seg["text"].strip(), at + a - f["start"], at + b - f["start"])
                at += f["end"] - f["start"]
            if not captions:
                track.note("Nothing was heard to write as subtitles.")
        elif subtitles:
            captions = read_srt(Path(str(subtitles)))
        track.at("Prepare the sound")
        status("Preparing the sound…")
        audio = np.zeros((int(total * RATE), 2), dtype="float32")
        at, heard_any = 0.0, False
        if not mute:
            for f in facts:
                if f["audio"]:
                    own = await asyncio.to_thread(read_pcm, f["path"], RATE, 2)
                    own = own[int(f["start"] * RATE): int(f["end"] * RATE)]
                    audio[int(at * RATE): int(at * RATE) + len(own)] += own[: len(audio) - int(at * RATE)]
                    heard_any = True
                at += f["end"] - f["start"]
        if sound:
            extra = await asyncio.to_thread(read_pcm, sound, RATE, 2)
            level = float(sound_volume) if sound_volume not in (None, "") else (0.2 if heard_any else 1.0)
            extra = extra[: len(audio)].copy()
            fade = min(len(extra), int(1.5 * RATE))
            if fade and len(extra) >= len(audio) - RATE:           # it runs to the end: let it fade
                extra[-fade:] *= np.linspace(1, 0, fade, dtype="float32")[:, None]
            audio[: len(extra)] += extra * level
            heard_any = True
        track.at("Encode")
        out_dir = Path(out_dir)
        stem = slug(name or (first["path"].stem + ("-joined" if len(facts) > 1 else "-edited")))[:40]
        dest = out_dir / f"{stem}-{time.strftime('%Y%m%d-%H%M%S')}.mp4"
        frames_total = int(total * fps)

        def frames():
            def chain():
                for f in facts:
                    yield from sample(f["path"], f["start"], f["end"], fps, size)
            return with_captions(chain(), captions, size, fps)
        status(f"Encoding {total:.0f} seconds of video…")
        await self._encode(dest, frames, fps, size, audio if heard_any else None, frames_total, status, job_id)
        track.at("Save")
        if captions:
            dest.with_suffix(".srt").write_text(srt(captions), encoding="utf-8")
        params = {"mode": "joined" if len(facts) > 1 else "edited", "seconds": round(total, 2), "width": size[0], "height": size[1],
                  "from": [str(f["path"]) for f in facts], "sound": "none" if not heard_any else "added" if mute and sound
                  else "own and added" if sound else "own", "captions": bool(captions),
                  "subtitles": str(dest.with_suffix(".srt")) if captions else None, "took": round(time.monotonic() - started, 1)}
        track.note(f"Saved {dest.name}: {total:.1f} seconds, after {params['took']:g} seconds of work.")
        return self._record(dest, name or first["path"].stem, "edited on this computer", params, chat_id, "local")

    # ------------------------------------------------------------------ 3. a clip made by a video model
    async def clip(self, prompt, *, out_dir, model=None, image=None, shape="landscape", seed=None, chat_id=None,
                   job_id=None, on_status=None):
        """A short clip from a description (and, online, optionally from a picture). `model` is a video model's id
        on this computer, "google", or None for the first that is ready (this computer first)."""
        status, track = self.app.jobs.reporter(on_status, job_id)
        prompt = (prompt or "").strip()
        if not prompt:
            raise VideoError("Describe the clip: what is seen, how it moves, the light, the style")
        local = [m for m in self.models() if m["ready"]]
        row = next((m for m in local if model in (m["id"], m["name"])), None) if model and model != "google" else None
        if model and model != "google" and not row:
            raise VideoError(f"There is no video model called {model!r} that is ready. Ready: "
                             + (", ".join(m["id"] for m in local) or "none on this computer")
                             + ("; online: google" if self.online()["ready"] else ""))
        if not model:
            row = local[0] if local else None
        if row is None and not self.online()["ready"]:
            raise VideoError("No video model is set up. Open Video → Models: download one for this computer, or switch on "
                             "Google's. A video from pictures, voice and music needs none of them (Video → Make).")
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        if row is not None:
            async with turn(self.app.images.lock, status, "Waiting for another picture or clip on this computer to finish…"):
                dest, params = await self._clip_local(row, prompt, out_dir, seed, status, track, job_id)
            name, source = row["name"], "local"
        else:
            dest, params = await self._clip_online(prompt, out_dir, image, shape, status, track, chat_id, job_id)
            name, source = f"Google · {self.online()['model']}", "online"
        params.update(mode="clip", took=round(time.monotonic() - started, 1), **(await asyncio.to_thread(info, dest)))
        track.note(f"Saved {dest.name}: {params['seconds']:g} seconds, after {params['took']:g} seconds of work.")
        return self._record(dest, prompt, name, params, chat_id, source)

    async def _clip_local(self, row, prompt, out_dir, seed, status, track, job_id):
        backend, exe = self.app.images.choose_engine()
        if not exe:
            raise VideoError("The image engine (it also runs video models) is not installed. Open Images → Engine.")
        s = row.get("settings") or {}
        steps, frames, fps = int(s.get("steps") or 20), int(s.get("frames") or 33), int(s.get("fps") or 16)
        width, height = int(s.get("width") or 832) // 16 * 16, int(s.get("height") or 480) // 16 * 16
        seed = int(seed) if seed not in (None, "", -1) else random.randint(1, 2**31 - 1)
        work = self.app.paths.data / "tmp" / new_id("clip_")
        work.mkdir(parents=True)
        raw = work / "clip.avi"
        cmd = [str(exe), "-M", "vid_gen"]
        for slot, path in (row.get("files") or {}).items():
            if slot in SLOT_FLAGS:
                cmd += [SLOT_FLAGS[slot], path]
        cmd += ["-p", prompt, "--cfg-scale", f"{float(s.get('cfg', 6.0)):g}", "--sampling-method", "euler",
                "--flow-shift", f"{float(s.get('flow_shift', 3.0)):g}", "-W", str(width), "-H", str(height),
                "--video-frames", str(frames), "--fps", str(fps), "--steps", str(steps), "-s", str(seed), "-o", str(raw)]
        if self.db.setting("image_compute", "auto") == "cpu" and backend not in ("cpu", "custom"):
            cmd += ["--backend", "cpu"]
        cmd += shlex.split(str(s.get("args") or ""), posix=os.name != "nt")
        minutes = float(self.db.setting("video_timeout_minutes", 240))
        track.stages(["Get ready", "Load the model", "Make the frames", "Save"])
        track.plan(clip={"prompt": prompt[:600], "model": row["name"], "where": "on this computer",
                         "engine": f"stable-diffusion.cpp ({backend})", "size": f"{width} × {height}", "frames": frames,
                         "frames_a_second": fps, "steps": steps, "seed": seed, "limit": f"{minutes:g} minutes",
                         "files": {slot: Path(p).name for slot, p in (row.get("files") or {}).items()}})
        track.at("Get ready")
        need = int(sum(Path(p).stat().st_size for p in (row.get("files") or {}).values()) * 1.1) + int(2 * 2**30)
        status("Making room in memory for the video model…")
        try:
            if not await self.app.images._free_memory(need):
                status(f"Memory is short for {row['name']} (it needs about {need / 2**30:.1f} GB): trying anyway.")
        except Exception:
            pass
        env = {**os.environ}
        env.pop("DVA_TOKEN", None)
        if os.name != "nt":
            env["LD_LIBRARY_PATH"] = f"{exe.parent}{os.pathsep}{env.get('LD_LIBRARY_PATH', '')}"
        track.at("Load the model")
        status(f"Loading {row['name']}…")
        track.note("Started: " + " ".join(Path(c).name if (i == 0 or Path(c).is_absolute()) else c for i, c in enumerate(cmd)))
        proc = await asyncio.create_subprocess_exec(*cmd, cwd=str(exe.parent), env=env, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.STDOUT, creationflags=NO_WINDOW)
        tail, drawing = [], [False]

        async def pump():
            buffer = b""
            while True:
                chunk = await proc.stdout.read(512)
                if not chunk:
                    break
                buffer += chunk
                *lines, buffer = re.split(rb"[\r\n]", buffer)
                for raw_line in lines:
                    line = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", raw_line.decode("utf-8", "replace")).strip()
                    if not line:
                        continue
                    tail.append(line)
                    del tail[:-60]
                    track.output(line)
                    m = re.search(r"(\d+)\s*/\s*(\d+)", line)
                    if m and int(m.group(2)) == steps and int(m.group(1)) <= steps:
                        if not drawing[0]:
                            drawing[0] = True
                            track.at("Make the frames")
                            status(f"{row['name']}: making {frames} frames, {steps} steps")
                        status.beat(f"{row['name']}: step {m.group(1)} of {steps}")
                        if job_id:
                            self.app.jobs.progress(job_id, f"{row['name']}: step {m.group(1)} of {steps}",
                                                   fraction=int(m.group(1)) / max(1, steps))
        try:
            await asyncio.wait_for(pump(), minutes * 60)
            code = await proc.wait()
            made = raw if raw.exists() else next(iter(sorted(work.glob("clip*.avi")) + sorted(work.glob("clip*.webm"))), None)
            if code != 0 or not made:
                self.app.log(f"video engine ({backend}) exit {code}: " + " | ".join(tail[-12:]))
                raise VideoError(self.app.images._explain(tail, code, row).replace("picture", "clip"))
            track.at("Save")
            status("Saving the clip…")
            facts = await asyncio.to_thread(info, made)
            dest = out_dir / f"{slug(prompt)[:32]}-{time.strftime('%Y%m%d-%H%M%S')}.mp4"
            size = (even(facts["width"]), even(facts["height"]))
            rate = max(8, round(facts["fps"] or fps))
            await self._encode(dest, lambda: sample(made, 0.0, facts["seconds"] or frames / fps, rate, size), rate, size, None,
                               int((facts["seconds"] or frames / fps) * rate), status, job_id)
        except asyncio.TimeoutError:
            proc.kill()
            raise VideoError(f"{row['name']} worked for longer than the limit of {minutes:g} minutes and was stopped. "
                             "Raise the limit in Video → Models, or make the video from pictures, voice and music instead.")
        except asyncio.CancelledError:
            if proc.returncode is None:
                proc.kill()
            raise
        finally:
            shutil.rmtree(work, ignore_errors=True)
        self.db.update("image_models", row["id"], {"last_used": now()})
        return dest, {"engine": backend, "steps": steps, "seed": seed, "frames": frames}

    async def _clip_online(self, prompt, out_dir, image, shape, status, track, chat_id, job_id):
        providers = self.app.providers
        online = self.online()
        if providers.over_budget():
            raise VideoError("This month's online budget is used up. Raise it in Models → Online, or make the video on "
                             "this computer.")
        conf = providers.conf("google")
        parts = [{"type": "text", "text": prompt}]
        body = {"model": online["model"], "input": prompt,
                "response_format": {"type": "video", "aspect_ratio": "9:16" if shape == "portrait" else "16:9", "resolution": "720p"}}
        if image:
            image = Path(str(image))
            if not image.is_file():
                raise VideoError(f"There is no picture at {image}")
            kind = "image/png" if image.suffix.lower() == ".png" else "image/webp" if image.suffix.lower() == ".webp" else "image/jpeg"
            body["input"] = [{"type": "image", "data": base64.b64encode(image.read_bytes()).decode(), "mime_type": kind}] + parts
            body["generation_config"] = {"video_config": {"task": "image_to_video"}}
        track.stages(["Ask the model", "Save"])
        track.plan(clip={"prompt": prompt[:600], "model": online["model"], "where": "online (Google)",
                         "from_picture": image.name if image else None, "shape": body["response_format"]["aspect_ratio"],
                         "sent": "the description" + (" and the picture" if image else ""),
                         "counted_as": f"${online['cost']:.2f} of the online budget (an amount you set, not Google's price)"})
        track.at("Ask the model")
        status(f"Asking Google ({online['model']}) for the clip: this takes a minute or more…")
        root = re.sub(r"/openai/?$", "", conf["base"].rstrip("/"))
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(900, connect=20)) as http:
                r = await http.post(f"{root}/interactions", json=body, headers={"x-goog-api-key": providers.key("google") or ""})
        except httpx.HTTPError as exc:
            raise VideoError(f"Google could not be reached ({type(exc).__name__})") from exc
        if r.status_code >= 400:
            try:
                err = r.json().get("error")
                message = err.get("message") if isinstance(err, dict) else str(err)
            except Exception:
                message = r.text[:300]
            raise VideoError(f"Google refused the request ({r.status_code}): {str(message)[:300]}")
        data, said = None, []
        for step in r.json().get("steps") or []:
            for item in step.get("content") or []:
                if item.get("type") == "video" and item.get("data"):
                    data = base64.b64decode(item["data"])
                elif item.get("type") == "text" and item.get("text"):
                    said.append(item["text"])
        if not data:
            raise VideoError("Google answered without a video (it may have declined the request)"
                             + (f": {' '.join(said)[:200]}" if said else "."))
        track.at("Save")
        dest = out_dir / f"{slug(prompt)[:32]}-{time.strftime('%Y%m%d-%H%M%S')}.mp4"
        dest.write_bytes(data)
        self.db.insert("usage", {"id": new_id("use_"), "created": now(), "month": month_key(),
                                 "model_id": cloud_id("google", online["model"]), "provider": "google", "chat_id": chat_id,
                                 "job_id": job_id, "purpose": "video", "input_tokens": 0, "output_tokens": 0,
                                 "cost": round(online["cost"], 5), "estimated": 1})
        return dest, {"cost": online["cost"], "from_picture": str(image) if image else None}

    # ------------------------------------------------------------------ requests from the Video window
    def start_job(self, kind, data):
        """Runs in the background as a task; the window follows it through event `video_job`."""
        titles = {"storyboard": "Write the scenes: ", "make": "Video: ", "edit": "Change a video: ", "clip": "Clip: "}
        what = str(data.get("title") or data.get("idea") or data.get("prompt") or Path(str(data.get("source") or (
            (data.get("clips") or [{}])[0].get("path") if isinstance((data.get("clips") or [{}])[0], dict) else (data.get("clips") or [""])[0])
            or "")).name or ", ".join(Path(str(s)).name for s in data.get("sources") or []) or "video")
        job = {"id": new_id("vid_"), "what": kind, "state": "running", "text": "Starting…", "result": None, "error": None,
               "title": what[:120], "started": now()}
        self.jobs[job["id"]] = job
        for old in list(self.jobs)[:-16]:
            if self.jobs[old]["state"] != "running":
                self.jobs.pop(old)
        publish = lambda: self.app.bus.publish("video_job", **job)        # noqa: E731

        def status(text):
            job["text"] = text
            publish()

        async def run(jid):
            job["job_id"] = jid
            out = self.folder()
            try:
                if kind == "storyboard":
                    job["result"] = await self.storyboard(idea=data.get("idea") or "", sources=data.get("sources") or [],
                                                          text=data.get("text") or "", minutes=data.get("minutes") or 1,
                                                          style=data.get("style") or "", model=data.get("model"),
                                                          job_id=jid, on_status=status)
                elif kind == "make":
                    job["result"] = await self.make(data.get("scenes") or [], out_dir=out, title=data.get("title") or "",
                                                    shape=data.get("shape") or "landscape", voice=data.get("voice") or None,
                                                    music=data.get("music") or None, captions=data.get("captions", True),
                                                    motion=data.get("motion", True), job_id=jid, on_status=status)
                elif kind == "edit":
                    job["result"] = await self.edit(data.get("source") or None, clips=data.get("clips") or None, out_dir=out,
                                                    start=data.get("start"), end=data.get("end"), mute=bool(data.get("mute")),
                                                    sound=data.get("sound") or None, sound_volume=data.get("sound_volume"),
                                                    subtitles=data.get("subtitles") or None, shape=data.get("shape") or None,
                                                    name=data.get("name") or None, job_id=jid, on_status=status)
                elif kind == "clip":
                    job["result"] = await self.clip(data.get("prompt"), out_dir=out, model=data.get("model") or None,
                                                    image=data.get("image") or None, shape=data.get("shape") or "landscape",
                                                    seed=data.get("seed"), job_id=jid, on_status=status)
                else:
                    raise VideoError(f"Unknown kind of video job: {kind}")
                job.update(state="done", text="Done")
            except asyncio.CancelledError:
                job.update(state="stopped", text="Stopped")
                raise
            except Exception as exc:
                job.update(state="failed", text="", error=str(exc)[:500])
                raise
            finally:
                self.tasks.pop(job["id"], None)
                publish()
            r = job["result"] or {}
            return {"media": [r]} if r.get("path") else {"text": json.dumps(r, ensure_ascii=False)[:1500]}
        row = self.app.jobs.create("video", titles.get(kind, "Video: ") + job["title"], lambda r: run(r["id"]), payload={"what": kind})
        job["job_id"] = row["id"]
        self.tasks[job["id"]] = row["id"]
        return job

    def stop_job(self, job_id):
        if job_id in self.tasks:
            self.app.jobs.cancel(self.tasks[job_id])


def read_srt(path):
    """The captions of a .srt file: [{"start", "end", "text"}]."""
    if not path.is_file():
        raise VideoError(f"There is no subtitle file at {path}")
    out = []
    for block in re.split(r"\n\s*\n", path.read_text(encoding="utf-8", errors="replace").replace("\r", "")):
        m = re.search(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)\s*\n(.+)", block, re.S)
        if m:
            g = [int(x) for x in m.groups()[:8]]
            out.append({"start": g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000, "end": g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000,
                        "text": " ".join(m.group(9).split())})
    return out
