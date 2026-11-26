"""Sound (2.12.0): speech from text, voices (also your own), transcription, music, and mixing them.

On this computer   Speech and transcription run in a separate process per request (kimilm/sound_worker.py, with the
                   sherpa-onnx library: ONNX models on the processor, no graphics card needed). The models are
                   downloaded in the app from the library's own releases: Kokoro (54 voices), Pocket TTS (speaks
                   with a voice taken from a short recording), Whisper (transcription, 99 languages).
                   The library's 2.x versions no longer contain the pronunciation component espeak-ng (it is
                   GPL-licensed), so with them local speech covers what Kokoro's own dictionaries cover: English
                   and Chinese. Models that need it (Piper voices, Kitten, Kokoro's other languages) are offered
                   only when a 1.x version of the library is installed (`espeak()`).
Music here         ACE-Step 1.5 through acestep.cpp (2.13.0): two programs, `ace-lm` (plans the piece: structure, words,
                   audio codes) and `ace-synth` (renders the sound), run one after the other for a request and then
                   gone. Its authors publish no ready-made programs, so they are built from the pinned source by a
                   workflow in KimiLM's own repository and downloaded from that repository's releases; a folder
                   with programs you already have works too. The models come from Hugging Face.
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
from .download import download, fetch_json
from .images import HF, fetch, pick_file
from .jobs import turn
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
     "about": "Speaks English with a voice taken from a recording: it hears about one second of it. This is what your own voice uses.",
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
# Music on this computer: every model is four files from one repository (chosen from its listing when downloaded).
ACE_REPO = "Serveurperso/ACE-Step-1.5-GGUF"


def ace(key, name, mb, ram, about, lm, dit, recommended=False):
    part = lambda slot, match, prefer: {"slot": slot, "sources": [{"repo": ACE_REPO, "match": match}], "prefer": prefer}  # noqa: E731
    return {"key": key, "role": "music", "family": "ace", "name": name, "mb": mb, "min_ram_gb": ram, "about": about,
            "recommended": recommended, "licence": "MIT", "page": f"https://huggingface.co/{ACE_REPO}", "voices": [],
            "hf": [part("lm", rf"^acestep-5Hz-lm-{lm}-.*\.gguf$", ["Q8_0", "Q6_K", "Q5_K_M"]),
                   part("dit", r"^acestep-v15-turbo-(Q|BF)[^-]*\.gguf$", dit),
                   part("encoder", r"^Qwen3-Embedding-0\.6B-.*\.gguf$", ["Q8_0"]),
                   part("vae", r"^vae-.*\.gguf$", ["BF16"])]}


CATALOG += [
    ace("ace-step-light", "ACE-Step 1.5 (light)", 3300, 8, "Music from a description, with or without singing, on this "
        "computer. The lightest set: expect several minutes for 30 seconds of music on a processor alone.",
        "0\\.6B", ["Q4_K_M", "Q5_K_M", "Q6_K", "Q8_0"], recommended=True),
    ace("ace-step", "ACE-Step 1.5 (better)", 5700, 16, "The same model with a larger planner and finer weights: "
        "better structure and words, slower, and it wants 16 GB of memory or a graphics card.",
        "1\\.7B", ["Q8_0", "Q6_K"]),
]
ACE_LM, ACE_SYNTH = ("ace-lm.exe", "ace-synth.exe") if os.name == "nt" else ("ace-lm", "ace-synth")
ACE_ASSET = {"nt": "acestep-windows-x64.zip"}.get(os.name, "acestep-linux-x64.tar.gz")   # no ARM64 build is published yet
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
        if os.environ.get("KIMILM_SOUND_LIBRARY"):          # tests name the version they stand in for
            return os.environ["KIMILM_SOUND_LIBRARY"]
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
            r["default"] = self.db.setting(f"sound_{r['role']}", "") == r["id"]
            if r["kind"] == "local":
                entry = next((c for c in CATALOG if c["key"] == r["id"]), {})
                if r["family"] == "ace":
                    r["ready"] = Path(r["path"]).is_dir() and bool(self.music_engine())
                    r["why"] = "" if r["ready"] else ("its files are missing" if not Path(r["path"]).is_dir() else
                                                      "the music engine is not installed")
                    continue
                r["ready"] = bool(self.library()) and Path(r["path"]).is_dir() and self.usable(entry)
                r["why"] = "" if r["ready"] else ("the speech library is not installed" if not self.library() else
                                                  "its files are missing" if not Path(r["path"]).is_dir() else
                                                  "it needs the wider pronunciation library")
            else:
                r["ready"] = self.app.providers.ready(r["provider"])
                r["why"] = "" if r["ready"] else "its provider has no key"
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
        kind = "*.gguf" if entry["family"] == "ace" else "*.onnx"
        if not folder.is_dir() or not any(folder.glob(kind)):
            raise ValueError(f"{folder} is not a folder with a model's {kind[1:]} files in it")
        size = sum(p.stat().st_size for p in folder.rglob("*") if p.is_file())
        settings = self._ace_settings(folder) if entry["family"] == "ace" else {}
        return self._insert({"id": entry["key"], "kind": "local", "role": entry["role"], "family": entry["family"],
                             "name": entry["name"], "path": str(folder), "size": size, "licence": entry["licence"],
                             "settings": settings})

    @staticmethod
    def _ace_settings(folder):
        """Which file is the planner and which the renderer (the programs are told by name)."""
        names = sorted(p.name for p in Path(folder).glob("*.gguf"))
        lm = next((n for n in names if "-lm-" in n), "")
        dit = next((n for n in names if n.startswith("acestep-v15")), "")
        if not lm or not dit or not any(n.startswith("vae") for n in names) or not any("Embedding" in n for n in names):
            raise ValueError("A music model needs four files: the planner (…-lm-…), the renderer (acestep-v15-…), "
                             "the text encoder (Qwen3-Embedding-…) and the VAE (vae-…)")
        return {"lm": lm, "dit": dit, "steps": 8, "shift": 3.0}

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
        try:
            import psutil
            ram_gb = psutil.virtual_memory().total / 2**30
        except Exception:
            ram_gb = 0
        return [{k: c[k] for k in ("key", "role", "name", "mb", "licence", "page", "about")}
                | {"installed": c["key"] in have, "usable": self.usable(c), "recommended": bool(c.get("recommended")),
                   "fits": not c.get("min_ram_gb") or not ram_gb or ram_gb >= c["min_ram_gb"] * 0.93,
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
        self.app.jobs.follow("download", "Download: " + entry["name"] + " (" + {"tts": "voice", "clone": "voice", "stt": "transcription", "music": "music"}.get(entry.get("role"), "sound") + " model)",
                             self._download(entry, state), lambda: state, stop=lambda: state.update(cancel=True),
                             payload={"what": "sound model", "key": key})
        return state

    def cancel_download(self, key):
        if key in self.downloads:
            self.downloads[key]["cancel"] = True

    async def _download(self, entry, state):
        publish = lambda: self.app.bus.publish("sound_download", **{k: v for k, v in state.items() if k != "cancel"})  # noqa: E731
        folder = self.folder()
        try:
            if entry.get("hf"):
                return await self._download_parts(entry, state, publish)
            archive = folder / Path(entry["url"]).name

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
        finally:
            publish()
            self.app.bus.publish("sound_changed")

    async def _download_parts(self, entry, state, publish):
        """A model made of several files of a Hugging Face repository (music): the files are chosen from the
        repository's listing, because publishers add and rename quantisations."""
        from urllib.parse import quote
        token = self.app.secrets.get("hf_token")
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        listing = {}
        for repo in {s["repo"] for p in entry["hf"] for s in p["sources"]}:
            tree = await fetch_json(f"{HF}/api/models/{repo}/tree/main?recursive=true", headers)
            listing[repo] = [{"path": f["path"], "size": (f.get("lfs") or {}).get("size") or f.get("size") or 0}
                             for f in tree if f.get("type") == "file"]
        chosen = []
        for part in entry["hf"]:
            repo, f = pick_file(listing, part)
            if not f:
                raise SoundError(f"The {part['slot']} file was not found in {part['sources'][0]['repo']}. The publisher "
                                 "may have renamed it.")
            chosen.append((repo, f))
        state["total"] = sum(f["size"] for _, f in chosen)
        target, before = self.folder() / entry["key"], 0
        for repo, f in chosen:
            dest = target / Path(f["path"]).name

            def progress(done, total, rate, before=before):
                state.update(state="downloading", done=before + done)
                publish()
            if not (dest.exists() and f["size"] and dest.stat().st_size == f["size"]):
                await fetch(f"{HF}/{repo}/resolve/main/{quote(f['path'])}?download=true", dest, state=state, headers=headers,
                            expected_size=f["size"] or None, progress=progress)
            before += f["size"]
        self._insert({"id": entry["key"], "kind": "local", "role": entry["role"], "family": entry["family"],
                      "name": entry["name"], "path": str(target), "size": state["total"], "licence": entry["licence"],
                      "settings": self._ace_settings(target)})
        state.update(state="done", done=state["total"])

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
    async def _worker(self, job, status, track=None):
        """Run one job in its own process and return its last message."""
        folder = self.app.paths.data / "tmp"
        folder.mkdir(exist_ok=True)
        spec = folder / f"{new_id('snd_')}.json"
        spec.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
        stand_in = os.environ.get("KIMILM_SOUND_WORKER")
        cmd = [sys.executable, stand_in, str(spec)] if stand_in else [sys.executable, "-m", "kimilm.sound_worker", str(spec)]
        env = {**os.environ, "PYTHONPATH": os.pathsep.join(
            x for x in (str(Path(__file__).resolve().parents[1]), os.environ.get("PYTHONPATH", "")) if x)}
        env.pop("KIMILM_TOKEN", None)
        proc = await asyncio.create_subprocess_exec(*cmd, env=env, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.PIPE,
                                                    creationflags=0x08000000 if os.name == "nt" else 0)
        last, errors = {}, []

        async def notes():
            async for raw in proc.stderr:
                errors.append(raw.decode("utf-8", "replace").strip())
                del errors[:-30]
                if track and errors[-1] and "deprecated" not in errors[-1]:
                    track.output(errors[-1])
        reader = asyncio.ensure_future(notes())
        try:
            async for raw in proc.stdout:
                try:
                    last = json.loads(raw)
                except ValueError:
                    continue
                if last.get("text") and "done" not in last:
                    if last.get("beat"):          # the voice is at work: a sign of life, not a new step
                        getattr(status, "beat", status)(last["text"])
                    else:
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
        status, track = self.app.jobs.reporter(on_status, job_id)
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
        track.stages(["Prepare the voices", "Speak", "Join and save"])
        track.plan(speech={"voices": cast, "lines": len(lines), "words": sum(len(ln["text"].split()) for ln in lines),
                           "models": sorted({models[ln["_voice"]["model"]]["name"] for ln in lines}),
                           "where": "on this computer" if all(models[ln["_voice"]["model"]]["kind"] == "local" for ln in lines)
                           else "online (in part)"})
        track.at("Speak")
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
                        if ln.get("pause") is not None:        # the silence after this line (a script's own rhythm)
                            seg["pause"] = float(ln["pause"])
                        if v["kind"] == "recording":
                            row = self.db.get("voices", v["id"].removeprefix("voice:"))
                            seg.update(ref_audio=row["ref_audio"], ref_text=row.get("ref_text") or "")
                        else:
                            entry = next(c for c in CATALOG if c["key"] == m["id"])
                            seg["sid"] = next(x["sid"] for x in entry["voices"] if x["speaker"] == v["id"].split(":", 1)[1])
                        job["segments"].append(seg)
                    async with turn(self.lock, status, "Waiting for another sound job on this computer to finish…"):
                        done = await self._worker(job, status, track)
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
            track.at("Join and save")
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
        status, track = self.app.jobs.reporter(on_status, job_id)
        audio = Path(audio)
        if not audio.is_file():
            raise SoundError(f"There is no file at {audio}")
        row = self.get(model) if model else self.default("stt")
        if not row or row["role"] != "stt" or not row["ready"]:
            raise SoundError("No transcription model is set up. Open Sound → Models and download Whisper for this "
                             "computer, or switch on an online one.")
        started = time.monotonic()
        track.stages(["Read the recording", "Write down what is said", "Save the text"])
        track.plan(transcription={"recording": audio.name, "model": row["name"], "language": language or "found by the model",
                                  "where": "on this computer" if row["kind"] == "local" else "online"})
        if row["kind"] == "local":
            vad = self.folder() / "silero_vad.onnx"
            if not vad.exists():
                status("Fetching the small model that finds speech…")
                await download(VAD_URL, vad)
            job = {"kind": "transcribe", "audio": str(audio), "vad": str(vad),
                   "model": {"family": row["family"], "dir": row["path"], "language": language or ""}}
            track.at("Write down what is said")
            async with turn(self.lock, status, "Waiting for another sound job on this computer to finish…"):
                done = await self._worker(job, status, track)
            result = {"text": done["text"], "segments": done["segments"], "seconds": done["seconds"]}
        else:
            conf = self._online_ok(row)
            track.at("Write down what is said")
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
        track.at("Save the text")
        track.plan(transcript={"words": len(result["text"].split()), "seconds": result["seconds"], "text": result["text"][:1500]})
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

    # ------------------------------------------------------------------ the music engine (acestep.cpp)
    def music_engine(self):
        """The folder with ace-lm and ace-synth: KIMILM_ACE_DIR, a folder the user chose, or the installed one."""
        for source, folder in (("your own (KIMILM_ACE_DIR)", os.environ.get("KIMILM_ACE_DIR")),
                               ("a folder you chose", self.db.setting("music_engine_dir", "")),
                               ("installed", str(self.app.paths.data / "runtimes" / "acestep"))):
            if folder and (Path(folder) / ACE_LM).exists() and (Path(folder) / ACE_SYNTH).exists():
                return {"dir": str(folder), "source": source}
        return None

    def set_music_engine(self, folder):
        folder = str(folder or "").strip()
        if folder and not ((Path(folder) / ACE_LM).exists() and (Path(folder) / ACE_SYNTH).exists()):
            raise ValueError(f"{folder} does not contain {ACE_LM} and {ACE_SYNTH}")
        self.db.set_setting("music_engine_dir", folder)
        self.app.bus.publish("sound_changed")

    def engine_source(self):
        v = json.loads(Path(__file__).with_name("versions.json").read_text(encoding="utf-8"))
        repo = os.environ.get("KIMILM_MUSIC_ENGINE_REPO") or v["music_engine_repo"]
        return {"repo": repo, "tag": v["music_engine_tag"], "commit": v["acestep_commit"], "built_from": v["acestep_repo"],
                "url": f"https://github.com/{repo}/releases/download/{v['music_engine_tag']}/{ACE_ASSET}",
                "asset": ACE_ASSET,
                "release_api": f"https://api.github.com/repos/{repo}/releases/tags/{v['music_engine_tag']}",
                "workflow": f"https://github.com/{repo}/actions/workflows/music-engine.yml"}

    async def _missing_build_reason(self, src):
        """Why the download was not there: no release at all, or a release without a build for this system."""
        not_built = ("The music engine has not been built yet. It is built once by the workflow "
                     f"“Build music engine” in {src['repo']} on GitHub (Actions → Build music engine → "
                     "Run workflow, about 15 minutes); then press Install again. Or choose a folder "
                     "that already holds the programs.")
        api = src.get("release_api")
        if not api:
            return not_built
        try:
            release = await fetch_json(api, headers={"Accept": "application/vnd.github+json"}, timeout=15)
        except Exception:      # no such release (404), offline or rate-limited: the general sentence fits
            return not_built
        names = [a.get("name", "") for a in (release.get("assets") or []) if isinstance(a, dict)]
        system = "Windows" if os.name == "nt" else "Linux" if sys.platform.startswith("linux") else sys.platform
        has = ", ".join(n for n in names if n) or "nothing yet"
        return (f"The music engine release {src['tag']} in {src['repo']} has no build for {system} "
                f"({src.get('asset', ACE_ASSET)}); it holds: {has}. Run the workflow “Build music engine” again "
                "(Actions → Build music engine → Run workflow, about 15 minutes): it builds for Windows and Linux. "
                "Then press Install again. Or choose a folder that already holds the programs.")

    async def install_music_engine(self):
        """Download the programs that KimiLM's own repository built from the pinned acestep.cpp source."""
        if getattr(self, "engine_installing", None):
            return
        src = self.engine_source()
        state = self.engine_installing = self.engine_last = {"state": "downloading", "done": 0, "total": 0, "cancel": False,
                                                             "file": ACE_ASSET}
        publish = lambda **kw: self.app.bus.publish("music_engine", **kw)        # noqa: E731
        target = self.app.paths.data / "runtimes" / "acestep"
        archive = target.with_name(ACE_ASSET)
        try:
            def progress(done, total, rate):
                state.update(done=done, total=total)
                publish(action="progress", done=done, total=total)
            try:
                await fetch(src["url"], archive, state=state, progress=progress)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    raise SoundError(await self._missing_build_reason(src)) from exc
                raise
            tmp = target.with_name("acestep.tmp")
            shutil.rmtree(tmp, ignore_errors=True)
            tmp.mkdir(parents=True)
            state.update(state="extracting")
            if archive.suffix == ".zip":
                from .runtime import Runtime
                await asyncio.to_thread(Runtime._extract, archive, tmp)
            else:
                with tarfile.open(archive, "r:*") as tar:
                    tar.extractall(tmp, filter="data")
            archive.unlink(missing_ok=True)
            exe = next(iter(tmp.rglob(ACE_SYNTH)), None)
            if not exe or not (exe.parent / ACE_LM).exists():
                raise SoundError("The download did not contain the music programs (ace-lm, ace-synth)")
            shutil.rmtree(target, ignore_errors=True)
            shutil.move(str(exe.parent), str(target))
            shutil.rmtree(tmp, ignore_errors=True)
            if os.name != "nt":
                for name in (ACE_LM, ACE_SYNTH):
                    (target / name).chmod(0o755)
            self.db.set_setting("music_engine_tag", src["tag"])
            state.update(state="done")
            publish(action="installed")
        except Exception as exc:
            self.app.log(f"music engine: {type(exc).__name__}: {exc}")
            state.update(state="cancelled" if state["cancel"] else "failed", error=str(exc)[:500])
            publish(action="failed", error=str(exc)[:500])
        finally:
            self.engine_installing = None
            self.app.bus.publish("sound_changed")

    def remove_music_engine(self):
        shutil.rmtree(self.app.paths.data / "runtimes" / "acestep", ignore_errors=True)
        self.app.bus.publish("sound_changed")

    def local_music(self):
        """True when music without a named model is made on this computer."""
        row = self.default("music")
        return bool(row and row["kind"] == "local")

    async def _program(self, cmd, cwd, status, label, minutes, track=None):
        """Run one of the music programs to its end; returns its last lines. Stops it when the job is stopped."""
        env = {**os.environ}
        env.pop("KIMILM_TOKEN", None)
        if os.name != "nt":
            env["LD_LIBRARY_PATH"] = f"{cwd}{os.pathsep}{env.get('LD_LIBRARY_PATH', '')}"
        status(f"{label}…")
        if track:
            track.note("Started: " + " ".join(Path(c).name if i == 0 else c for i, c in enumerate(cmd)))
        proc = await asyncio.create_subprocess_exec(*cmd, cwd=str(cwd), env=env, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.STDOUT,
                                                    creationflags=0x08000000 if os.name == "nt" else 0)
        tail, began = [], time.monotonic()

        async def pump():
            buffer = b""
            while True:
                chunk = await proc.stdout.read(512)
                if not chunk:
                    break
                buffer += chunk
                *lines, buffer = re.split(rb"[\r\n]", buffer)
                for raw in lines:
                    line = raw.decode("utf-8", "replace").strip()
                    if line:
                        tail.append(line)
                        del tail[:-40]
                        if track:
                            track.output(line)
                        m = re.match(r"\[([\w-]+)\]\s*(Step \d+)?", line)
                        if m:
                            getattr(status, "beat", status)(
                                f"{label} ({int(time.monotonic() - began)} s{', ' + m.group(2).lower() if m.group(2) else ''})")
        try:
            await asyncio.wait_for(pump(), minutes * 60)
            code = await proc.wait()
        except asyncio.TimeoutError:
            proc.kill()
            raise SoundError(f"The music took longer than the limit of {minutes:g} minutes and was stopped. Ask for a "
                             "shorter piece, or raise the limit in Sound → Voices & models.")
        except asyncio.CancelledError:
            proc.kill()
            raise
        return code, tail

    async def _music_local(self, row, prompt, seconds, instrumental, lyrics, language, seed, status, track=None):
        engine = self.music_engine()
        if not engine:
            raise SoundError("The music engine is not installed (Sound → Voices & models → Music on this computer).")
        s = row.get("settings") or {}
        work = self.app.paths.data / "tmp" / new_id("mus_")
        work.mkdir(parents=True)
        request = {"caption": prompt, "lyrics": lyrics.strip() or ("[Instrumental]" if instrumental else ""),
                   "duration": float(max(10, min(600, int(seconds or 30)))), "seed": int(seed) if seed not in (None, "") else -1,
                   "inference_steps": int(s.get("steps") or 8), "shift": float(s.get("shift") or 3.0),
                   "lm_model": s.get("lm") or "", "synth_model": s.get("dit") or "", "output_format": "mp3"}
        if language:
            request["vocal_language"] = language
        (work / "request.json").write_text(json.dumps(request), encoding="utf-8")
        track = track or self.app.jobs.track(None)
        track.stages(["Plan the piece", "Render the sound", "Save"])
        track.plan(music={"model": row["name"], "where": "on this computer", "engine": engine["dir"],
                          "programs": ["ace-lm plans the piece (structure, words, audio codes)", "ace-synth renders the sound"],
                          "request": request})
        folder = Path(row["path"])
        need = int(sum(p.stat().st_size for p in folder.glob("*.gguf")) * 1.1) + 2**30
        minutes = float(self.db.setting("music_timeout_minutes", 90))
        try:
            status("Making room in memory for the music model…")
            try:
                await self.app.images._free_memory(need)
            except Exception:
                pass
            exe = Path(engine["dir"])
            track.at("Plan the piece")
            code, tail = await self._program([str(exe / ACE_LM), "--models", str(folder), "--request", str(work / "request.json")],
                                             exe, status, "Planning the piece", minutes, track)
            planned = work / "request0.json"
            if code != 0 or not planned.exists():
                raise SoundError(self._music_error(tail, code, "planning"))
            track.at("Render the sound")
            code, tail = await self._program([str(exe / ACE_SYNTH), "--models", str(folder), "--request", str(planned)],
                                             exe, status, "Rendering the sound", minutes, track)
            made = next(iter(sorted(work.glob("request0*.mp3")) + sorted(work.glob("request0*.wav"))), None)
            if code != 0 or not made:
                raise SoundError(self._music_error(tail, code, "rendering"))
            try:
                plan = json.loads(planned.read_text(encoding="utf-8"))
            except ValueError:
                plan = {}
            track.plan(music_plan={k: plan[k] for k in ("bpm", "keyscale", "timesignature", "vocal_language", "lyrics") if plan.get(k)})
            keep = work.with_suffix(made.suffix)
            shutil.move(str(made), str(keep))
            track.at("Save")
            words = plan.get("lyrics") or ""
            facts = {k: plan[k] for k in ("bpm", "keyscale", "timesignature", "vocal_language") if plan.get(k)}
            return keep, ("" if words.strip() == "[Instrumental]" else words), facts
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def _music_error(self, tail, code, phase):
        self.app.log(f"music engine ({phase}) exit {code}: " + " | ".join(tail[-10:]))
        text = " ".join(tail[-15:]).lower()
        if "alloc" in text or "out of memory" in text or code in (-9, 137):
            return "This computer ran out of memory for the music model. Close other apps, or ask for a shorter piece."
        last = next((line for line in reversed(tail) if "fatal" in line.lower() or "error" in line.lower()), "")
        return (f"The music engine stopped while {phase} (code {code})" + (f": {last[:300]}" if last else ".")
                + " Its last lines are in the app's log.")

    # ------------------------------------------------------------------ music
    async def music(self, prompt, *, out_dir, model=None, seconds=None, instrumental=False, lyrics="", chat_id=None,
                    job_id=None, on_status=None, language="", seed=None):
        status, track = self.app.jobs.reporter(on_status, job_id)
        prompt = (prompt or "").strip()
        if not prompt:
            raise SoundError("Describe the music: the style, the instruments, the mood, the tempo")
        row = self.get(model) if model else self.default("music")
        if not row or row["role"] != "music" or not row["ready"]:
            raise SoundError("No music model is set up. Open Sound → Voices & models: install the music engine and "
                             "download ACE-Step for this computer, or add an online music model (Google's Lyria).")
        if row["kind"] == "local":
            started = time.monotonic()
            async with turn(self.lock, status, "Waiting for another sound job on this computer to finish…"):
                made, words, facts = await self._music_local(row, prompt, seconds, instrumental, lyrics, language, seed, status, track)
            dest = Path(out_dir) / f"{slug(prompt)[:40]}-{time.strftime('%Y%m%d-%H%M%S')}{made.suffix}"
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(made), str(dest))
            self.db.update("sound_models", row["id"], {"last_used": now()})
            params = {"mode": "music", "seconds": seconds_of(dest), "words": words[:2000], **facts,
                      "took": round(time.monotonic() - started, 1)}
            return self._record(dest, prompt, row["name"], params, chat_id, "local")
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
    async def mix(self, tracks, *, out_dir, name="mix", fmt="mp3", length="first", fade=2.0, chat_id=None, keep=True, job_id=None):
        """Lay recordings over each other: [{"path", "volume" (1 = as it is), "start" (seconds), "from" / "to" (the
        part of the recording to use), "fade_in", "fade_out", "mute"}]. `length` is "first" (as long as the first
        track, e.g. the voice, plus the fade) or "longest". `keep=False` makes a preview that is not put in the library."""
        tracks = [t for t in tracks or [] if not t.get("mute")]
        if not tracks:
            raise SoundError("Give the recordings to mix")
        status, track = self.app.jobs.reporter(None, job_id)
        track.plan(mix={"tracks": [{"file": Path(t["path"]).name, **{k: t[k] for k in ("start", "from", "to", "volume", "fade_in", "fade_out") if t.get(k)}}
                                   for t in tracks], "length": length})
        status(f"Mixing {len(tracks)} recording{'s' if len(tracks) != 1 else ''}…")

        def work():
            import numpy as np
            rate, loaded = 44100, []
            for t in tracks:
                samples = read_pcm(t["path"], rate, 2)
                a, b = int(float(t.get("from") or 0) * rate), int(float(t.get("to") or 0) * rate)
                samples = samples[max(0, a):b if b > a else None] * float(1.0 if t.get("volume") is None else t["volume"])
                for key, ramp in (("fade_in", (0.0, 1.0)), ("fade_out", (1.0, 0.0))):       # a track's own fades
                    n = min(len(samples), int(float(t.get(key) or 0) * rate))
                    if n:
                        curve = np.linspace(*ramp, n, dtype="float32")[:, None]
                        if key == "fade_in":
                            samples[:n] *= curve
                        else:
                            samples[-n:] *= curve
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
        params = {"mode": "mix", "seconds": seconds, "tracks": [Path(t["path"]).name for t in tracks]}
        if not keep:
            return {"id": "", "kind": "audio", "path": str(dest), "name": dest.name, "prompt": name, "model_name": "mix",
                    "source": "local", "params": params}
        return self._record(dest, name, "mix", params, chat_id, "local")

    @staticmethod
    def peaks(path, n=240):
        """The loudness of a recording in `n` steps (for drawing it), and its length."""
        import numpy as np
        samples = np.abs(read_pcm(path, 8000, 1).reshape(-1))
        if not len(samples):
            return {"seconds": 0.0, "peaks": []}
        edges = np.linspace(0, len(samples), min(n, len(samples)) + 1).astype(int)
        peaks = [round(float(samples[a:b].max()) if b > a else 0.0, 3) for a, b in zip(edges[:-1], edges[1:])]
        return {"seconds": round(len(samples) / 8000, 2), "peaks": peaks}

    # ------------------------------------------------------------------ the Sound window
    def status(self):
        can = self.can()
        engine = self.music_engine()
        return {"library": self.library(), "espeak": self.espeak(), "can": can, "models": self.models(),
                "music_engine": {"installed": bool(engine), **(engine or {}), **self.engine_source(),
                                 "installing": getattr(self, "engine_installing", None) and
                                 {k: v for k, v in self.engine_installing.items() if k != "cancel"},
                                 "timeout_minutes": self.db.setting("music_timeout_minutes", 90)},
                "catalog": self.catalog(), "voices": self.voices(),
                "defaults": {role: (self.default(role) or {}).get("id") for role in ROLES},
                "default_voice": (self.voice() if can["tts"] and any(v["ready"] for v in self.voices()) else {}).get("id"),
                "online": [{"id": p, "name": s["name"], "ready": self.app.providers.ready(p),
                            "roles": [r for r in ("tts", "stt", "music") if r in s]} for p, s in ONLINE.items()],
                "jobs": list(self.jobs.values())[-8:],
                "text_model": bool(self.app.router.pick("worker")[0]),
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
        # "what" repeats the kind: an event's own field `kind` is the event's name, so `kind` is lost on the way
        job = {"id": new_id("snd_"), "kind": kind, "what": kind, "state": "running", "text": "Starting…", "result": None, "error": None,
               "title": (("Podcast: " + ", ".join(Path(str(s)).name for s in data.get("sources") or ["a text"])) if kind == "podcast"
                         else str(data.get("text") or data.get("prompt") or Path(str(data.get("audio") or "")).name))[:120],
               "started": now()}
        self.jobs[job["id"]] = job
        for old in list(self.jobs)[:-16]:
            if self.jobs[old]["state"] != "running":
                self.jobs.pop(old)
        publish = lambda: self.app.bus.publish("sound_job", **job)        # noqa: E731

        def status(text):
            job["text"] = text
            publish()
        out = self.app.paths.data / "sound"

        async def run(jid):
            job["job_id"] = jid
            try:
                if kind == "speak":
                    job["result"] = await self.speak(data.get("lines") or str(data.get("text") or ""), out_dir=out,
                                                     voice=data.get("voice"), voices=data.get("voices"),
                                                     speed=data.get("speed") or 1.0, fmt=data.get("format") or "mp3",
                                                     name=data.get("name"), on_status=status, job_id=jid)
                elif kind == "transcribe":
                    job["result"] = await self.transcribe(data.get("audio"), model=data.get("model"),
                                                          language=data.get("language") or "", out_dir=out / "transcripts",
                                                          on_status=status, job_id=jid)
                elif kind == "music":
                    job["result"] = await self.music(data.get("prompt"), out_dir=out, model=data.get("model"),
                                                     seconds=data.get("seconds"), instrumental=bool(data.get("instrumental")),
                                                     lyrics=data.get("lyrics") or "", language=data.get("language") or "",
                                                     seed=data.get("seed"), on_status=status, job_id=jid)
                elif kind == "podcast":
                    from . import podcast
                    job["result"] = await podcast.make(self.app, sources=data.get("sources") or [], text=data.get("text") or "",
                                                       minutes=data.get("minutes") or 5, style=data.get("style") or "conversation",
                                                       hosts=data.get("hosts"), voices=data.get("voices"),
                                                       focus=data.get("focus") or "", model=data.get("model"),
                                                       music=bool(data.get("music")), out_dir=out, on_status=status, job_id=jid)
                elif kind == "mix":
                    preview = bool(data.get("preview"))                      # heard once, not kept in the library
                    if preview:
                        for old in (self.app.paths.data / "sound" / "previews").glob("*"):
                            old.unlink(missing_ok=True)
                    job["result"] = await self.mix(data.get("tracks") or [], name=data.get("name") or "mix",
                                                   out_dir=out / "previews" if preview else out,
                                                   fmt="wav" if preview else data.get("format") or "mp3",
                                                   length=data.get("length") or "first", keep=not preview, job_id=jid,
                                                   fade=float(2.0 if data.get("fade") is None else data["fade"]))
                else:
                    raise SoundError(f"Unknown kind of sound job: {kind}")
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
            return {"media": [r]} if r.get("path") else {"text": r.get("text", ""), "files": [r.get(k) for k in ("file", "subtitles") if r.get(k)]}
        # a task of its own, so that Activity lists it and its trail can be read (2.14.0)
        names = {"speak": "Speak", "transcribe": "Transcribe", "music": "Music", "mix": "Mix", "podcast": "Podcast"}
        title = job["title"] if kind == "podcast" else f"{names.get(kind, kind)}: {job['title']}"
        row = self.app.jobs.create("sound", title, lambda r: run(r["id"]), payload={"what": kind})
        job["job_id"] = row["id"]
        self.tasks[job["id"]] = row["id"]
        return job

    def stop_job(self, job_id):
        if job_id in self.tasks:
            self.app.jobs.cancel(self.tasks[job_id])

    def forget(self, media_id, delete_file=False):
        row = self.db.get("media", media_id)
        if not row:
            raise KeyError(media_id)
        if delete_file:
            Path(row["path"]).unlink(missing_ok=True)
        self.db.delete("media", media_id)
        self.app.bus.publish("sound_changed")
