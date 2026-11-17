"""Sound (2.12.0): speech from text, voices (also your own), transcription, music, and mixing them.

On this computer   Speech and transcription run in a separate process per request (dva/sound_worker.py, with the
                   sherpa-onnx library: ONNX models on the processor, no graphics card needed). The models are
                   downloaded in the app from the library's own releases: Kokoro (54 voices), Pocket TTS (speaks
                   with a voice taken from a short recording), Whisper (transcription, 99 languages).
                   The library's 2.x versions no longer contain the pronunciation component espeak-ng (it is
                   GPL-licensed), so with them local speech covers what Kokoro's own dictionaries cover: English
                   and Chinese. Models that need it (Piper voices, Kitten, Kokoro's other languages) are offered
                   only when a 1.x version of the library is installed (`espeak()`).
Online             OpenAI (speech, transcription) and Google (speech with one or two speakers, transcription,
                   music with Lyria), with the keys saved for those providers, counted against the monthly budget
                   with estimated prices. Anthropic's Claude neither speaks nor listens through its interface.
Voices             A voice is a speaker of an installed model, a provider's voice, or a voice made from a
                   recording. A recording of your own voice needs your confirmation; someone else's voice needs a
                   recording of that person agreeing. Agents can use voices; only the user makes them.
Scripts            Several lines with a voice each become one recording (a podcast, a dialogue, a voiceover with
                   chapters): lines of local voices are spoken by one process, lines of online voices by the
                   provider (Google speaks two of its voices in one request), and the pieces are joined here.
Records            Every recording is a file and a row in table `media` (kind audio), like pictures."""
import asyncio
import base64
import json
import os
import re
import shutil
import sys
import tarfile
import time
import wave
from importlib import metadata
from pathlib import Path

import httpx

from .db import new_id, now
from .download import download
from .images import fetch
from .providers import cloud_id, month_key

RELEASES = "https://github.com/k2-fsa/sherpa-onnx/releases/download"
AUDIO_EXT = (".wav", ".mp3", ".m4a", ".ogg", ".flac", ".opus", ".webm", ".aac")
KOKORO = ("af_alloy af_aoede af_bella af_heart af_jessica af_kore af_nicole af_nova af_river af_sarah af_sky am_adam "
          "am_echo am_eric am_fenrir am_liam am_michael am_onyx am_puck am_santa bf_alice bf_emma bf_isabella bf_lily "
          "bm_daniel bm_fable bm_george bm_lewis ef_dora em_alex ff_siwis hf_alpha hf_beta hm_omega hm_psi if_sara "
          "im_nicola jf_alpha jf_gongitsune jf_nezumi jf_tebukuro jm_kumo pf_dora pm_alex pm_santa zf_xiaobei zf_xiaoni "
          "zf_xiaoxiao zf_xiaoyi zm_yunjian zm_yunxi zm_yunxia zm_yunyang em_santa").split()
KOKORO_LANG = {"a": "English (US)", "b": "English (UK)", "e": "Spanish", "f": "French", "h": "Hindi", "i": "Italian",
               "j": "Japanese", "p": "Portuguese", "z": "Chinese"}
KITTEN = ["expr-voice-2-m", "expr-voice-2-f", "expr-voice-3-m", "expr-voice-3-f", "expr-voice-4-m", "expr-voice-4-f",
          "expr-voice-5-m", "expr-voice-5-f"]


def kokoro_voice(name, sid):
    return {"speaker": name, "sid": sid, "name": name[3:].capitalize(), "language": KOKORO_LANG[name[0]],
            "gender": "woman" if name[1] == "f" else "man", "needs_espeak": name[0] not in "abz"}


# Models for this computer. `mb` is the download; licences are what the publishers state.
CATALOG = [
    {"key": "kokoro-v1", "role": "tts", "family": "kokoro", "name": "Kokoro", "mb": 334, "recommended": True,
     "url": f"{RELEASES}/tts-models/kokoro-multi-lang-v1_0.tar.bz2", "licence": "Apache-2.0",
     "page": "https://huggingface.co/hexgrad/Kokoro-82M",
     "about": "Natural voices that run on a laptop's processor faster than they speak: 28 English voices (American "
              "and British, women and men), Chinese, and more languages with the wider pronunciation library.",
     "voices": [kokoro_voice(n, i) for i, n in enumerate(KOKORO)]},
    {"key": "pocket-tts", "role": "clone", "family": "pocket", "name": "Pocket TTS (a voice from a recording)", "mb": 94,
     "url": f"{RELEASES}/tts-models/sherpa-onnx-pocket-tts-int8-2026-01-26.tar.bz2", "licence": "CC-BY-4.0",
     "page": "https://github.com/kyutai-labs/pocket-tts",
     "about": "Speaks English with the voice of a short recording (10 to 30 seconds). This is what your own voice uses.",
     "voices": []},
    {"key": "kitten-nano", "role": "tts", "family": "kitten", "name": "Kitten (smallest)", "mb": 25, "needs_espeak": True,
     "url": f"{RELEASES}/tts-models/kitten-nano-en-v0_2-fp16.tar.bz2", "licence": "Apache-2.0",
     "page": "https://github.com/KittenML/KittenTTS", "about": "Eight English voices in 25 MB, for very small computers.",
     "voices": [{"speaker": n, "sid": i, "name": f"Kitten {n[11]} ({'woman' if n.endswith('f') else 'man'})",
                 "language": "English", "gender": "woman" if n.endswith("f") else "man"} for i, n in enumerate(KITTEN)]},
    *[{"key": f"piper-{code.lower().replace('_', '-')}", "role": "tts", "family": "vits", "name": f"Piper: {lang}",
       "mb": 64, "needs_espeak": True, "url": f"{RELEASES}/tts-models/vits-piper-{code}-{who}-medium.tar.bz2",
       "licence": "stated in the voice's MODEL_CARD file, in its folder", "page": "https://github.com/rhasspy/piper",
       "about": f"One {lang} voice.",
       "voices": [{"speaker": who, "sid": 0, "name": f"{who.capitalize()} ({lang})", "language": lang, "gender": ""}]}
      for code, who, lang in (("sw_CD", "lanfrica", "Swahili"), ("fr_FR", "siwis", "French"), ("en_GB", "alba", "English (UK)"))],
    *[{"key": f"whisper-{size}", "role": "stt", "family": "whisper", "name": f"Whisper {size}", "mb": mb,
       "recommended": size == "base", "url": f"{RELEASES}/asr-models/sherpa-onnx-whisper-{size}.tar.bz2",
       "licence": "MIT", "page": "https://github.com/openai/whisper", "about": about, "voices": []}
      for size, mb, about in (("tiny", 111, "Fastest; fine for clear speech and dictation."),
                              ("base", 198, "A good start: quick on a laptop, 99 languages."),
                              ("small", 610, "More accurate, slower."),
                              ("turbo", 538, "The most accurate here; needs patience without a graphics card."))],
]
VAD_URL = f"{RELEASES}/asr-models/silero_vad.onnx"
ONLINE = {
    "openai": {"name": "OpenAI",
               "tts": {"match": r"tts", "suggest": ["gpt-4o-mini-tts", "tts-1-hd", "tts-1"], "per_minute": 0.015},
               "stt": {"match": r"transcribe|whisper", "suggest": ["gpt-4o-transcribe", "gpt-4o-mini-transcribe", "whisper-1"],
                       "per_minute": 0.006},
               "voices": "alloy ash ballad coral echo fable nova onyx sage shimmer verse".split()},
    "google": {"name": "Google",
               "tts": {"match": r"tts", "suggest": ["gemini-3.8-flash-tts", "gemini-3.8-flash-lite-tts"], "per_minute": 0.015},
               "stt": {"match": r"flash(?!.*(tts|image|live))", "suggest": ["gemini-3.5-flash", "gemini-2.5-flash"],
                       "per_minute": 0.006},
               "music": {"match": r"lyria", "suggest": ["lyria-3.5", "lyria-3-clip-preview"], "per_item": 0.10},
               "voices": ("Zephyr Puck Charon Kore Fenrir Leda Orus Aoede Callirrhoe Autonoe Enceladus Iapetus Umbriel "
                          "Algieba Despina Erinome Algenib Rasalgethi Laomedeia Achernar Alnilam Schedar Gacrux "
                          "Pulcherrima Achird Zubenelgenubi Vindemiatrix Sadachbia Sadaltager Sulafat").split()},
}
ROLES = {"tts": "speech", "stt": "transcription", "music": "music", "clone": "a voice from a recording"}

SMALL = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
         "seventeen eighteen nineteen").split()
TENS = "twenty thirty forty fifty sixty seventy eighty ninety".split()


class SoundError(RuntimeError):
    pass


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")[:48] or "sound"


def number_words(n):
    """A whole number below a million million in English words."""
    if n < 20:
        return SMALL[n]
    if n < 100:
        return TENS[n // 10 - 2] + ("-" + SMALL[n % 10] if n % 10 else "")
    if n < 1000:
        return SMALL[n // 100] + " hundred" + (" and " + number_words(n % 100) if n % 100 else "")
    for size, word in ((10**9, "billion"), (10**6, "million"), (1000, "thousand")):
        if n >= size:
            rest = n % size
            return number_words(n // size) + " " + word + ((" " if rest >= 100 else " and ") + number_words(rest) if rest else "")
    return str(n)


def spell_english(text):
    """Numbers, money and signs as words. Speech models that pronounce from a dictionary skip what is not a word,
    so "45%" or "2026" would be silent."""
    def year(m):
        n = int(m.group(0))
        a, b = divmod(n, 100)
        if b == 0:
            return number_words(a) + " hundred"
        return number_words(a) + " " + ("oh " + SMALL[b] if b < 10 else number_words(b))

    def number(m):
        whole, frac = m.group(1).replace(",", ""), m.group(2)
        if len(whole) > 12 or (whole.startswith("0") and len(whole) > 1):      # phone numbers, codes
            return " ".join(SMALL[int(c)] for c in whole)
        words = number_words(int(whole))
        return words + (" point " + " ".join(SMALL[int(c)] for c in frac) if frac else "")
    money = {"$": "dollars", "€": "euros", "£": "pounds"}
    units = {"k": "thousand", "m": "million", "b": "billion"}
    text = re.sub(r"([$€£])\s?(\d[\d,]*(?:\.\d+)?)(?:\s?(thousand|million|billion|trillion|[kKmMbB]\b))?",
                  lambda m: " ".join(x for x in (m.group(2), units.get((m.group(3) or "").lower(), m.group(3) or ""),
                                                 money[m.group(1)]) if x), text)
    text = re.sub(r"(\d)\s?%", r"\1 percent", text).replace("&", " and ").replace("+", " plus ").replace("=", " equals ")
    text = re.sub(r"(?<![\d.,])(1[1-9]\d\d|20\d\d)(?!\d|,\d|\.\d)", year, text)
    text = re.sub(r"(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?", number, text)
    return re.sub(r"\s{2,}", " ", text)


def read_pcm(path, rate=44100, channels=2):
    """An audio (or video) file as float32 samples of shape (n, channels)."""
    import av
    import numpy as np
    chunks = []
    with av.open(str(path)) as box:
        stream = next((s for s in box.streams if s.type == "audio"), None)
        if stream is None:
            raise SoundError(f"{Path(path).name} has no sound in it")
        resampler = av.AudioResampler(format="flt", layout="stereo" if channels == 2 else "mono", rate=rate)
        for frame in list(box.decode(stream)) + [None]:
            for out in resampler.resample(frame):
                chunks.append(out.to_ndarray().reshape(-1, channels))
    return np.concatenate(chunks).astype("float32") if chunks else np.zeros((0, channels), dtype="float32")


def write_audio(path, samples, rate):
    """Write float32 samples (n, channels) by the file's ending: .wav as it is, .mp3 and .m4a encoded. Returns the
    file written: a .wav beside it when this computer's media library cannot encode the format."""
    import av
    import numpy as np
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    samples = np.clip(samples, -1.0, 1.0)
    channels = samples.shape[1]
    pcm = (samples * 32767).astype("<i2")
    if path.suffix.lower() == ".wav":
        with wave.open(str(path), "wb") as f:
            f.setnchannels(channels)
            f.setsampwidth(2)
            f.setframerate(rate)
            f.writeframes(pcm.tobytes())
        return path
    codec = {".mp3": "mp3", ".m4a": "aac"}.get(path.suffix.lower())
    if not codec:
        raise SoundError(f"Sound can be saved as .wav, .mp3 or .m4a, not {path.suffix}")
    layout = "stereo" if channels == 2 else "mono"
    try:
        av.Codec(codec, "w")
    except Exception:
        return write_audio(path.with_suffix(".wav"), samples, rate)
    with av.open(str(path), "w") as box:
        stream = box.add_stream(codec, rate=rate, layout=layout)
        stream.bit_rate = 128000 if channels == 2 else 96000
        fifo = av.AudioFifo()
        frame = av.AudioFrame.from_ndarray(np.ascontiguousarray(pcm.reshape(1, -1)), format="s16", layout=layout)
        frame.sample_rate = rate
        for part in av.AudioResampler(format=stream.codec_context.format.name, layout=layout, rate=rate).resample(frame):
            fifo.write(part)
        size = stream.codec_context.frame_size or 1024
        while fifo.samples:
            part = fifo.read(min(size, fifo.samples))
            part.sample_rate = rate
            for packet in stream.encode(part):
                box.mux(packet)
        for packet in stream.encode(None):
            box.mux(packet)
    return path


def seconds_of(path):
    try:
        import av
        with av.open(str(path)) as box:
            return round(float(box.duration or 0) / 1_000_000, 2)
    except Exception:
        return 0.0


def srt(segments):
    def stamp(t):
        ms = int(round(t * 1000))
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"
    return "\n".join(f"{i}\n{stamp(s['start'])} --> {stamp(s['end'])}\n{s['text']}\n" for i, s in enumerate(segments, 1))


class Sound:
    def __init__(self, app):
        self.app, self.db = app, app.db
        self.lock = asyncio.Lock()            # one local job at a time
        self.downloads, self.jobs, self.tasks = {}, {}, {}

    # ------------------------------------------------------------------ what this computer can do
    @staticmethod
    def library():
        if os.environ.get("DVA_SOUND_LIBRARY"):          # tests name the version they stand in for
            return os.environ["DVA_SOUND_LIBRARY"]
        try:
            return metadata.version("sherpa-onnx")
        except metadata.PackageNotFoundError:
            return None

    def espeak(self):
        """True when the installed speech library still pronounces through espeak-ng (its 1.x versions)."""
        return (self.library() or "").startswith("1.")

    def hidden_tools(self):
        """Sound tools that have no model to work with right now (they are not offered to an agent)."""
        from .tools.sound import NEEDS
        can = self.can()
        return {name for name, role in NEEDS.items() if not can[role]}

    def folder(self):
        return self.app.paths.models_dir({"models_dir": self.db.setting("models_dir")}) / "sound"

    def usable(self, entry):
        return not entry.get("needs_espeak") or self.espeak()

    # ------------------------------------------------------------------ models
    def models(self):
        rows = self.db.all("SELECT * FROM sound_models ORDER BY added")
        for r in rows:
            if r["kind"] == "local":
                entry = next((c for c in CATALOG if c["key"] == r["id"]), {})
                r["ready"] = bool(self.library()) and Path(r["path"]).is_dir() and self.usable(entry)
                r["why"] = "" if r["ready"] else ("the speech library is not installed" if not self.library() else
                                                  "its files are missing" if not Path(r["path"]).is_dir() else
                                                  "it needs the wider pronunciation library")
            else:
                r["ready"] = self.app.providers.ready(r["provider"])
                r["why"] = "" if r["ready"] else "its provider has no key"
            r["default"] = self.db.setting(f"sound_{r['role']}", "") == r["id"]
        return rows

    def get(self, model_id):
        return next((m for m in self.models() if m["id"] == model_id), None)

    def default(self, role):
        """The model used for a role when none is named: the chosen one, else a ready local one, else online."""
        rows = [m for m in self.models() if m["role"] == role and m["ready"]]
        return next((m for m in rows if m["default"]), None) or next((m for m in rows if m["kind"] == "local"), None) \
            or (rows[0] if rows else None)

    def can(self):
        return {role: self.default(role) is not None for role in ROLES}

    def available(self):
        can = self.can()
        return can["tts"] or can["stt"] or can["music"]

    def _insert(self, row):
        if self.db.get("sound_models", row["id"]):
            self.db.delete("sound_models", row["id"])
        self.db.insert("sound_models", {"provider": "", "remote_id": "", "path": "", "family": "", "size": 0,
                                        "licence": "", "settings": {}, **row, "added": now()})
        self.app.bus.publish("sound_changed")
        return self.get(row["id"])

    def add_online(self, provider, role, remote_id):
        spec = (ONLINE.get(provider) or {}).get(role)
        if not spec:
            what = ROLES.get(role, role)
            raise ValueError("Claude does not speak, listen or make music through its interface." if provider == "anthropic"
                             else f"{(ONLINE.get(provider) or {}).get('name', provider)} is not set up here for {what}.")
        remote_id = str(remote_id or "").strip().removeprefix("models/")
        if not remote_id:
            raise ValueError("Choose the provider's model")
        settings = {k: spec[k] for k in ("per_minute", "per_item") if k in spec}
        return self._insert({"id": slug(f"{provider}-{remote_id}"), "kind": "online", "role": role, "provider": provider,
                             "remote_id": remote_id, "name": f"{ONLINE[provider]['name']} · {remote_id}", "settings": settings})

    def add_folder(self, key, path):
        """A model of the catalogue whose unpacked folder is already on this computer (used where it is)."""
        entry = next((c for c in CATALOG if c["key"] == key), None)
        if not entry:
            raise ValueError("Choose which model the folder holds")
        folder = Path(str(path or ""))
        if not folder.is_dir() or not any(folder.glob("*.onnx")):
            raise ValueError(f"{folder} is not a folder with a model's .onnx files in it")
        size = sum(p.stat().st_size for p in folder.rglob("*") if p.is_file())
        return self._insert({"id": entry["key"], "kind": "local", "role": entry["role"], "family": entry["family"],
                             "name": entry["name"], "path": str(folder), "size": size, "licence": entry["licence"]})

    def update(self, model_id, data):
        row = self.db.get("sound_models", model_id)
        if not row:
            raise KeyError(model_id)
        if data.get("default"):
            self.db.set_setting(f"sound_{row['role']}", model_id)
        if isinstance(data.get("settings"), dict):
            self.db.update("sound_models", model_id, {"settings": {**(row.get("settings") or {}), **data["settings"]}})
        self.app.bus.publish("sound_changed")
        return self.get(model_id)

    def remove(self, model_id):
        row = self.db.get("sound_models", model_id)
        if not row:
            raise KeyError(model_id)
        if row["kind"] == "local" and row.get("path") and self.folder() in Path(row["path"]).parents:
            shutil.rmtree(row["path"], ignore_errors=True)          # only what KimiLM downloaded itself
        self.db.delete("sound_models", model_id)
        self.app.bus.publish("sound_changed")

    def catalog(self):
        have = {m["id"] for m in self.models() if m["kind"] == "local"}
        return [{k: c[k] for k in ("key", "role", "name", "mb", "licence", "page", "about")}
                | {"installed": c["key"] in have, "usable": self.usable(c), "recommended": bool(c.get("recommended")),
                   "voices": len(c["voices"]), "download": self.downloads.get(c["key"])} for c in CATALOG]

    def start_download(self, key):
        entry = next((c for c in CATALOG if c["key"] == key), None)
        if not entry:
            raise KeyError(key)
        if not self.usable(entry):
            raise ValueError(f"{entry['name']} needs the wider pronunciation library (see the note in Sound → Models)")
        state = self.downloads.get(key)
        if state and state["state"] in ("starting", "downloading", "unpacking"):
            return state
        state = self.downloads[key] = {"key": key, "name": entry["name"], "state": "starting", "done": 0,
                                       "total": entry["mb"] * 2**20, "error": None, "cancel": False}
        self.app.spawn(self._download(entry, state))
        return state

    def cancel_download(self, key):
        if key in self.downloads:
            self.downloads[key]["cancel"] = True

    async def _download(self, entry, state):
        publish = lambda: self.app.bus.publish("sound_download", **{k: v for k, v in state.items() if k != "cancel"})  # noqa: E731
        folder = self.folder()
        archive = folder / Path(entry["url"]).name
        try:
            def progress(done, total, rate):
                state.update(state="downloading", done=done, total=total or state["total"])
                publish()
            await fetch(entry["url"], archive, state=state, progress=progress)
            state.update(state="unpacking")
            publish()
            target = folder / entry["key"]
            await asyncio.to_thread(self._unpack, archive, target)
            archive.unlink(missing_ok=True)
            if entry["role"] == "stt" and not (folder / "silero_vad.onnx").exists():
                await download(VAD_URL, folder / "silero_vad.onnx")      # finds where speech is in a recording
            size = sum(p.stat().st_size for p in target.rglob("*") if p.is_file())
            self._insert({"id": entry["key"], "kind": "local", "role": entry["role"], "family": entry["family"],
                          "name": entry["name"], "path": str(target), "size": size, "licence": entry["licence"]})
            state.update(state="done", done=state["total"])
        except Exception as exc:
            state.update(state="cancelled" if state["cancel"] else "failed", error=str(exc)[:300])
            self.app.log(f"sound model {entry['key']}: {type(exc).__name__}: {exc}")
        publish()
        self.app.bus.publish("sound_changed")

    @staticmethod
    def _unpack(archive, target):
        """Unpack a model archive (one folder inside) so that the model's files are directly in `target`."""
        tmp = target.with_name(target.name + ".tmp")
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        with tarfile.open(archive, "r:*") as tar:
            tar.extractall(tmp, filter="data")
        inner = [p for p in tmp.iterdir()]
        source = inner[0] if len(inner) == 1 and inner[0].is_dir() else tmp
        shutil.rmtree(target, ignore_errors=True)
        shutil.move(str(source), str(target))
        shutil.rmtree(tmp, ignore_errors=True)

    async def online_candidates(self, provider, role):
        spec = (ONLINE.get(provider) or {}).get(role)
        if not spec:
            raise ValueError("Claude does not speak, listen or make music through its interface." if provider == "anthropic"
                             else f"{provider} is not set up here for {ROLES.get(role, role)}.")
        try:
            conf = self.app.providers.conf(provider)
            async with httpx.AsyncClient(timeout=30) as http:
                r = await http.get(conf["base"].rstrip("/") + "/models", headers=self.app.providers.headers(provider))
            r.raise_for_status()
            data = r.json()
            ids = [str(m.get("id") or m.get("name") or "").removeprefix("models/") for m in data.get("data") or data.get("models") or []]
            found = sorted({i for i in ids if re.search(spec["match"], i, re.I)}, reverse=True)
            if found:
                return {"models": found, "from": "provider"}
        except Exception as exc:
            self.app.log(f"sound models of {provider}: {type(exc).__name__}: {exc}")
        return {"models": spec["suggest"], "from": "suggestions"}

    # ------------------------------------------------------------------ voices
    def voices(self):
        """Every voice that can speak now: speakers of installed models, providers' voices, voices from recordings."""
        out = []
        models = [m for m in self.models() if m["role"] in ("tts", "clone")]
        for m in models:
            if m["role"] != "tts":
                continue
            if m["kind"] == "local":
                entry = next((c for c in CATALOG if c["key"] == m["id"]), {"voices": []})
                for v in entry["voices"]:
                    if v.get("needs_espeak") and not self.espeak():
                        continue
                    out.append({"id": f"{m['id']}:{v['speaker']}", "name": v["name"], "kind": "local", "model": m["id"],
                                "model_name": m["name"], "language": v["language"], "gender": v["gender"], "ready": m["ready"]})
            else:
                for name in ONLINE[m["provider"]]["voices"]:
                    out.append({"id": f"{m['id']}:{name}", "name": name.capitalize(), "kind": "online", "model": m["id"],
                                "model_name": m["name"], "language": "many", "gender": "", "ready": m["ready"]})
        clone = self.default("clone")
        for v in self.db.all("SELECT * FROM voices ORDER BY created"):
            out.append({"id": f"voice:{v['id']}", "name": v["name"], "kind": "recording", "model": (clone or {}).get("id"),
                        "model_name": (clone or {}).get("name") or "needs Pocket TTS", "language": "English", "gender": "",
                        "ready": bool(clone) and Path(v["ref_audio"]).exists(), "whose": (v.get("consent") or {}).get("whose")})
        default = self.db.setting("sound_voice", "")
        for v in out:
            v["default"] = v["id"] == default
        return out

    def voice(self, wanted=None):
        """A voice by id or by name (as an agent would say it); without one, the default voice."""
        voices = [v for v in self.voices() if v["ready"]]
        if not voices:
            raise SoundError("No voice is set up yet. Open Sound → Models and download Kokoro for this computer, or "
                             "switch on an online voice (OpenAI or Google).")
        if wanted:
            w = str(wanted).strip().lower()
            hit = next((v for v in voices if v["id"].lower() == w), None) or \
                next((v for v in voices if w in (v["name"].lower(), v["id"].split(":", 1)[1].lower())), None)
            if not hit:
                names = ", ".join(v["name"] for v in voices[:40])
                raise SoundError(f"There is no voice called {wanted!r}. Voices: {names}")
            return hit
        return next((v for v in voices if v["default"]), None) or next(
            (v for v in voices if v["id"].endswith(":af_heart")), None) or voices[0]

    async def create_voice(self, name, audio, transcript="", consent=None):
        """A voice from a recording. Your own voice needs your confirmation; another person's voice needs a
        recording in which that person agrees."""
        consent = dict(consent or {})
        name = " ".join(str(name or "").split())[:60]
        if not name:
            raise ValueError("Give the voice a name")
        if consent.get("whose") == "mine":
            if not consent.get("confirmed"):
                raise ValueError("Confirm that the recording is your own voice")
        elif consent.get("whose") == "other":
            if not str(consent.get("person") or "").strip():
                raise ValueError("Say whose voice it is")
            if not consent.get("consent_audio") or not Path(consent["consent_audio"]).is_file():
                raise ValueError("Another person's voice needs a recording in which they agree to its use: add it")
        else:
            raise ValueError("Say whether this is your own voice or another person's (with their recorded consent)")
        if not audio or not Path(audio).is_file():
            raise ValueError("The recording was not found")
        samples = await asyncio.to_thread(read_pcm, audio, 24000, 1)
        if len(samples) < 24000 * 5:
            raise ValueError("The recording is shorter than 5 seconds: 10 to 30 seconds of clear speech work best")
        vid = new_id("vc_")
        folder = self.app.paths.data / "voices"
        ref = folder / f"{vid}.wav"
        await asyncio.to_thread(write_audio, ref, samples[: 24000 * 30], 24000)
        kept = {"whose": consent["whose"], "at": now(), "person": str(consent.get("person") or "")[:80],
                "statement": "I confirm this is my own voice." if consent["whose"] == "mine" else
                             "The person agreed in a recording that is kept with this voice."}
        if consent["whose"] == "other":
            proof = folder / f"{vid}-consent{Path(consent['consent_audio']).suffix.lower() or '.wav'}"
            shutil.copyfile(consent["consent_audio"], proof)
            kept["consent_audio"] = str(proof)
        self.db.insert("voices", {"id": vid, "name": name, "ref_audio": str(ref), "ref_text": str(transcript or "")[:1000],
                                  "consent": kept, "created": now()})
        self.app.bus.publish("sound_changed")
        return next(v for v in self.voices() if v["id"] == f"voice:{vid}")

    def delete_voice(self, voice_id):
        vid = voice_id.removeprefix("voice:")
        row = self.db.get("voices", vid)
        if not row:
            raise KeyError(voice_id)
        for path in (row["ref_audio"], (row.get("consent") or {}).get("consent_audio")):
            if path:
                Path(path).unlink(missing_ok=True)
        self.db.delete("voices", vid)
        self.app.bus.publish("sound_changed")

    # ------------------------------------------------------------------ the worker process
    async def _worker(self, job, status):
        """Run one job in its own process and return its last message."""
        folder = self.app.paths.data / "tmp"
        folder.mkdir(exist_ok=True)
        spec = folder / f"{new_id('snd_')}.json"
        spec.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
        stand_in = os.environ.get("DVA_SOUND_WORKER")
        cmd = [sys.executable, stand_in, str(spec)] if stand_in else [sys.executable, "-m", "dva.sound_worker", str(spec)]
        env = {**os.environ, "PYTHONPATH": os.pathsep.join(
            x for x in (str(Path(__file__).resolve().parents[1]), os.environ.get("PYTHONPATH", "")) if x)}
        env.pop("DVA_TOKEN", None)
        proc = await asyncio.create_subprocess_exec(*cmd, env=env, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.PIPE,
                                                    creationflags=0x08000000 if os.name == "nt" else 0)
        last, errors = {}, []

        async def notes():
            async for raw in proc.stderr:
                errors.append(raw.decode("utf-8", "replace").strip())
                del errors[:-30]
        reader = asyncio.ensure_future(notes())
        try:
            async for raw in proc.stdout:
                try:
                    last = json.loads(raw)
                except ValueError:
                    continue
                if last.get("text") and "done" not in last:
                    status(last["text"])
            await proc.wait()
            await reader
        except asyncio.CancelledError:
            proc.kill()
            raise
        finally:
            spec.unlink(missing_ok=True)
        if last.get("error") or not last.get("done"):
            self.app.log("sound worker: " + (last.get("error") or f"exit {proc.returncode}") + " | " + " | ".join(errors[-6:]))
            raise SoundError(self._explain(last.get("error") or "", errors, proc.returncode))
        return last

    @staticmethod
    def _explain(error, errors, code):
        text = (error + " " + " ".join(errors)).lower()
        if "no module named 'sherpa_onnx'" in text or "no module named sherpa_onnx" in text:
            return ("The speech library is not installed. In the engine folder run: "
                    ".venv\\Scripts\\python.exe -m pip install -r requirements.txt")
        if "memoryerror" in text or "bad_alloc" in text or "failed to allocate" in text:
            return "This computer ran out of memory for the sound model. Close other apps or use a smaller model."
        if "to token ids" in text or "gave no sound" in text:
            return ("The voice could not pronounce this text. Voices on this computer read English (and Chinese); "
                    "for other languages use an online voice.")
        return (error.split(": ", 1)[-1] if error else f"The sound process stopped (code {code}).")[:400]

    # ------------------------------------------------------------------ speaking
    def _record(self, path, prompt, model_name, params, chat_id, source):
        row = {"id": new_id("med_"), "kind": "audio", "path": str(path), "chat_id": chat_id, "prompt": prompt[:4000],
               "model": model_name, "model_name": model_name, "source": source, "params": params, "created": now()}
        self.db.insert("media", row)
        self.app.bus.publish("sound_changed")
        return {**row, "name": Path(path).name}

    def _usage(self, row, cost, chat_id, job_id, purpose):
        self.db.insert("usage", {"id": new_id("use_"), "created": now(), "month": month_key(),
                                 "model_id": cloud_id(row["provider"], row["remote_id"]), "provider": row["provider"],
                                 "chat_id": chat_id, "job_id": job_id, "purpose": purpose, "input_tokens": 0,
                                 "output_tokens": 0, "cost": round(cost, 5), "estimated": 1})

    def _online_ok(self, row):
        if self.app.providers.over_budget():
            raise SoundError("This month's online budget is used up. Use a model on this computer, or raise the "
                             "budget in Models → Online.")
        return self.app.providers.conf(row["provider"])

    @staticmethod
    def _answer(response, row):
        if response.status_code >= 400:
            try:
                err = response.json().get("error")
                message = err.get("message") if isinstance(err, dict) else str(err)
            except Exception:
                message = response.text[:300]
            raise SoundError(f"{row['name']} refused the request ({response.status_code}): {str(message)[:300]}")
        return response

    @staticmethod
    def _interaction_audio(data):
        """The audio (and any text) of a Google interactions answer."""
        audio, text = None, []
        for step in data.get("steps") or []:
            if step.get("type") != "model_output":
                continue
            for part in step.get("content") or []:
                if part.get("type") == "audio" and part.get("data"):
                    audio = base64.b64decode(part["data"])
                elif part.get("type") == "text" and part.get("text"):
                    text.append(part["text"])
        return audio, "\n".join(text)

    async def _say_online(self, row, lines, dest):
        """Lines of one online model as one file. Google speaks two of its voices in a single request."""
        conf = self._online_ok(row)
        async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=20)) as http:
            if row["provider"] == "openai":
                body = {"model": row["remote_id"], "input": lines[0]["text"], "voice": lines[0]["speaker_id"].lower(),
                        "response_format": "wav"}
                if lines[0].get("style"):
                    body["instructions"] = lines[0]["style"]
                r = self._answer(await http.post(conf["base"].rstrip("/") + "/audio/speech", json=body,
                                                 headers=self.app.providers.headers("openai")), row)
                dest.write_bytes(r.content)
            else:
                root = re.sub(r"/openai/?$", "", conf["base"].rstrip("/"))
                content = [{"type": "text", "text": ln["text"], "annotations": [
                    {"type": "speech_metadata", **({"speaker": ln["speaker"]} if len(lines) > 1 else {}),
                     **({"style": ln["style"]} if ln.get("style") else {})}]} for ln in lines]
                if len(lines) > 1:
                    pairs = {ln["speaker"]: ln["speaker_id"] for ln in lines}
                    speech = {"mode": "conversational", "speakers": [{"speaker": s, "voice": v} for s, v in pairs.items()]}
                else:
                    speech = [{"voice": lines[0]["speaker_id"]}]
                body = {"model": row["remote_id"], "input": [{"type": "user_input", "content": content}],
                        "response_format": {"type": "audio"}, "generation_config": {"speech_config": speech}}
                r = self._answer(await http.post(f"{root}/interactions", json=body,
                                                 headers={"x-goog-api-key": self.app.providers.key("google") or ""}), row)
                audio, _ = self._interaction_audio(r.json())
                if not audio:
                    raise SoundError(f"{row['name']} answered without sound (it may have declined the text).")
                dest.write_bytes(audio)
        return dest

    def parse_script(self, text, default_voice=None):
        """Lines "Name: what they say" as a script; text without such lines is one speaker's text."""
        lines, current = [], None
        for raw in str(text or "").splitlines():
            line = raw.strip()
            m = re.match(r"^\**([A-Za-z][\w .'-]{0,30}?)\**\s*:\s+(.*)$", line)
            if m and not re.match(r"^(https?|note|warning|e\.g)$", m.group(1), re.I):
                current = {"speaker": m.group(1).strip(), "text": m.group(2).strip()}
                lines.append(current)
            elif line and current:
                current["text"] += " " + line
            elif line:
                current = {"speaker": "", "text": line}
                lines.append(current)
        speakers = {ln["speaker"] for ln in lines}
        if len(speakers - {""}) < 2 and not all(ln["speaker"] for ln in lines):
            return [{"speaker": "", "text": " ".join(str(text).split()), "voice": default_voice}]
        return lines

    async def speak(self, lines, *, out_dir, voice=None, voices=None, speed=1.0, fmt="mp3", name=None, chat_id=None,
                    job_id=None, on_status=None):
        """Say `lines` ([{"text", "speaker", "voice", "style"}] or one text) and save one recording.
        `voices` maps speaker names to voices; a line's own "voice" wins; `voice` is used for the rest."""
        status = on_status or (lambda text: None)
        if isinstance(lines, str):
            lines = self.parse_script(lines)
        lines = [dict(ln) for ln in lines if str(ln.get("text") or "").strip()]
        if not lines:
            raise SoundError("There is nothing to say")
        chosen, cast = {}, {}
        ready = [v for v in self.voices() if v["ready"]]
        for ln in lines:
            who = ln.get("speaker") or ""
            want = ln.get("voice") or (voices or {}).get(who) or (voice if not who or len({x.get("speaker") for x in lines}) == 1 else None)
            if want:
                v = self.voice(want)
            elif who in chosen:
                v = chosen[who]
            else:                       # a speaker without a voice: the default first, then one not used yet
                first = self.voice(voice)
                heard = {c["gender"] for c in chosen.values()}       # the next speaker should sound different
                others = [x for x in ready if x["model"] == first["model"] and x["id"] != first["id"]
                          and x["language"] == first["language"]]
                pool = [first] + sorted(others, key=lambda x: (x["gender"] in heard, x["name"] not in ("Michael", "George", "Emma", "Bella")))
                v = next((x for x in pool if x["id"] not in {c["id"] for c in chosen.values()}), first)
            chosen.setdefault(who, v)
            cast[who or "Voice"] = v["name"]
            ln["_voice"] = v
        models = {m["id"]: m for m in self.models()}
        # consecutive lines of the same kind are made together
        runs = []
        for ln in lines:
            v = ln["_voice"]
            m = models[v["model"]]
            key = ("local",) if m["kind"] == "local" else ("online", m["id"])
            duo = m["kind"] == "online" and m["provider"] == "google"
            if runs and runs[-1]["key"] == key and (m["kind"] == "local" or (
                    duo and len({x["_voice"]["id"] for x in runs[-1]["lines"]} | {v["id"]}) <= 2
                    and all(x.get("speaker") for x in runs[-1]["lines"] + [ln]))):
                runs[-1]["lines"].append(ln)
            else:
                runs.append({"key": key, "model": m, "lines": [ln]})
        tmp = self.app.paths.data / "tmp" / new_id("say_")
        tmp.mkdir(parents=True)
        started, files, marks, cost = time.monotonic(), [], [], 0.0
        try:
            for i, run in enumerate(runs):
                part = tmp / f"{i:03d}.wav"
                if run["key"][0] == "local":
                    job = {"kind": "speak", "out": str(part), "models": {}, "segments": []}
                    for ln in run["lines"]:
                        v = ln["_voice"]
                        m = models[v["model"]]
                        job["models"][m["id"]] = {"family": m["family"], "dir": m["path"]}
                        seg = {"model": m["id"], "text": spell_english(ln["text"]) if v["language"].startswith("English") else ln["text"],
                               "speaker": ln.get("speaker") or "", "speed": float(ln.get("speed") or speed or 1.0)}
                        if v["kind"] == "recording":
                            row = self.db.get("voices", v["id"].removeprefix("voice:"))
                            seg.update(ref_audio=row["ref_audio"], ref_text=row.get("ref_text") or "")
                        else:
                            entry = next(c for c in CATALOG if c["key"] == m["id"])
                            seg["sid"] = next(x["sid"] for x in entry["voices"] if x["speaker"] == v["id"].split(":", 1)[1])
                        job["segments"].append(seg)
                    async with self.lock:
                        done = await self._worker(job, status)
                    marks.append(done.get("segments") or [])
                else:
                    m = run["model"]
                    status(f"Asking {m['name']}…")
                    for ln in run["lines"]:
                        ln["speaker_id"] = ln["_voice"]["id"].split(":", 1)[1]
                    await self._say_online(m, run["lines"], part)
                    length = seconds_of(part)
                    price = float((m.get("settings") or {}).get("per_minute") or 0.015) * length / 60
                    cost += price
                    self._usage(m, price, chat_id, job_id, "speech")
                    marks.append([{"start": 0.0, "end": length, "text": " ".join(x["text"] for x in run["lines"]),
                                   "speaker": run["lines"][0].get("speaker") or ""}])
                files.append(part)
            status("Putting the recording together…")
            out_dir = Path(out_dir)
            stem = slug(name or lines[0]["text"])[:40] + "-" + time.strftime("%Y%m%d-%H%M%S")
            dest = out_dir / f"{stem}.{fmt if fmt in ('wav', 'mp3', 'm4a') else 'mp3'}"
            dest, seconds, segments = await asyncio.to_thread(self._join, files, marks, dest)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        params = {"mode": "script" if len({ln.get("speaker") for ln in lines}) > 1 else "speech", "seconds": seconds,
                  "voices": cast, "segments": segments, "took": round(time.monotonic() - started, 1)}
        if cost:
            params["cost"] = round(cost, 4)
        names = ", ".join(dict.fromkeys(ln["_voice"]["name"] for ln in lines))
        source = "online" if all(r["key"][0] == "online" for r in runs) else "local" if all(r["key"][0] == "local" for r in runs) else "mixed"
        return self._record(dest, " ".join(ln["text"] for ln in lines), names, params, chat_id, source)

    @staticmethod
    def _join(files, marks, dest, pause=0.4):
        """Join the parts (with a short pause between them) and return the length and where each line starts."""
        import numpy as np
        rate, pieces, segments, at = 24000, [], [], 0.0
        for path, part_marks in zip(files, marks):
            samples = read_pcm(path, rate, 1)
            segments += [{**m, "start": round(m["start"] + at, 2), "end": round(m["end"] + at, 2)} for m in part_marks]
            pieces += [samples, np.zeros((int(rate * pause), 1), dtype="float32")]
            at += len(samples) / rate + pause
        out = np.concatenate(pieces[:-1])
        return write_audio(dest, out, rate), round(len(out) / rate, 2), segments

    # ------------------------------------------------------------------ listening
    async def transcribe(self, audio, *, model=None, language="", out_dir=None, chat_id=None, job_id=None, on_status=None):
        """The words of a recording (or of a video's sound), with times. Returns {"text", "segments", "seconds"…}."""
        status = on_status or (lambda text: None)
        audio = Path(audio)
        if not audio.is_file():
            raise SoundError(f"There is no file at {audio}")
        row = self.get(model) if model else self.default("stt")
        if not row or row["role"] != "stt" or not row["ready"]:
            raise SoundError("No transcription model is set up. Open Sound → Models and download Whisper for this "
                             "computer, or switch on an online one.")
        started = time.monotonic()
        if row["kind"] == "local":
            vad = self.folder() / "silero_vad.onnx"
            if not vad.exists():
                status("Fetching the small model that finds speech…")
                await download(VAD_URL, vad)
            job = {"kind": "transcribe", "audio": str(audio), "vad": str(vad),
                   "model": {"family": row["family"], "dir": row["path"], "language": language or ""}}
            async with self.lock:
                done = await self._worker(job, status)
            result = {"text": done["text"], "segments": done["segments"], "seconds": done["seconds"]}
        else:
            conf = self._online_ok(row)
            status(f"Sending the recording to {row['name']}…")
            small = self.app.paths.data / "tmp" / f"{new_id('up_')}.mp3"       # small enough to send
            small = await asyncio.to_thread(lambda: write_audio(small, read_pcm(audio, 16000, 1), 16000))
            length, kind = seconds_of(small), "audio/mpeg" if small.suffix == ".mp3" else "audio/wav"
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=20)) as http:
                    if row["provider"] == "openai":
                        data = {"model": row["remote_id"], "response_format": "json"}
                        if language:
                            data["language"] = language
                        r = self._answer(await http.post(conf["base"].rstrip("/") + "/audio/transcriptions", data=data,
                                                         files={"file": (small.name, small.read_bytes(), kind)},
                                                         headers=self.app.providers.headers("openai")), row)
                        text = (r.json().get("text") or "").strip()
                    else:
                        root = re.sub(r"/openai/?$", "", conf["base"].rstrip("/"))
                        ask = "Transcribe this recording word for word. Give only the transcript" + (
                            f" (the language is {language})." if language else ".")
                        body = {"contents": [{"role": "user", "parts": [
                            {"text": ask}, {"inlineData": {"mimeType": kind, "data": base64.b64encode(small.read_bytes()).decode()}}]}]}
                        r = self._answer(await http.post(f"{root}/models/{row['remote_id']}:generateContent", json=body,
                                                         headers={"x-goog-api-key": self.app.providers.key("google") or ""}), row)
                        parts = ((r.json().get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
                        text = " ".join(p.get("text", "") for p in parts).strip()
            finally:
                small.unlink(missing_ok=True)
            self._usage(row, float((row.get("settings") or {}).get("per_minute") or 0.006) * length / 60, chat_id, job_id,
                        "transcription")
            result = {"text": text, "segments": [], "seconds": length}
        result.update(model=row["name"], source=row["kind"], took=round(time.monotonic() - started, 1))
        if out_dir:
            folder = Path(out_dir)
            folder.mkdir(parents=True, exist_ok=True)
            stem = f"{slug(audio.stem)[:40]}-{time.strftime('%Y%m%d-%H%M%S')}"
            (folder / f"{stem}.txt").write_text(result["text"] + "\n", encoding="utf-8")
            result["file"] = str(folder / f"{stem}.txt")
            if result["segments"]:
                (folder / f"{stem}.srt").write_text(srt(result["segments"]), encoding="utf-8")
                result["subtitles"] = str(folder / f"{stem}.srt")
        self.db.update("sound_models", row["id"], {"last_used": now()})
        return result

    # ------------------------------------------------------------------ music
    async def music(self, prompt, *, out_dir, model=None, seconds=None, instrumental=False, lyrics="", chat_id=None,
                    job_id=None, on_status=None):
        status = on_status or (lambda text: None)
        prompt = (prompt or "").strip()
        if not prompt:
            raise SoundError("Describe the music: the style, the instruments, the mood, the tempo")
        row = self.get(model) if model else self.default("music")
        if not row or row["role"] != "music" or not row["ready"]:
            raise SoundError("No music model is set up. Music is made online for now (Google's Lyria): open Sound → "
                             "Models, add the Google key and a music model.")
        conf = self._online_ok(row)
        ask = prompt
        if seconds:
            ask += f"\nLength: about {int(seconds)} seconds."
        if instrumental and not lyrics:
            ask += "\nInstrumental only, no vocals."
        if lyrics:
            ask += "\nLyrics:\n" + lyrics.strip()
        status(f"Asking {row['name']} for the music…")
        started = time.monotonic()
        root = re.sub(r"/openai/?$", "", conf["base"].rstrip("/"))
        async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=20)) as http:
            r = self._answer(await http.post(f"{root}/interactions", json={"model": row["remote_id"], "input": ask},
                                             headers={"x-goog-api-key": self.app.providers.key("google") or ""}), row)
        audio, words = self._interaction_audio(r.json())
        if not audio:
            raise SoundError(f"{row['name']} answered without music (it may have declined the request)" + (f": {words[:200]}" if words else "."))
        ext = "wav" if audio[:4] == b"RIFF" else "mp3"
        dest = Path(out_dir) / f"{slug(prompt)[:40]}-{time.strftime('%Y%m%d-%H%M%S')}.{ext}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(audio)
        cost = float((row.get("settings") or {}).get("per_item") or 0.10)
        self._usage(row, cost, chat_id, job_id, "music")
        params = {"mode": "music", "seconds": seconds_of(dest), "cost": cost, "words": words[:2000],
                  "took": round(time.monotonic() - started, 1)}
        return self._record(dest, prompt, row["name"], params, chat_id, "online")

    # ------------------------------------------------------------------ mixing
    async def mix(self, tracks, *, out_dir, name="mix", fmt="mp3", length="first", fade=2.0, chat_id=None):
        """Lay recordings over each other: [{"path", "volume" (1 = as it is), "start" (seconds)}]. `length` is
        "first" (as long as the first track, e.g. the voice, plus the fade) or "longest"."""
        if not tracks:
            raise SoundError("Give the recordings to mix")

        def work():
            import numpy as np
            rate, loaded = 44100, []
            for t in tracks:
                samples = read_pcm(t["path"], rate, 2) * float(t.get("volume", 1.0) or 1.0)
                loaded.append((int(float(t.get("start") or 0) * rate), samples))
            ends = [start + len(s) for start, s in loaded]
            total = min(max(ends), ends[0] + int(fade * rate)) if length == "first" else max(ends)
            out = np.zeros((total, 2), dtype="float32")
            for start, samples in loaded:
                piece = samples[: max(0, total - start)]
                out[start:start + len(piece)] += piece
            n = min(total, int(fade * rate))
            if n and len(loaded) > 1:
                out[-n:] *= np.linspace(1.0, 0.0, n, dtype="float32")[:, None]
            peak = float(np.abs(out).max()) if total else 0.0
            if peak > 0.98:
                out *= 0.98 / peak
            dest = Path(out_dir) / f"{slug(name)[:40]}-{time.strftime('%Y%m%d-%H%M%S')}.{fmt if fmt in ('wav', 'mp3', 'm4a') else 'mp3'}"
            return write_audio(dest, out, rate), round(total / rate, 2)
        dest, seconds = await asyncio.to_thread(work)
        return self._record(dest, name, "mix", {"mode": "mix", "seconds": seconds,
                                                "tracks": [Path(t["path"]).name for t in tracks]}, chat_id, "local")

    # ------------------------------------------------------------------ the Sound window
    def status(self):
        can = self.can()
        return {"library": self.library(), "espeak": self.espeak(), "can": can, "models": self.models(),
                "catalog": self.catalog(), "voices": self.voices(),
                "defaults": {role: (self.default(role) or {}).get("id") for role in ROLES},
                "default_voice": (self.voice() if can["tts"] and any(v["ready"] for v in self.voices()) else {}).get("id"),
                "online": [{"id": p, "name": s["name"], "ready": self.app.providers.ready(p),
                            "roles": [r for r in ("tts", "stt", "music") if r in s]} for p, s in ONLINE.items()],
                "jobs": list(self.jobs.values())[-8:],
                "claude": "Claude does not speak, listen or make music through its interface. A chat that runs on "
                          "Claude can still use the sound tools: they work with the models chosen here.",
                "note": "" if self.espeak() else
                        "Voices on this computer read English and Chinese. The speech library's current version left "
                        "out the pronunciation component for other languages because of its licence; Piper voices "
                        "(Swahili, French and more) and Kitten need the earlier version. Online voices read many languages."}

    def audio_library(self, limit=80, chat_id=None):
        sql, args = "SELECT * FROM media WHERE kind='audio'", []
        if chat_id:
            sql += " AND chat_id=?"
            args.append(chat_id)
        rows = self.db.all(sql + " ORDER BY created DESC LIMIT ?", [*args, limit])
        return [{**r, "name": Path(r["path"]).name, "exists": Path(r["path"]).exists()} for r in rows]

    def start_job(self, kind, data):
        """A request from the Sound window: runs in the background, reports through event `sound_job`."""
        job = {"id": new_id("snd_"), "kind": kind, "state": "running", "text": "Starting…", "result": None, "error": None,
               "title": str(data.get("text") or data.get("prompt") or Path(str(data.get("audio") or "")).name)[:120], "started": now()}
        self.jobs[job["id"]] = job
        for old in list(self.jobs)[:-16]:
            if self.jobs[old]["state"] != "running":
                self.jobs.pop(old)
        publish = lambda: self.app.bus.publish("sound_job", **job)        # noqa: E731

        def status(text):
            job["text"] = text
            publish()
        out = self.app.paths.data / "sound"

        async def run():
            try:
                if kind == "speak":
                    job["result"] = await self.speak(data.get("lines") or str(data.get("text") or ""), out_dir=out,
                                                     voice=data.get("voice"), voices=data.get("voices"),
                                                     speed=data.get("speed") or 1.0, fmt=data.get("format") or "mp3",
                                                     name=data.get("name"), on_status=status)
                elif kind == "transcribe":
                    job["result"] = await self.transcribe(data.get("audio"), model=data.get("model"),
                                                          language=data.get("language") or "", out_dir=out / "transcripts",
                                                          on_status=status)
                elif kind == "music":
                    job["result"] = await self.music(data.get("prompt"), out_dir=out, model=data.get("model"),
                                                     seconds=data.get("seconds"), instrumental=bool(data.get("instrumental")),
                                                     lyrics=data.get("lyrics") or "", on_status=status)
                elif kind == "mix":
                    job["result"] = await self.mix(data.get("tracks") or [], out_dir=out, name=data.get("name") or "mix",
                                                   fmt=data.get("format") or "mp3", length=data.get("length") or "first")
                else:
                    raise SoundError(f"Unknown kind of sound job: {kind}")
                job.update(state="done", text="Done")
            except asyncio.CancelledError:
                job.update(state="stopped", text="Stopped")
            except Exception as exc:
                job.update(state="failed", text="", error=str(exc)[:500])
                if not isinstance(exc, (SoundError, ValueError)):
                    self.app.log(f"sound job: {type(exc).__name__}: {exc}")
            finally:
                self.tasks.pop(job["id"], None)
                publish()
        self.tasks[job["id"]] = self.app.spawn(run())
        return job

    def stop_job(self, job_id):
        task = self.tasks.get(job_id)
        if task:
            task.cancel()

    def forget(self, media_id, delete_file=False):
        row = self.db.get("media", media_id)
        if not row:
            raise KeyError(media_id)
        if delete_file:
            Path(row["path"]).unlink(missing_ok=True)
        self.db.delete("media", media_id)
        self.app.bus.publish("sound_changed")
