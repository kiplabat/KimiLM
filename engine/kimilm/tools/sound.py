"""Sound tools (2.12.0): an agent says a text or a script with several voices, turns a recording into text, asks
for music, and lays recordings over each other (a voice over music).

The voices and models are the ones chosen in Sound; the files go to the folder `sound` of the chat's first folder
(or of the chat's own folder) and the chat plays them where they were made. Speech and transcription on this
computer are not asked about. An online voice or model is sent the text or the recording and costs money: that
asks first, once per chat. Agents use voices; they cannot make a voice from a recording (only the user can)."""
from pathlib import Path

from . import Tool, ToolDenied, ToolError
from ..sound import SoundError

ONLINE = Tool("use_online_sound_model", "", {}, None, "sound", changes=True, label="Use an online sound model")


async def _allow(ctx, model, sends):
    """An online model asks first (unless the privacy setting lets text go online and only text is sent)."""
    if not model or model["kind"] != "online":
        return
    if sends == "the text" and ctx.app.db.setting("cloud_privacy", "ask") == "auto":
        return
    if not await ctx.approve(ONLINE, {"model": model["name"], "sends": sends},
                             f"Use {model['name']} (online)? It is sent {sends}, and it is paid from the online budget."):
        raise ToolDenied("The user declined the online sound model.")


def _status(ctx):
    return ctx.reporter()


def _show(ctx, record):
    ctx.notes.setdefault("media", []).append(
        {"id": record["id"], "kind": "audio", "path": record["path"], "name": record["name"], "prompt": record["prompt"][:600],
         "model": record["model_name"], "source": record["source"], "params": {k: v for k, v in record["params"].items() if k != "segments"}})


async def list_voices(ctx):
    voices = [v for v in ctx.app.sound.voices() if v["ready"]]
    if not voices:
        raise ToolError("No voice is set up. Tell the user to open Sound → Models.")
    lines = [f"- {v['name']} ({', '.join(x for x in (v['language'], v['gender'], 'the user’s recording' if v['kind'] == 'recording' else '', 'online' if v['kind'] == 'online' else '') if x)})"
             + (" [default]" if v["default"] else "") for v in voices[:80]]
    return "Voices (use the name):\n" + "\n".join(lines)


async def speak(ctx, text, voice=None, voices=None, name=None, speed=None):
    sound = ctx.app.sound
    try:
        lines = sound.parse_script(text)
        used = {sound.voice((voices or {}).get(ln.get("speaker") or "") or voice)["model"] for ln in lines} if (voice or voices) \
            else {sound.voice()["model"]}
        for mid in used:
            await _allow(ctx, sound.get(mid), "the text")
        record = await sound.speak(lines, out_dir=ctx.primary / "sound", voice=voice, voices=voices or None,
                                   speed=speed or 1.0, name=name, chat_id=ctx.chat["id"], job_id=ctx.job_id, on_status=_status(ctx))
    except SoundError as exc:
        raise ToolError(str(exc)) from exc
    _show(ctx, record)
    p = record["params"]
    cast = ", ".join(f"{who}: {v}" for who, v in p["voices"].items())
    return (f"Recorded {p['seconds']:g} seconds ({cast}){', about US$%g' % p['cost'] if p.get('cost') else ''}:\n- {record['path']}\n"
            "The user can play it in the chat. Do not read the text out again in your answer.")


async def transcribe_audio(ctx, path, language=None):
    sound = ctx.app.sound
    p = ctx.resolve(path)
    if not p.is_file():
        raise ToolError(f"No file at {p}")
    try:
        await _allow(ctx, sound.default("stt"), "the recording")
        result = await sound.transcribe(p, language=language or "", out_dir=ctx.primary / "sound" / "transcripts",
                                        chat_id=ctx.chat["id"], job_id=ctx.job_id, on_status=_status(ctx))
    except SoundError as exc:
        raise ToolError(str(exc)) from exc
    text = result["text"] or "(no speech was found in the recording)"
    more = f"\n…(the full transcript is in {result['file']})" if len(text) > 6000 else ""
    files = f"\nSaved: {result['file']}" + (f" and {result['subtitles']} (with times)" if result.get("subtitles") else "")
    return f"Transcript of {p.name} ({result['seconds']:g} seconds, {result['model']}):\n{text[:6000]}{more}{files}"


DOCUMENTS = (".pdf", ".docx", ".odt", ".pptx", ".txt", ".md", ".html", ".htm", ".rtf", ".csv")


async def make_podcast(ctx, source=None, text=None, minutes=5, style="conversation", focus=None, music=False):
    from .. import podcast
    sound = ctx.app.sound
    given = [source] if isinstance(source, str) else list(source or [])
    paths = [ctx.resolve(p) for p in given if p]
    if not paths and not (text or "").strip():        # "make a podcast of this": the document attached to the chat
        uploads = sorted((p for p in (ctx.workspace / "uploads").glob("*") if p.suffix.lower() in DOCUMENTS),
                         key=lambda p: p.stat().st_mtime) if (ctx.workspace / "uploads").is_dir() else []
        if not uploads:
            raise ToolError("Give `source` (the document's path) or `text`. No document is attached to this chat.")
        paths = uploads[-1:]
    try:
        await _allow(ctx, sound.get(sound.voice()["model"]), "the script")
        if music:
            await _allow(ctx, sound.default("music"), "a description of the music")
        record = await podcast.make(ctx.app, sources=paths, text=text or "", minutes=minutes or 5, style=style or "conversation",
                                    focus=focus or "", model=ctx.model_id, music=bool(music), out_dir=ctx.primary / "sound",
                                    chat_id=ctx.chat["id"], job_id=ctx.job_id, on_status=_status(ctx))
    except SoundError as exc:
        raise ToolError(str(exc)) from exc
    _show(ctx, record)
    p = record["params"]
    cast = ", ".join(f"{who}: {v}" for who, v in p["voices"].items())
    return (f"The podcast “{p['title']}” is recorded: {p['seconds'] / 60:.1f} minutes, {p['parts']} parts, {p['words']} words "
            f"({cast}).\n- Recording: {record['path']}\n- Script: {p['script']}\nIt is about: {p['summary']}\n"
            "The user can play it in the chat. Tell them it is ready in one or two sentences; do not repeat the script.")


async def generate_music(ctx, prompt, seconds=None, instrumental=True, lyrics=None):
    sound = ctx.app.sound
    try:
        await _allow(ctx, sound.default("music"), "the description")
        record = await sound.music(prompt, out_dir=ctx.primary / "sound", seconds=seconds, instrumental=bool(instrumental),
                                   lyrics=lyrics or "", chat_id=ctx.chat["id"], job_id=ctx.job_id, on_status=_status(ctx))
    except SoundError as exc:
        raise ToolError(str(exc)) from exc
    _show(ctx, record)
    p = record["params"]
    where = f"about US${p['cost']:g}" if p.get("cost") else "on this computer"
    facts = ", ".join(str(x) for x in (p.get("bpm") and f"{p['bpm']} beats a minute", p.get("keyscale")) if x)
    return (f"Made {p['seconds']:g} seconds of music with {record['model_name']} ({where}{', ' + facts if facts else ''}):\n"
            f"- {record['path']}" + (f"\nIts words: {p['words'][:400]}" if p.get("words") else ""))


async def mix_audio(ctx, tracks, name="mix", length="first"):
    if not isinstance(tracks, list) or not tracks:
        raise ToolError("Give `tracks`: a list of {path, volume, start}")
    clean = []
    for t in tracks:
        p = ctx.resolve((t or {}).get("path"))
        if not p.is_file():
            raise ToolError(f"No file at {p}")
        clean.append({"path": str(p), "volume": float(t.get("volume", 1.0) or 1.0), "start": float(t.get("start") or 0),
                      **{k: float(t[k]) for k in ("from", "to", "fade_in", "fade_out") if t.get(k) is not None}})
    try:
        record = await ctx.app.sound.mix(clean, out_dir=ctx.primary / "sound", name=name, length=length, chat_id=ctx.chat["id"])
    except SoundError as exc:
        raise ToolError(str(exc)) from exc
    _show(ctx, record)
    return f"Mixed {len(clean)} recordings into {record['params']['seconds']:g} seconds:\n- {record['path']}"


S = lambda props, req: {"type": "object", "properties": props, "required": req}  # noqa: E731
TOOLS = [
    Tool("speak",
         "Say a text aloud and save the recording (a voiceover, a narration, a podcast). For several speakers write "
         "the text as a script, one line per turn: `Host: Welcome…` then `Guest: Thanks…`; each speaker gets a voice.",
         S({"text": {"type": "string", "description": "What is said, or the script."},
            "voice": {"type": "string", "description": "A voice's name (see list_voices). Leave out for the user's default."},
            "voices": {"type": "object", "description": "For a script: speaker name → voice name, e.g. {\"Host\": \"Heart\", \"Guest\": \"George\"}.",
                       "additionalProperties": {"type": "string"}},
            "name": {"type": "string", "description": "A short name for the file."},
            "speed": {"type": "number", "minimum": 0.5, "maximum": 2}}, ["text"]),
         speak, "sound", label="Speak", summary=lambda a: str(a.get("text", ""))[:160]),
    Tool("make_podcast",
         "Make a podcast episode from a document (PDF, Word, slides, text): KimiLM reads it, writes a conversation "
         "between two hosts about it and records it. Use this whenever the user asks for a podcast, an audio "
         "summary or a discussion of a document or a long text; do not write the script yourself.",
         S({"source": {"type": "string", "description": "Path of the document. Leave out to use the document attached to this chat."},
            "text": {"type": "string", "description": "The material itself, when there is no file."},
            "minutes": {"type": "number", "minimum": 1, "maximum": 30, "description": "Length of the episode. Default 5."},
            "style": {"type": "string", "enum": ["conversation", "interview", "narration"]},
            "focus": {"type": "string", "description": "What the listener cares about most, if the user said so."},
            "music": {"type": "boolean", "description": "Open with a short piece of music (needs a music model)."}}, []),
         make_podcast, "sound", label="Make podcast",
         summary=lambda a: f"Podcast of {Path(str(a.get('source') or 'the attached document')).name}"),
    Tool("list_voices", "The voices that can speak now, with their language.", S({}, []), list_voices, "sound",
         label="List voices", summary=lambda a: "Voices"),
    Tool("transcribe_audio", "Turn a recording (or the sound of a video) into text, with times. Works for meetings, "
         "interviews, voice notes.", S({"path": {"type": "string"},
                                        "language": {"type": "string", "description": "Two-letter code such as en or sw. Leave out to detect it."}},
                                       ["path"]),
         transcribe_audio, "sound", label="Transcribe", summary=lambda a: f"Transcribe {a.get('path')}"),
    Tool("generate_music", "Make a piece of music from a description: style, instruments, mood, tempo, and how long.",
         S({"prompt": {"type": "string"}, "seconds": {"type": "integer", "minimum": 5, "maximum": 240},
            "instrumental": {"type": "boolean", "description": "No singing (default true)."},
            "lyrics": {"type": "string", "description": "Words to sing, with [Verse] and [Chorus] marks."}}, ["prompt"]),
         generate_music, "sound", label="Make music", summary=lambda a: str(a.get("prompt", ""))[:160]),
    Tool("mix_audio", "Lay recordings over each other into one file, e.g. a voice over quiet music. The first track "
         "sets the length unless length is \"longest\".",
         S({"tracks": {"type": "array", "items": S({"path": {"type": "string"},
                                                    "volume": {"type": "number", "description": "1 = as it is; 0.2 for music under a voice."},
                                                    "start": {"type": "number", "description": "Seconds after the beginning."},
                                                    "from": {"type": "number", "description": "Use the recording from this second."},
                                                    "to": {"type": "number", "description": "…up to this second."},
                                                    "fade_in": {"type": "number"}, "fade_out": {"type": "number"}}, ["path"])},
            "name": {"type": "string"}, "length": {"type": "string", "enum": ["first", "longest"]}}, ["tracks"]),
         mix_audio, "sound", label="Mix sound", summary=lambda a: f"Mix {len(a.get('tracks') or [])} recordings"),
]
NEEDS = {"speak": "tts", "list_voices": "tts", "make_podcast": "tts", "transcribe_audio": "stt", "generate_music": "music"}
