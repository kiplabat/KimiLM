"""Your own voice (2.18.0): recorded sentence by sentence, checked, kept, listened to, changed and used.

Since 2.12.0 a voice could be made from one recording. That worked, but it asked the user to know what a good
recording is, and afterwards the voice was a name in a list: it could not be heard, renamed or improved.

What the voice model really hears (measured for this release, with the model KimiLM installs: Pocket TTS as
published for sherpa-onnx, `sherpa-onnx-pocket-tts-int8-2026-01-26`, library 2.0.1): **about one second**. Its
encoder turns a recording of any length into 13 frames, 1.04 seconds, and they are the recording's first; a
recording of 3 seconds and one of 44 give exactly the same voice. (The library's option for a longer reference,
`max_reference_audio_len`, cuts the recording before the encoder and so cannot add to that.) Which second it
is matters: given a lower-pitched second the voice speaks lower, and different seconds of the same speaker give
audibly different voices. Until now that second was whatever a recording began with, often a breath.

So a voice is now made like this:

  The sentences   Eight short sentences to read aloud (`SCRIPT`), each in a different way: plainly, as a
                  question, with joy, steadily, calmly, with emphasis, softly, clearly. Each gives the voice
                  model a different second of this person to start from.
  A take          One sentence, recorded once. Every take is checked at once (`Voices.check`): is there speech
                  in it, is it loud enough, does it distort, how much noise is around it, is there a full
                  second of unbroken speech, and, when a transcription model is on this computer, were the
                  words of the sentence heard. The answer is "good", "fair" (usable, with a remark) or
                  "again" (with what to change). A take never leaves this computer for a check: an online
                  transcription model is not asked.
  Its best second The 1.04 seconds of a take with the most unbroken speech (`best_second`): what the voice
                  model is given when the voice is taken from this take.
  The lead        The take the voice is taken from. KimiLM proposes one; the user chooses by ear: the voice says
                  the same sentence starting from each take (`Voices.sample(which="all")`), and the one that
                  sounds most like its owner becomes the lead.
  The reference   The file the voice speaks from (`<data>/voices/<id>.wav`): the lead's best second first,
                  then the takes (the model does not hear them; they make the file what Sound expects of a
                  voice's recording, at least five seconds).
  A voice         A name, a note, whose voice it is (the rules of 2.12.0 stand: your own after you confirm it,
                  another person's only with their recorded consent), its takes and its lead. Voices can be
                  renamed, extended, recorded again take by take, and deleted with every recording that belongs
                  to them.

A voice made from one recording (still offered for a file the user already has) has that recording as its
only take, and is taken from its best second instead of its first.

Agents use voices; only the user makes, changes and deletes them: there are routes for it, and no tools."""
import asyncio
import difflib
import re
import shutil
from pathlib import Path

from .db import new_id, now
from .jobs import turn
from .sound import read_pcm, spell_english, write_audio

RATE = 24000                       # what the voice model reads
SECOND = 24960                     # the samples the voice model hears: 13 frames of 1,920 (1.04 s)
GAP = 0.25                         # silence between two parts of the reference

SCRIPT = [
    {"id": "plain", "how": "plainly, as you would tell a friend",
     "text": "Good morning. Today I am recording my own voice."},
    {"id": "question", "how": "as a real question",
     "text": "Did you know the weather can change before lunch?"},
    {"id": "joy", "how": "with joy",
     "text": "What a wonderful surprise! I did not expect you."},
    {"id": "list", "how": "steadily, one thing after the other",
     "text": "I need bread, milk, two oranges and some tea."},
    {"id": "calm", "how": "slowly and calmly",
     "text": "In the evening, I like to sit by the window."},
    {"id": "emphasis", "how": "with weight on what matters",
     "text": "It was not the first answer, but the second."},
    {"id": "soft", "how": "softly, as an ending",
     "text": "Thank you for listening. Take all the time you need."},
    {"id": "sounds", "how": "clearly, every word",
     "text": "The quick brown fox jumps over the lazy dog."},
]
TIPS = ["A quiet room: no music, no fan, windows closed.",
        "The microphone a hand's width from your mouth, a little to the side.",
        "Speak as you usually do. The voice will sound the way you sound here: not faster, not more formal.",
        "Start a moment after you press Record and stop a moment after the last word.",
        "Say each sentence in one breath, without stopping in the middle: the voice is taken from one unbroken second."]
SAMPLE = "This is how I sound when this computer reads with my voice. What do you think of it?"
SENTENCES = {s["id"]: s for s in SCRIPT}
# which take the voice is taken from when the user has not chosen: an even, ordinary way of speaking first
PREFER = ("plain", "calm", "list", "emphasis", "sounds", "soft", "question", "joy")


class VoiceError(ValueError):
    pass


def gone(path):
    """Remove a recording that is no longer needed. A file that is being played at this moment cannot be removed
    on Windows: it is left, and goes with the voice's folder when the voice is deleted."""
    try:
        if path:
            Path(path).unlink(missing_ok=True)
    except OSError:
        pass


def words_of(text):
    return re.findall(r"[a-z0-9']+", str(text or "").lower().replace("’", "'"))


def best_second(voiced, db, frame, rate=RATE, size=SECOND):
    """Where the second with the most unbroken speech starts (in frames), and how much of it is speech (0..1).
    `voiced` and `db` are per frame of `frame` samples."""
    import numpy as np
    span = max(1, round(size / frame))
    n = len(voiced)
    if n <= span:
        return 0, float(voiced.mean()) if n else 0.0
    count = np.convolve(voiced.astype("float32"), np.ones(span, dtype="float32"), "valid")       # speech frames in each window
    loud = np.convolve(np.maximum(db, -80.0), np.ones(span, dtype="float32"), "valid") / span
    score = count + (loud - loud.min()) / max(1e-6, float(loud.max() - loud.min())) * 0.9            # fullest first, then loudest
    start = int(np.argmax(score))
    return start, float(count[start] / span)


def measure(samples, rate=RATE):
    """What can be said about a take from its sound alone. `samples` is mono float32."""
    import numpy as np
    x = np.asarray(samples, dtype="float32").reshape(-1)
    seconds = len(x) / rate
    frame = max(1, rate // 50)                       # 20 ms
    n = len(x) // frame
    if n < 5:
        return {"seconds": round(seconds, 2), "speech_seconds": 0.0, "level_db": -120.0, "noise_db": None, "clipped": 0.0,
                "start": 0.0, "end": round(seconds, 2), "best": 0.0, "steady": 0.0}
    frames = x[: n * frame].reshape(n, frame)
    db = 20 * np.log10(np.sqrt((frames ** 2).mean(axis=1)) + 1e-9)
    top = float(np.percentile(db, 95))
    # the room is what is heard in the quietest moments: before the first word, between words, after the last
    floor = float(np.percentile(db, 5))
    # speech: within 30 dB of the loudest moments, and clearly above the room
    voiced = db > max(top - 30.0, min(floor + 6.0, top - 6.0), -60.0)
    speech = frames[voiced]
    level = float(20 * np.log10(np.sqrt((speech ** 2).mean()) + 1e-9)) if len(speech) else -120.0
    noise = floor if n >= 25 else None               # under half a second there is no telling
    where = np.flatnonzero(voiced)
    start, end = (float(where[0] * frame / rate), float((where[-1] + 1) * frame / rate)) if len(where) else (0.0, seconds)
    at, steady = best_second(voiced, db, frame, rate)
    return {"seconds": round(seconds, 2), "speech_seconds": round(float(voiced.sum()) * frame / rate, 2),
            "level_db": round(level, 1), "noise_db": None if noise is None else round(max(noise, -120.0), 1),
            "clipped": round(float((np.abs(x) >= 0.985).mean()), 5), "start": round(start, 2), "end": round(end, 2),
            "best": round(at * frame / rate, 2), "steady": round(steady, 2)}


def judge(facts, text="", heard=None):
    """The verdict on a take: "good", "fair" (usable, with remarks) or "again" (with what to change)."""
    problems, notes = [], []
    expected = len(words_of(text)) / 2.6 if text else 0.0            # seconds at an ordinary pace
    if facts["speech_seconds"] < 0.8:
        problems.append("Almost nothing was heard. Check that the right microphone is chosen and speak after pressing Record.")
    else:
        if facts["level_db"] < -45:
            problems.append("It is far too quiet. Come closer to the microphone or raise its level.")
        elif facts["level_db"] < -35:
            notes.append("It is rather quiet: a little closer to the microphone would help.")
        if facts["clipped"] > 0.002:
            problems.append("It is too loud and distorts. Move back a little or lower the microphone's level.")
        if facts["noise_db"] is not None:
            clear = facts["level_db"] - facts["noise_db"]
            if clear < 10:
                problems.append("There is a lot of noise around the voice. Find a quieter place or switch off what hums.")
            elif clear < 18:
                notes.append("There is some background noise: the voice will carry a little of it.")
        if facts.get("steady", 1.0) < 0.6:
            notes.append("There is no full second of unbroken speech in it: say the sentence in one breath.")
        if expected and facts["speech_seconds"] < expected * 0.45:
            problems.append("It is much shorter than the sentence: a part of it seems to be missing.")
        elif expected and facts["seconds"] > expected * 2.5 + 4:
            notes.append("There are long silences in it: they are cut away, nothing to do.")
    match = None
    if heard is not None and text and facts["speech_seconds"] >= 0.8:
        match = round(difflib.SequenceMatcher(None, words_of(text), words_of(heard)).ratio(), 2)
        if match < 0.5:
            problems.append("Other words were heard than the sentence" + (f": “{heard[:120]}”" if heard.strip() else "")
                            + ". Read the sentence as it is written.")
        elif match < 0.8:
            notes.append(f"Not every word was understood (heard: “{heard[:120]}”). Fine if you said it as written.")
    return {"verdict": "again" if problems else "fair" if notes else "good", "problems": problems, "notes": notes,
            "match": match}


def cut(samples, facts, rate=RATE, margin=0.12, level=-23.0, lead=False):
    """A take as it goes into a reference, at a common loudness: its speech with a breath of margin, or, with
    `lead`, from its best second on (so that this second is what the voice model hears)."""
    import numpy as np
    x = np.asarray(samples, dtype="float32").reshape(-1)
    a = int(facts.get("best", 0.0) * rate) if lead else max(0, int((facts["start"] - margin) * rate))
    b = min(len(x), int((facts["end"] + margin) * rate))
    x = x[a:b] if b - a >= min(len(x), SECOND) else x[a:]
    if facts["level_db"] > -100:
        gain = 10 ** ((level - facts["level_db"]) / 20)
        peak = float(np.abs(x).max()) if len(x) else 0.0
        if peak * gain > 0.95:                       # never louder than the file can hold
            gain = 0.95 / peak
        x = x * gain
    return x.astype("float32")


class Voices:
    def __init__(self, app):
        self.app, self.db = app, app.db
        self.sampling = {}                           # voice id -> the task that lets it speak

    # ------------------------------------------------------------------ files
    def folder(self, vid=None):
        base = self.app.paths.data / "voices"
        return base / vid if vid else base

    def uploads(self):
        return (self.app.paths.data / "sound" / "uploads").resolve()

    def discard(self, paths):
        """Remove recordings that were tried and not kept. Only files in the app's own folder for recordings:
        a voice is personal, and what was not kept should not stay behind."""
        count, uploads = 0, self.uploads()
        for path in paths or []:
            p = Path(str(path or "x")).resolve()
            if path and p.is_file() and uploads in p.parents:
                p.unlink(missing_ok=True)
                count += 1
        return count

    # ------------------------------------------------------------------ the sentences and the check
    def script(self):
        clone, hear = self.app.sound.default("clone"), self._listener()
        return {"sentences": SCRIPT, "tips": TIPS, "sample": SAMPLE, "heard": round(SECOND / RATE, 2),
                "ready": bool(clone), "model": (clone or {}).get("name"),
                "words_checked": bool(hear), "listener": (hear or {}).get("name"),
                "language": "English: the voice model on this computer speaks English."}

    def _listener(self):
        """The transcription model that may hear a take: one on this computer, never an online one."""
        row = self.app.sound.default("stt")
        return row if row and row["kind"] == "local" and row["ready"] else None

    def _sentence(self, sentence=None, text=None):
        if sentence and sentence in SENTENCES:
            return SENTENCES[sentence]["id"], SENTENCES[sentence]["text"]
        return None, " ".join(str(text or "").split())[:400]

    async def check(self, audio, sentence=None, text=None, words=True):
        """Check one take. Returns its facts and the verdict; nothing is kept."""
        path = Path(str(audio or ""))
        if not path.is_file():
            raise VoiceError("The recording was not found")
        key, said = self._sentence(sentence, text)
        try:
            samples = await asyncio.to_thread(read_pcm, path, RATE, 1)
        except Exception as exc:
            raise VoiceError(f"The recording could not be read ({str(exc)[:120]})") from exc
        facts = await asyncio.to_thread(measure, samples)
        heard = None
        if words and said and facts["speech_seconds"] >= 0.8 and self._listener():
            try:
                heard = (await self.app.sound.transcribe(path, language="en"))["text"]
            except Exception as exc:                 # the check goes on without the words
                self.app.log(f"voice take: the words could not be checked: {type(exc).__name__}: {exc}")
        return {"audio": str(path), "sentence": key, "text": said, **{k: facts[k] for k in (
            "seconds", "speech_seconds", "level_db", "noise_db", "clipped", "best", "steady")}, "heard": heard,
            **judge(facts, said, heard)}

    # ------------------------------------------------------------------ takes, the lead and the reference
    async def _take(self, vid, audio, sentence=None, text=None, checked=None, whole=False):
        """Bring a recording into a voice's folder as a take (24 kHz, one channel) and return its record. The
        recording itself stays where it is until the voice is saved (`discard`)."""
        path = Path(str(audio or ""))
        if not path.is_file():
            raise VoiceError("The recording was not found")
        key, said = self._sentence(sentence, text)
        samples = await asyncio.to_thread(read_pcm, path, RATE, 1)
        facts = await asyncio.to_thread(measure, samples)
        if facts["speech_seconds"] < 0.8:
            raise VoiceError("There is almost no speech in " + (f"the take of “{said[:50]}”" if said else "the recording"))
        seen = checked if checked and checked.get("audio") == str(path) else None
        verdict = {k: seen.get(k) for k in ("verdict", "problems", "notes", "match", "heard")} if seen else \
            {**judge(facts, "" if whole else said), "heard": None}
        tid = new_id("tk_")
        dest = self.folder(vid) / f"{key or 'own'}-{tid[-6:]}.wav"
        await asyncio.to_thread(write_audio, dest, samples[: int(RATE * 120)], RATE)
        return {"id": tid, "sentence": key, "text": said, "path": str(dest), "recorded": now(), "source": str(path),
                "whole": bool(whole), **facts, **verdict}

    @staticmethod
    def _order(takes):
        """Takes in the order of the sentences; takes of the user's own words after them, oldest first."""
        place = {s["id"]: i for i, s in enumerate(SCRIPT)}
        return sorted(takes, key=lambda t: (place.get(t.get("sentence"), len(place)), t.get("recorded") or ""))

    @staticmethod
    def _pick(takes):
        """The take a voice is taken from when the user has not chosen: one that passed the check and has a full
        second of speech, an even way of speaking before a marked one; else the one with the fullest second."""
        fine = [t for t in takes if t.get("verdict") != "again" and t.get("steady", 0) >= 0.8]
        rank = {s: i for i, s in enumerate(PREFER)}
        if fine:
            return sorted(fine, key=lambda t: (rank.get(t.get("sentence"), -1 if t.get("whole") else len(rank)), t.get("recorded") or ""))[0]
        return sorted(takes, key=lambda t: (t.get("verdict") == "again", -t.get("steady", 0)))[0]

    async def _lead_audio(self, take):
        """A take from its best second on: what the voice model is given when the voice is taken from it."""
        return cut(await asyncio.to_thread(read_pcm, take["path"], RATE, 1), take, lead=True)

    async def _rebuild(self, vid, takes, lead=None):
        """Write the reference: the lead's best second first. Returns (lead id, text of the lead)."""
        import numpy as np
        takes = [t for t in takes if Path(t["path"]).is_file()]
        if not takes:
            raise VoiceError("A voice needs at least one recording")
        first = next((t for t in takes if t["id"] == lead), None) or self._pick(takes)
        pieces = [await self._lead_audio(first)]
        at = len(pieces[0]) / RATE
        for take in self._order(takes):              # the model does not hear these: they make the file long enough
            if at >= 12.0:
                break
            part = cut(await asyncio.to_thread(read_pcm, take["path"], RATE, 1), take)
            pieces += [np.zeros(int(RATE * GAP), dtype="float32"), part]
            at += GAP + len(part) / RATE
        audio = np.concatenate(pieces)[: RATE * 30]
        if len(audio) / RATE < 5.2:
            raise VoiceError("The recording is shorter than 5 seconds: a few clear sentences work best" if all(
                t.get("whole") for t in takes) else "That is too little speech for a voice: record another sentence")
        ref = self.folder() / f"{vid}.wav"
        await asyncio.to_thread(write_audio, ref, audio.reshape(-1, 1), RATE)
        return first["id"], (first.get("text") or "")[:1000]

    # ------------------------------------------------------------------ voices
    def _row(self, voice_id):
        vid = str(voice_id).removeprefix("voice:")
        row = self.db.get("voices", vid)
        if not row:
            raise KeyError(voice_id)
        return vid, row

    def _name(self, name, but=None):
        name = " ".join(str(name or "").split())[:60]
        if not name:
            raise VoiceError("Give the voice a name")
        taken = {v["name"].lower() for v in self.app.sound.voices() if v["id"] != f"voice:{but}"}
        if name.lower() in taken:
            raise VoiceError(f"There is a voice called {name!r} already: choose another name, so that it can be asked for by name")
        return name

    def view(self, row, listed=None):
        """A voice as the window gets it."""
        listed = listed if listed is not None else {v["id"]: v for v in self.app.sound.voices()}
        base = listed.get(f"voice:{row['id']}") or {}
        detail, consent = row.get("detail") or {}, row.get("consent") or {}
        takes, tries, lead = self._order(detail.get("takes") or []), detail.get("tries") or {}, detail.get("lead")
        old = not takes and Path(row["ref_audio"]).is_file()           # made before 2.18.0: one recording, as it was given
        if old:
            takes = [{"id": "whole", "sentence": None, "text": row.get("ref_text") or "", "path": row["ref_audio"],
                      "recorded": row.get("created"), "whole": True, "verdict": None, "problems": [], "notes": []}]
            lead = "whole"

        def heard(take):
            t = tries.get(take["id"])
            return t if t and Path(t.get("path", "")).is_file() else None
        have = {t.get("sentence") for t in takes}
        return {"id": f"voice:{row['id']}", "name": row["name"], "kind": "recording", "note": detail.get("note") or "",
                "whose": consent.get("whose"), "person": consent.get("person") or "", "statement": consent.get("statement") or "",
                "created": row.get("created"), "updated": detail.get("updated") or row.get("created"),
                "ready": bool(base.get("ready")), "default": bool(base.get("default")), "model_name": base.get("model_name"),
                "heard": round(SECOND / RATE, 2), "lead": lead, "old": old,
                "reference": row["ref_audio"] if Path(row["ref_audio"]).is_file() else None,
                "takes": [{**{k: v for k, v in t.items() if k != "source"}, "exists": Path(t["path"]).is_file(),
                           "lead": t["id"] == lead, "try": heard(t)} for t in takes],
                "missing": [s["id"] for s in SCRIPT if s["id"] not in have],
                "sample": next((heard(t) for t in takes if t["id"] == lead), None),
                "sampling": row["id"] in self.sampling}

    def mine(self):
        listed = {v["id"]: v for v in self.app.sound.voices()}
        return [self.view(r, listed) for r in self.db.all("SELECT * FROM voices ORDER BY created")]

    def get(self, voice_id):
        return self.view(self._row(voice_id)[1])

    def _save(self, vid, detail, **columns):
        detail["updated"] = now()
        self.db.update("voices", vid, {"detail": detail, **columns})
        self.app.bus.publish("sound_changed")
        return self.get(vid)

    async def create(self, name, consent, audio=None, takes=None, transcript="", note=""):
        """A new voice: from takes of the sentences ([{"audio", "sentence" | "text", "check"}]), or from one
        recording the user has (`audio`). Whose voice it is follows the rules of Sound.create_voice."""
        name = self._name(name)
        proof = (consent or {}).get("consent_audio")
        vid = new_id("vc_")
        made, tmp = [], self.folder() / f"{vid}.wav"
        try:
            if takes:
                for t in takes[:24]:
                    made.append(await self._take(vid, t.get("audio"), t.get("sentence"), t.get("text"), t.get("check")))
            else:
                made.append(await self._take(vid, audio, None, transcript, whole=True))
            lead, text = await self._rebuild(vid, made)
            # the rules about whose voice it is, and the row itself, are Sound's: it is given the reference
            row = await self.app.sound.create_voice(name, str(tmp), text, consent)
        except Exception:
            shutil.rmtree(self.folder(vid), ignore_errors=True)
            tmp.unlink(missing_ok=True)
            raise
        new = row["id"].removeprefix("voice:")
        shutil.move(str(self.folder(vid)), str(self.folder(new)))            # Sound made the voice under an id of its own
        tmp.unlink(missing_ok=True)
        self.discard([t.pop("source", None) for t in made] + [proof])       # the voice is saved: the uploads can go
        for take in made:
            take["path"] = str(self.folder(new) / Path(take["path"]).name)
        return self._save(new, {"note": str(note or "")[:500], "takes": made, "lead": lead, "tries": {}})

    async def _adopt(self, vid, row, detail):
        """The takes of a voice. A voice from before 2.18.0 has none: its one recording becomes its first take."""
        takes = [dict(t) for t in detail.get("takes") or []]
        if takes:
            return takes
        whole = self.folder(vid) / "whole.wav"
        samples = await asyncio.to_thread(read_pcm, row["ref_audio"], RATE, 1)
        await asyncio.to_thread(write_audio, whole, samples, RATE)
        take = {"id": new_id("tk_"), "sentence": None, "text": row.get("ref_text") or "", "path": str(whole),
                "recorded": row.get("created"), "whole": True, "verdict": None, "problems": [], "notes": [], "heard": None,
                "match": None, **(await asyncio.to_thread(measure, samples))}
        detail["lead"] = take["id"]
        return [take]

    async def update(self, voice_id, data):
        """Rename a voice, change its note, choose the take it is taken from ({"lead": take id})."""
        vid, row = self._row(voice_id)
        detail, columns = dict(row.get("detail") or {}), {}
        if "name" in data and " ".join(str(data["name"] or "").split()) != row["name"]:
            columns["name"] = self._name(data["name"], but=vid)
        if "note" in data:
            detail["note"] = str(data["note"] or "")[:500]
        if data.get("lead") and data["lead"] != detail.get("lead"):
            takes = await self._adopt(vid, row, detail)
            # a voice from before 2.18.0 spoke from the first second of its recording: choosing its one take
            # ("whole") takes it from the clearest second instead
            want = detail["lead"] if data["lead"] == "whole" and len(takes) == 1 and takes[0].get("whole") else data["lead"]
            if not any(t["id"] == want for t in takes):
                raise KeyError(data["lead"])
            detail["lead"], columns["ref_text"] = await self._rebuild(vid, takes, want)
            detail["takes"] = takes
            tries = dict(detail.get("tries") or {})
            gone((tries.pop("whole", None) or {}).get("path"))
            detail["tries"] = tries
        return self._save(vid, detail, **columns)

    async def add_take(self, voice_id, audio, sentence=None, text=None, checked=None):
        """Record a sentence for a voice that exists: a new take of a sentence replaces the one before. The voice
        goes on being taken from the take it was taken from, unless that is the take replaced."""
        vid, row = self._row(voice_id)
        detail = dict(row.get("detail") or {})
        takes = await self._adopt(vid, row, detail)
        new = await self._take(vid, audio, sentence, text, checked)
        old = [t for t in takes if new["sentence"] and t.get("sentence") == new["sentence"]]
        takes = [t for t in takes if t not in old] + [new]
        lead = new["id"] if any(t["id"] == detail.get("lead") for t in old) else detail.get("lead")
        try:
            detail["lead"], ref_text = await self._rebuild(vid, takes, lead)
        except VoiceError:
            gone(new["path"])
            raise
        tries = dict(detail.get("tries") or {})
        for t in old:
            gone(t["path"])
            gone((tries.pop(t["id"], None) or {}).get("path"))
        self.discard([new.pop("source", None)])
        detail.update(takes=takes, tries=tries)
        return self._save(vid, detail, ref_text=ref_text)

    async def remove_take(self, voice_id, take_id):
        vid, row = self._row(voice_id)
        detail = dict(row.get("detail") or {})
        takes = [dict(t) for t in detail.get("takes") or []]
        removed = next((t for t in takes if t["id"] == take_id), None)
        if not removed:
            raise KeyError(take_id)
        takes = [t for t in takes if t["id"] != take_id]
        lead = None if detail.get("lead") == take_id else detail.get("lead")
        detail["lead"], ref_text = await self._rebuild(vid, takes, lead)       # refuses when too little would be left
        tries = dict(detail.get("tries") or {})
        gone(removed["path"])
        gone((tries.pop(take_id, None) or {}).get("path"))
        detail.update(takes=takes, tries=tries)
        return self._save(vid, detail, ref_text=ref_text)

    def delete(self, voice_id):
        """Delete a voice with everything recorded for it: the takes, the reference, what it said, the consent."""
        vid, row = self._row(voice_id)
        if self.db.setting("sound_voice", "") == f"voice:{vid}":
            self.db.set_setting("sound_voice", "")
        self.app.sound.delete_voice(vid)
        shutil.rmtree(self.folder(vid), ignore_errors=True)
        if self.folder(vid).exists():                # a file of it was open somewhere: said, not hidden
            self.app.log(f"voice {vid}: some of its recordings could not be removed from {self.folder(vid)}")

    # ------------------------------------------------------------------ hearing the voice
    def sample(self, voice_id, text="", which="lead"):
        """Let the voice say a sentence, to hear what it has become: taken from its lead (`which="lead"`), or
        from every take in turn (`which="all"`), so that the one that sounds most like its owner can be chosen.
        Runs as a task; event `voice_sample` says when it is there. What it says is kept with the voice, not in
        the library."""
        vid, row = self._row(voice_id)
        if vid in self.sampling:
            return {"job_id": self.sampling[vid]}
        clone = self.app.sound.default("clone")
        if not clone or not clone["ready"]:
            raise VoiceError("Download “Pocket TTS” first (Sound → Voices & models): it is the model that speaks with "
                             "a voice from a recording")
        text = " ".join(str(text or "").split())[:400] or SAMPLE

        async def run(job):
            status, track = self.app.jobs.reporter(None, job["id"])
            tmp = self.app.paths.data / "tmp" / new_id("try_")
            tmp.mkdir(parents=True)
            try:
                current = self.db.get("voices", vid)
                detail = dict(current.get("detail") or {})
                takes = [dict(t) for t in detail.get("takes") or []]
                if takes:
                    lead = detail.get("lead") or self._pick(takes)["id"]
                    chosen = [t for t in self._order(takes) if Path(t["path"]).is_file() and (which == "all" or t["id"] == lead)]
                else:                                # from before 2.18.0: it speaks from its recording as it was given
                    chosen = [{"id": "whole", "as_given": current["ref_audio"]}]
                track.stages(["Prepare the voice", "Speak", "Save"])
                track.plan(voice={"name": current["name"], "says": text, "model": clone["name"], "taken_from": [
                    (SENTENCES.get(t.get("sentence")) or {}).get("how") or "the recording" for t in chosen],
                    "what_the_model_hears": f"the clearest {SECOND / RATE:.2f} seconds of each"})
                segments = []
                for t in chosen:
                    ref = Path(t.get("as_given") or tmp / f"{t['id']}.wav")
                    if not t.get("as_given"):
                        await asyncio.to_thread(write_audio, ref, (await self._lead_audio(t)).reshape(-1, 1), RATE)
                    segments.append({"model": clone["id"], "text": spell_english(text), "speaker": t["id"], "speed": 1.0,
                                     "ref_audio": str(ref), "ref_text": "", "pause": 0.3})
                track.at("Speak")
                status(f"The voice speaks, taken from {len(chosen)} take{'s' if len(chosen) != 1 else ''}…" if which == "all"
                       else "The voice speaks…")
                spec = {"kind": "speak", "out": str(tmp / "said.wav"), "segments": segments,
                        "models": {clone["id"]: {"family": clone["family"], "dir": clone["path"]}}}
                async with turn(self.app.sound.lock, status, "Waiting for another sound job on this computer to finish…"):
                    done = await self.app.sound._worker(spec, status, track)
                track.at("Save")
                said = await asyncio.to_thread(read_pcm, done["out"], RATE, 1)
                tries, stamp, files = dict(detail.get("tries") or {}), new_id("")[-8:], []
                for t, mark in zip(chosen, done.get("segments") or []):
                    part = said[int(mark["start"] * RATE): int(mark["end"] * RATE)]
                    gone((tries.get(t["id"]) or {}).get("path"))
                    dest = self.folder(vid) / f"says-{t['id'][-6:]}-{stamp}.wav"       # a new name each time: nothing stale is played
                    await asyncio.to_thread(write_audio, dest, part, RATE)
                    tries[t["id"]] = {"path": str(dest), "text": text, "at": now(), "seconds": round(len(part) / RATE, 1)}
                    files.append(str(dest))
                latest = self.db.get("voices", vid)
                if latest:                           # the voice may have been changed meanwhile: only what it said is added
                    kept = dict(latest.get("detail") or {})
                    there = {t["id"] for t in kept.get("takes") or []} or {"whole"}
                    kept["tries"] = {**(kept.get("tries") or {}), **{k: v for k, v in tries.items() if k in there}}
                    self.db.update("voices", vid, {"detail": kept})
                self.sampling.pop(vid, None)
                self.app.bus.publish("voice_sample", voice=f"voice:{vid}", state="done")
                return {"files": files}
            except BaseException as exc:
                self.sampling.pop(vid, None)
                self.app.bus.publish("voice_sample", voice=f"voice:{vid}", state="failed",
                                     error="Stopped" if isinstance(exc, asyncio.CancelledError) else str(exc)[:300])
                raise
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        job = self.app.jobs.create("sound", f"Hear the voice {row['name']}: {text[:80]}", run, payload={"what": "voice sample"})
        self.sampling[vid] = job["id"]
        return {"job_id": job["id"]}
