"""Stand-in for kimilm/sound_worker.py in the tests (no speech models needed). It reads the same job file and answers
in the same way, writes real WAV files (a tone per spoken part, its pitch by the speaker), and appends every job
to the file named by KIMILM_SOUND_LOG so the tests can read what the engine asked for.

Marks: a text containing "unpronounceable" fails the way a voice does that cannot read a text; a recording whose
name contains "silence" has no speech in it; a recording whose name starts with "says-" holds the words of its
name ("says-good-morning-1a2b3c.webm" is heard as "good morning": the last part is the upload's own number)."""
import json
import os
import sys
import wave
from pathlib import Path

import numpy as np


def say(**data):
    sys.stdout.write(json.dumps(data) + "\n")
    sys.stdout.flush()


def main(argv):
    job = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    if os.environ.get("KIMILM_SOUND_LOG"):
        with open(os.environ["KIMILM_SOUND_LOG"], "a", encoding="utf-8") as f:
            f.write(json.dumps(job) + "\n")
    if job["kind"] == "speak":
        rate, pieces, marks, at = 24000, [], [], 0.0
        for i, seg in enumerate(job["segments"]):
            say(progress=i / len(job["segments"]), text=f"Speaking part {i + 1} of {len(job['segments'])}")
            if "unpronounceable" in seg["text"]:
                sys.stderr.write("Failed to convert 'x' to token IDs\n")
                say(error="ValueError: The voice gave no sound for: " + seg["text"][:40])
                return 1
            if not Path(job["models"][seg["model"]]["dir"]).is_dir() or (seg.get("ref_audio") and not Path(seg["ref_audio"]).is_file()):
                say(error="FileNotFoundError: the model or the recording is missing")
                return 1
            length = 0.25 * max(1, len(seg["text"].split())) / float(seg.get("speed") or 1.0)
            pitch = 180 + 20 * int(seg.get("sid") or 0) + (300 if seg.get("ref_audio") else 0)
            t = np.arange(int(rate * length)) / rate
            pieces += [0.3 * np.sin(2 * np.pi * pitch * t), np.zeros(int(rate * 0.35))]
            marks.append({"start": round(at, 2), "end": round(at + length, 2), "text": seg["text"], "speaker": seg.get("speaker") or ""})
            at += length + 0.35
        out = (np.concatenate(pieces) * 32767).astype("<i2")
        Path(job["out"]).parent.mkdir(parents=True, exist_ok=True)
        with wave.open(job["out"], "wb") as f:
            f.setnchannels(1)
            f.setsampwidth(2)
            f.setframerate(rate)
            f.writeframes(out.tobytes())
        say(done=True, out=job["out"], seconds=round(len(out) / rate, 2), rate=rate, segments=marks)
        return 0
    if job["kind"] == "transcribe":
        audio = Path(job["audio"])
        if not audio.is_file() or not Path(job["vad"]).is_file():
            say(error="FileNotFoundError: the recording or the speech finder is missing")
            return 1
        say(progress=0.5, text="Transcribing: 1 of 2 seconds")
        if "silence" in audio.name:
            say(done=True, text="", segments=[], seconds=2.0)
            return 0
        if audio.name.startswith("says-"):
            words = " ".join(audio.stem.split("-")[1:-1])
            say(done=True, text=words, segments=[{"start": 0.0, "end": 2.0, "text": words}], seconds=2.0)
            return 0
        lang = job["model"].get("language") or "auto"
        segments = [{"start": 0.0, "end": 1.2, "text": f"These are the words of {audio.stem[:12]}."},
                    {"start": 1.5, "end": 2.4, "text": f"Language: {lang}."}]
        say(done=True, text=" ".join(s["text"] for s in segments), segments=segments, seconds=2.4)
        return 0
    say(error=f"ValueError: unknown job {job['kind']}")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
