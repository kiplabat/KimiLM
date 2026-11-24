"""The process that makes speech and reads speech on this computer (2.12.0).

    python -m kimilm.sound_worker job.json

It loads the models a job needs (sherpa-onnx: ONNX models on the processor), does the job, writes the result and
ends, so its memory is free again. The engine starts one per request (kimilm/sound.py) and reads its output: one
JSON object per line, {"progress": …} while it works and {"done": true, …} or {"error": "…"} at the end.

Jobs
  speak       {"models": {id: {"family", "dir"}}, "segments": [{"model", "text", "sid" | "ref_audio" (+"ref_text"),
              "speed", "pause"}], "out": "x.wav"}  →  one WAV file, and where each segment starts in it
  transcribe  {"model": {"family": "whisper", "dir", "language"}, "vad": "silero_vad.onnx", "audio": "x.mp3"}
              →  {"text", "segments": [{"start", "end", "text"}], "seconds"}
Families: kokoro, kitten, vits (Piper voices), pocket (a voice from a recording); whisper."""
import json
import os
import re
import sys
import tempfile
import time
import wave
from pathlib import Path


def say(**data):
    sys.stdout.write(json.dumps(data, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def espeak():
    """True when the installed library still pronounces unknown words through espeak-ng (its 1.x versions)."""
    try:
        from importlib import metadata
        return metadata.version("sherpa-onnx").startswith("1.")
    except Exception:
        return False


def threads():
    return max(1, min(8, (os.cpu_count() or 2) - 1))


def one(folder, *patterns, optional=False):
    """The first file in a model's folder that matches (publishers name files by model: tiny-encoder.int8.onnx)."""
    for pattern in patterns:
        found = sorted(Path(folder).glob(pattern))
        if found:
            return str(found[0])
    if optional:
        return ""
    raise FileNotFoundError(f"{Path(folder).name} has no file like {patterns[0]}")


def read_audio(path, rate):
    """Any audio or video file as mono float32 samples at `rate`."""
    import av
    import numpy as np
    chunks = []
    with av.open(str(path)) as box:
        stream = next((s for s in box.streams if s.type == "audio"), None)
        if stream is None:
            raise ValueError(f"{Path(path).name} has no sound in it")
        resampler = av.AudioResampler(format="flt", layout="mono", rate=rate)
        for frame in box.decode(stream):
            for out in resampler.resample(frame):
                chunks.append(out.to_ndarray().reshape(-1))
        for out in resampler.resample(None):
            chunks.append(out.to_ndarray().reshape(-1))
    return np.concatenate(chunks).astype("float32") if chunks else np.zeros(0, dtype="float32")


def resample(samples, rate, to):
    import numpy as np
    if rate == to or len(samples) == 0:
        return samples
    n = int(round(len(samples) * to / rate))
    return np.interp(np.linspace(0, len(samples) - 1, n), np.arange(len(samples)), samples).astype("float32")


def write_wav(path, samples, rate):
    import numpy as np
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    data = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes(data.tobytes())


# ---------------------------------------------------------------------------------------------------------------
# Words the dictionary does not know. Kokoro pronounces from a dictionary (about 180,000 English words); without
# the espeak-ng component (left out of the library's 2.x versions) a word that is not in it is skipped in silence,
# and many ordinary forms are missing ("households", "described", "makes"), as are names ("KimiLM", "amapiano").
# For every such word of a request a pronunciation is worked out and given to the model as a second dictionary:
#   1. from a word that is known: plural and verb endings (-s, -ed, -ing, -ly, -er, -ness, -ment…), possessives;
#   2. as two known words written together ("down" + "loads");
#   3. short all-capital or vowel-less words as letters ("CPU", "MCP");
#   4. otherwise letter by letter with plain vowels (a e i o u as in Spanish or Swahili), which reads names and
#      foreign words recognisably instead of dropping them.
VOICELESS, SIBILANT = set("p t k f θ".split()), set("s z ʃ ʒ ʧ ʤ".split())
LETTERS = {"sh": "ʃ", "ch": "ʧ", "th": "θ", "ph": "f", "ng": "ŋ", "ny": "n j", "kh": "k", "gh": "g", "ts": "t s", "zh": "ʒ",
           "oo": "u", "ee": "i", "ai": "I", "ay": "A", "ei": "A", "ou": "u", "au": "W", "oi": "Y", "oy": "Y", "ck": "k", "qu": "k w",
           "a": "ɑ", "e": "ɛ", "i": "i", "o": "O", "u": "u", "y": "j", "b": "b", "c": "k", "d": "d", "f": "f", "g": "ɡ", "h": "h",
           "j": "ʤ", "k": "k", "l": "l", "m": "m", "n": "n", "p": "p", "q": "k", "r": "ɹ", "s": "s", "t": "t", "v": "v", "w": "w",
           "x": "k s", "z": "z"}


def s_ending(ph):
    last = ph.split()[-1]
    return ph + (" ᵻ z" if last in SIBILANT else " s" if last in VOICELESS else " z")


def ed_ending(ph):
    last = ph.split()[-1]
    return ph + (" ᵻ d" if last in ("t", "d") else " t" if last in VOICELESS | {"s", "ʃ", "ʧ"} else " d")


def pronounce(word, lex, tokens, spelled=False):
    """Phonemes for a word the dictionary lacks, or None. `lex` is the dictionary, `tokens` the model's symbols."""
    w = word.lower().strip("'")
    if w in lex:
        return lex[w]
    bits = re.findall(r"[A-Z][a-z]+|[A-Z]+(?![a-z])|[a-z]+", word)        # KimiLM → Kimi + LM, OpenAI → Open + AI
    if not spelled and len(bits) > 1 and "".join(bits) == word:
        said = [pronounce(b, lex, tokens) for b in bits]
        if all(said):
            return " ".join(said)
    for end, cut in (("'s", 2), ("s'", 1), ("’s", 2)):
        if w.endswith(end) and w[:-cut] in lex:
            return s_ending(lex[w[:-cut]]) if not w[:-cut].endswith("s") or end != "s'" else lex[w[:-cut]]
    tries = []
    if w.endswith("ies"):
        tries.append((w[:-3] + "y", s_ending))
    if w.endswith("es"):
        tries += [(w[:-2], s_ending), (w[:-1], s_ending)]
    if w.endswith("s") and not w.endswith("ss"):
        tries.append((w[:-1], s_ending))
    if w.endswith("ied"):
        tries.append((w[:-3] + "y", ed_ending))
    if w.endswith("ed"):
        tries += [(w[:-1], ed_ending), (w[:-2], ed_ending)] + ([(w[:-3], ed_ending)] if len(w) > 4 and w[-3] == w[-4] else [])
    if w.endswith("ing"):
        tries += [(w[:-3], lambda p: p + " ɪ ŋ"), (w[:-3] + "e", lambda p: p + " ɪ ŋ")] + (
            [(w[:-4], lambda p: p + " ɪ ŋ")] if len(w) > 5 and w[-4] == w[-5] else [])
    for end, sound in (("ly", "l i"), ("er", "ə ɹ"), ("ers", "ə ɹ z"), ("est", "ə s t"), ("ness", "n ə s"), ("ment", "m ə n t"),
                       ("ments", "m ə n t s"), ("less", "l ə s"), ("ful", "f ə l"), ("able", "ə b ə l"), ("ation", "A ʃ ə n")):
        if w.endswith(end):
            tries += [(w[:-len(end)], lambda p, s=sound: p + " " + s), (w[:-len(end)] + "e", lambda p, s=sound: p + " " + s)]
    for base, rule in tries:
        if len(base) > 1 and base in lex:
            return rule(lex[base])
    if not spelled:                                    # two known words written together, the longest first part first
        for i in range(len(w) - 3, 2, -1):
            if w[:i] in lex:
                rest = pronounce(w[i:], lex, tokens, spelled=True) if len(w) - i >= 3 else None
                if rest:
                    return lex[w[:i]] + " " + rest
    if spelled:
        return None
    if (word.isupper() and len(w) <= 5) or not re.search(r"[aeiouy]", w):
        if all(c in lex for c in w):
            return " ".join(lex[c] for c in w)         # said as letters
    out, i, stressed = [], 0, False
    while i < len(w):                                  # letter by letter, plain vowels
        two = w[i:i + 2]
        sound = LETTERS.get(two) if len(two) == 2 and two in LETTERS and len(two) == 2 else None
        if sound and two not in ("ny", "ng") or (sound and i + 2 <= len(w)):
            i += 2
        else:
            sound = LETTERS.get(w[i])
            i += 1
        if not sound:
            continue
        if not stressed and sound.split()[0] in ("ɑ", "ɛ", "i", "O", "u", "I", "A", "W", "Y"):
            out.append("ˈ")
            stressed = True
        out.append(sound)
    ph = " ".join(out)
    return ph if ph and all(t in tokens for t in ph.split()) else None


def extra_lexicon(folder, texts):
    """A second dictionary with the words of `texts` that the model's own one lacks. Returns its path, or None."""
    files = sorted(Path(folder).glob("lexicon-us-en*.txt")) or sorted(Path(folder).glob("lexicon*en*.txt"))
    if not files:
        return None
    wanted = {w for t in texts for w in re.findall(r"[A-Za-z][A-Za-z'’]*", t)}
    lex = {}
    with open(files[0], encoding="utf-8") as f:
        for line in f:
            word, _, ph = line.rstrip("\n").partition(" ")
            lex.setdefault(word.lower(), ph)
    tokens = {line.rsplit(" ", 1)[0] for line in open(Path(folder) / "tokens.txt", encoding="utf-8") if line.strip()}
    lines = []
    for word in sorted(wanted):
        if word.lower() in lex or word.lower().replace("’", "'") in lex:
            continue
        ph = pronounce(word.replace("’", "'"), lex, tokens)
        if ph:
            lines.append(f"{word.lower()} {ph}")
    if not lines:
        return None
    out = Path(tempfile.gettempdir()) / f"kimilm-words-{os.getpid()}.txt"
    out.write_text("\n".join(sorted(set(lines))) + "\n", encoding="utf-8")
    return str(out)


def load_tts(model):
    import sherpa_onnx as so
    family, d = model["family"], model["dir"]
    data_dir = str(Path(d) / "espeak-ng-data") if (Path(d) / "espeak-ng-data").is_dir() else ""
    parts = {}
    if family == "kokoro":
        lexicons = ",".join([str(p) for p in sorted(Path(d).glob("lexicon*.txt"))] + ([model["extra"]] if model.get("extra") else []))
        parts["kokoro"] = so.OfflineTtsKokoroModelConfig(
            model=one(d, "model.onnx", "model*.onnx"), voices=one(d, "voices.bin"), tokens=one(d, "tokens.txt"),
            lexicon=lexicons, data_dir=data_dir, dict_dir=str(Path(d) / "dict") if (Path(d) / "dict").is_dir() else "")
    elif family == "kitten":
        parts["kitten"] = so.OfflineTtsKittenModelConfig(
            model=one(d, "model*.onnx"), voices=one(d, "voices.bin"), tokens=one(d, "tokens.txt"), data_dir=data_dir)
    elif family == "vits":
        parts["vits"] = so.OfflineTtsVitsModelConfig(
            model=one(d, "*.onnx"), lexicon=one(d, "lexicon.txt", optional=True), tokens=one(d, "tokens.txt"),
            data_dir=data_dir)
    elif family == "pocket":
        parts["pocket"] = so.OfflineTtsPocketModelConfig(
            lm_flow=one(d, "lm_flow*.onnx"), lm_main=one(d, "lm_main*.onnx"), encoder=one(d, "encoder*.onnx"),
            decoder=one(d, "decoder*.onnx"), text_conditioner=one(d, "text_conditioner*.onnx"),
            vocab_json=one(d, "vocab.json"), token_scores_json=one(d, "token_scores.json"))
    else:
        raise ValueError(f"Unknown kind of speech model: {family}")
    config = so.OfflineTtsConfig(model=so.OfflineTtsModelConfig(num_threads=threads(), **parts), max_num_sentences=1)
    if not config.validate():
        raise ValueError(f"The files of {Path(d).name} are not complete")
    return so.OfflineTts(config)


def speak(job):
    import numpy as np
    import sherpa_onnx as so
    loaded, refs, pieces, marks, extras = {}, {}, [], [], []
    rate, at = int(job.get("rate") or 0), 0.0
    segments = [s for s in job["segments"] if str(s.get("text") or "").strip()]
    if not segments:
        raise ValueError("There is nothing to say")
    words = sum(len(s["text"].split()) for s in segments) or 1
    spoken = 0
    for i, seg in enumerate(segments):
        key = seg["model"]
        if key not in loaded:
            say(progress=i / len(segments), text="Loading the voice…")
            model = dict(job["models"][key])
            if model["family"] == "kokoro" and not espeak():      # words its dictionary lacks would be silent
                model["extra"] = extra_lexicon(model["dir"], [s["text"] for s in segments if s["model"] == key])
                extras.append(model["extra"])
            loaded[key] = load_tts(model)
        say(progress=spoken / words, text=f"Speaking part {i + 1} of {len(segments)}")
        tts = loaded[key]
        family = job["models"][key]["family"]
        here = len(seg["text"].split())

        def heard(samples, progress, i=i, spoken=spoken, here=here):
            # the voice works through the text sentence by sentence: a sign of life (2.14.0), not a new step
            say(beat=True, progress=(spoken + here * float(progress)) / words,
                text=f"Speaking part {i + 1} of {len(segments)}: {round(100 * float(progress))}%")
            return 1
        speed = float(seg.get("speed") or 1.0)
        if family == "pocket":
            ref = seg.get("ref_audio")
            if not ref:
                raise ValueError("This voice model speaks with a voice from a recording: choose such a voice")
            if ref not in refs:
                refs[ref] = read_audio(ref, 24000)[: 24000 * 30]
            config = so.GenerationConfig()
            config.reference_audio = refs[ref].tolist()
            config.reference_sample_rate = 24000
            config.reference_text = str(seg.get("ref_text") or "")
            config.speed = speed
            audio = tts.generate(seg["text"], config)
        else:
            try:
                audio = tts.generate(seg["text"], sid=int(seg.get("sid") or 0), speed=speed, callback=heard)
            except TypeError:                     # a library version whose voices do not report as they go
                audio = tts.generate(seg["text"], sid=int(seg.get("sid") or 0), speed=speed)
        spoken += here
        samples = np.asarray(audio.samples, dtype="float32")
        if len(samples) == 0:
            raise ValueError(f"The voice gave no sound for: {seg['text'][:80]}")
        rate = rate or int(audio.sample_rate)
        samples = resample(samples, int(audio.sample_rate), rate)
        pause = float(seg.get("pause", 0.35 if i < len(segments) - 1 else 0.0))
        marks.append({"start": round(at, 2), "end": round(at + len(samples) / rate, 2), "text": seg["text"],
                      "speaker": seg.get("speaker") or ""})
        pieces += [samples, np.zeros(int(rate * pause), dtype="float32")]
        at += len(samples) / rate + pause
    for path in extras:
        if path:
            Path(path).unlink(missing_ok=True)
    out = np.concatenate(pieces)
    write_wav(job["out"], out, rate)
    say(done=True, out=job["out"], seconds=round(len(out) / rate, 2), rate=rate, segments=marks)


def transcribe(job):
    import sherpa_onnx as so
    model = job["model"]
    d = model["dir"]
    if model["family"] != "whisper":
        raise ValueError(f"Unknown kind of transcription model: {model['family']}")
    say(progress=0.0, text="Reading the recording…")
    samples = read_audio(job["audio"], 16000)
    total = len(samples) / 16000
    if total < 0.2:
        raise ValueError("The recording is empty")
    say(progress=0.0, text="Loading the transcription model…")
    recognizer = so.OfflineRecognizer.from_whisper(
        encoder=one(d, "*encoder.int8.onnx", "*encoder.onnx"), decoder=one(d, "*decoder.int8.onnx", "*decoder.onnx"),
        tokens=one(d, "*tokens.txt"), language=str(model.get("language") or ""), task=str(job.get("task") or "transcribe"),
        num_threads=threads())
    # Whisper reads at most 30 seconds at a time: the recording is cut where nobody speaks
    vad_config = so.VadModelConfig(silero_vad=so.SileroVadModelConfig(
        model=job["vad"], min_silence_duration=0.4, min_speech_duration=0.25, max_speech_duration=25), sample_rate=16000)
    vad = so.VoiceActivityDetector(vad_config, buffer_size_in_seconds=120)
    window, segments = vad_config.silero_vad.window_size, []

    def drain():
        while not vad.empty():
            seg = vad.front
            # a little sound before and after what was detected, so the first and last words are whole
            a, b = max(0, seg.start - 4000), min(len(samples), seg.start + len(seg.samples) + 3200)
            start = a / 16000
            stream = recognizer.create_stream()
            stream.accept_waveform(16000, samples[a:b])
            recognizer.decode_stream(stream)
            text = stream.result.text.strip()
            if text:
                segments.append({"start": round(start, 2), "end": round(b / 16000, 2), "text": text})
            vad.pop()
            say(progress=min(0.99, start / total), text=f"Transcribing: {int(start)} of {int(total)} seconds")
    for i in range(0, len(samples), window):
        vad.accept_waveform(samples[i:i + window])
        drain()
    vad.flush()
    drain()
    say(done=True, text=" ".join(s["text"] for s in segments), segments=segments, seconds=round(total, 2))


def main(argv):
    job = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    began = time.monotonic()
    try:
        {"speak": speak, "transcribe": transcribe}[job["kind"]](job)
    except Exception as exc:
        say(error=f"{type(exc).__name__}: {exc}"[:600], took=round(time.monotonic() - began, 1))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
