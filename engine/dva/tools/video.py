"""Video tools (2.14.0): an agent builds a video from scenes (pictures, narration, captions, music), changes an
existing video (cut, join, other sound, subtitles), and asks a video model for a short clip.

The files go to the folder `video` of the chat's first folder (or of the chat's own folder) and the chat plays them
where they were made. Everything made on this computer is not asked about. An online voice, image model or video
model is sent the text or the picture and costs money: that asks first."""
from pathlib import Path

from . import Tool, ToolDenied, ToolError
from ..images import ImageError
from ..sound import SoundError
from ..video import SHAPES, VideoError

ONLINE = Tool("use_online_video_model", "", {}, None, "video", changes=True, label="Make a clip online")
ERRORS = (VideoError, SoundError, ImageError, ValueError)


def _show(ctx, record):
    ctx.notes.setdefault("media", []).append(
        {"id": record["id"], "kind": "video", "path": record["path"], "name": record["name"], "prompt": record["prompt"][:600],
         "model": record["model_name"], "source": record["source"],
         "params": {k: v for k, v in record["params"].items() if k not in ("scenes", "from")}})


def _where(ctx, path):
    try:
        return str(Path(path).relative_to(ctx.primary))
    except ValueError:
        return str(path)


async def make_video(ctx, scenes, title=None, shape="landscape", voice=None, music=None, captions=True):
    video = ctx.app.video
    if not isinstance(scenes, list) or not scenes:
        raise ToolError("Give `scenes`: a list of {narration, picture} (and optionally image, caption, seconds).")
    ready = []
    for s in scenes:
        s = dict(s) if isinstance(s, dict) else {"narration": str(s)}
        if s.get("image"):
            s["image"] = str(ctx.resolve(s["image"]))
        ready.append(s)
    tune = music
    if music and (ctx.primary / str(music)).is_file() or (music and Path(str(music)).is_file()):
        tune = str(ctx.resolve(music))
    can = video.can()
    from . import images as image_tools, sound as sound_tools
    try:
        if can["voice"] and any(str(s.get("narration") or "").strip() for s in ready):
            await sound_tools._allow(ctx, ctx.app.sound.get(ctx.app.sound.voice(voice)["model"]), "the narration")
        if can["pictures"] and any(str(s.get("picture") or "").strip() and not s.get("image") for s in ready):
            row = ctx.app.images.default_model()
            if row and row["kind"] == "online" and not await ctx.approve(
                    image_tools.ONLINE, {"model": row["name"]},
                    f"Make the scenes' pictures with {row['name']} (online)? It is sent each scene's description, and it is "
                    "paid from the online budget."):
                raise ToolDenied("The user declined the online image model.")
        record = await video.make(ready, out_dir=ctx.primary / "video", title=title or "", shape=shape if shape in SHAPES else "landscape",
                                  voice=voice or None, music=tune or None, captions=captions is not False,
                                  chat_id=ctx.chat["id"], job_id=ctx.job_id, on_status=ctx.reporter())
    except ERRORS as exc:
        raise ToolError(str(exc)) from exc
    _show(ctx, record)
    p = record["params"]
    return (f"Made the video {_where(ctx, record['path'])}: {p['seconds']:g} seconds, {len(p['scenes'])} scenes, "
            f"{p['width']} × {p['height']}" + (", narrated" if p.get("voices") else ", without a voice")
            + (", with music" if p.get("music") else "") + (", with captions" if p.get("captions") else "")
            + ". The chat shows it; do not describe how it looks.")


async def edit_video(ctx, source=None, clips=None, start=None, end=None, mute=False, sound=None, sound_volume=None,
                     subtitles=None, shape=None, name=None):
    video = ctx.app.video
    parts = None
    if clips:
        parts = [{**c, "path": str(ctx.resolve(c["path"]))} if isinstance(c, dict) else {"path": str(ctx.resolve(c))} for c in clips]
    if not parts and not source:
        raise ToolError("Give `source` (the video's path), or `clips` to join several.")
    subs = subtitles
    if subtitles and subtitles != "auto":
        subs = str(ctx.resolve(subtitles))
    try:
        record = await video.edit(str(ctx.resolve(source)) if source and not parts else None, clips=parts,
                                  out_dir=ctx.primary / "video", start=start, end=end, mute=bool(mute),
                                  sound=str(ctx.resolve(sound)) if sound else None, sound_volume=sound_volume, subtitles=subs or None,
                                  shape=shape if shape in SHAPES else None, name=name or None, chat_id=ctx.chat["id"],
                                  job_id=ctx.job_id, on_status=ctx.reporter())
    except ERRORS as exc:
        raise ToolError(str(exc)) from exc
    _show(ctx, record)
    p = record["params"]
    return (f"Saved {_where(ctx, record['path'])}: {p['seconds']:g} seconds, {p['width']} × {p['height']}, sound: {p['sound']}"
            + (", with subtitles (also as a .srt file beside it)" if p.get("captions") else "") + ". The chat shows it.")


async def generate_video_clip(ctx, prompt, image=None, shape="landscape", model=None):
    video = ctx.app.video
    local = [m for m in video.models() if m["ready"]]
    online = (model == "google") or (not model and not local)
    try:
        if online:
            info = video.online()
            if not info["ready"]:
                raise ToolError("No video model is set up (Video → Models). A video can still be built from pictures, "
                                "narration and music with make_video, which needs no video model.")
            if not await ctx.approve(ONLINE, {"model": info["model"], "sends": "the description" + (" and the picture" if image else "")},
                                     f"Make this clip with Google ({info['model']}, online)? It is sent the description"
                                     + (" and the picture" if image else "") + f", and it is counted as ${info['cost']:.2f} of the online budget."):
                raise ToolDenied("The user declined the online video model.")
        record = await video.clip(prompt, out_dir=ctx.primary / "video", model="google" if online else model or None,
                                  image=str(ctx.resolve(image)) if image else None, shape=shape if shape in SHAPES else "landscape",
                                  chat_id=ctx.chat["id"], job_id=ctx.job_id, on_status=ctx.reporter())
    except ERRORS as exc:
        raise ToolError(str(exc)) from exc
    _show(ctx, record)
    p = record["params"]
    return (f"Made the clip {_where(ctx, record['path'])}: {p['seconds']:g} seconds, {p['width']} × {p['height']}, by "
            f"{record['model_name']}. The chat shows it; do not describe how it looks.")


S = lambda props, req: {"type": "object", "properties": props, "required": req}  # noqa: E731
SHAPE = {"type": "string", "enum": list(SHAPES), "description": "landscape (16:9, the default), portrait (9:16) or square."}
TOOLS = [
    Tool("make_video",
         "Build a video on this computer from scenes: each scene is a picture with a narration spoken over it, a slow "
         "movement, and captions in step with the voice; music can play underneath. Use it for explainers, stories, "
         "summaries of a document, slideshows and social clips. You write the scenes: 4 to 12 of them, each narration one "
         "or two short spoken sentences (about 20 words, 8 seconds), each picture one sentence describing what is seen "
         "(subject, setting, light, style; no text in the picture). It works without a video model; it uses the image "
         "model and the voices that are set up, and says what was missing.",
         S({"scenes": {"type": "array", "items": S({
                "narration": {"type": "string", "description": "What the voice says in this scene."},
                "picture": {"type": "string", "description": "What the scene shows, for the image model."},
                "image": {"type": "string", "description": "A picture file to use as it is instead of making one."},
                "caption": {"type": "string", "description": "A few words shown when the scene has no narration."},
                "seconds": {"type": "number", "description": "Length of a scene without narration."}}, [])},
            "title": {"type": "string"}, "shape": SHAPE,
            "voice": {"type": "string", "description": "A voice's name (see list_voices). Default: the default voice."},
            "music": {"type": "string", "description": "A description of the music to make, or a recording's path. Leave out for none."},
            "captions": {"type": "boolean", "description": "Show the narration as captions (default true)."}}, ["scenes"]),
         make_video, "video", label="Make video",
         summary=lambda a: f"{a.get('title') or 'Video'}: {len(a.get('scenes') or [])} scenes"),
    Tool("edit_video",
         "Change a video file: keep only a part (start and end in seconds), join several (clips), take its sound away "
         "(mute), lay a recording over or under it (sound: a narration or music; sound_volume 1 = as it is, 0.2 under "
         "speech), and burn subtitles in (subtitles: \"auto\" writes them from what is said in the video, or a .srt "
         "file). The result is a new .mp4; the original is not changed.",
         S({"source": {"type": "string", "description": "The video's path."},
            "clips": {"type": "array", "description": "To join several: paths, or {path, start, end}.",
                      "items": {"type": ["string", "object"]}},
            "start": {"type": "number"}, "end": {"type": "number"}, "mute": {"type": "boolean"},
            "sound": {"type": "string", "description": "A recording to add (path)."}, "sound_volume": {"type": "number"},
            "subtitles": {"type": "string", "description": "\"auto\" or a .srt file's path."},
            "shape": SHAPE, "name": {"type": "string"}}, []),
         edit_video, "video", label="Change video",
         summary=lambda a: str(a.get("source") or f"join {len(a.get('clips') or [])} clips")[:160]),
    Tool("generate_video_clip",
         "Ask a video model for one short clip (a few seconds) from a description: what is seen, how it moves, the "
         "light, the style. It is slow on this computer and costs money online; for anything longer than a few seconds "
         "or with a narration, use make_video instead. Only there when a video model is set up.",
         S({"prompt": {"type": "string"},
            "image": {"type": "string", "description": "A picture to start from (online model only)."},
            "shape": SHAPE, "model": {"type": "string", "description": "A video model's id on this computer, or \"google\"."}}, ["prompt"]),
         generate_video_clip, "video", label="Make clip", summary=lambda a: str(a.get("prompt", ""))[:160]),
]
