"""Picture tools (2.11.0): an agent makes a picture from a description, or changes one that exists.

The pictures are made by the image model chosen in Images (on this computer, or an online one), not by the chat's
own model, so every text model can ask for them, including ones that cannot see. The files go to the folder
`images` of the chat's first folder (or of the chat's own folder), the chat shows them where they were made, and
a model that can see checks them with look_at_image.

Making a picture on this computer changes nothing outside that folder and is not asked about. An online image
model costs money and is sent the prompt (and, for a change, the picture): that asks first, once per chat."""
import math
from pathlib import Path

from . import Tool, ToolDenied, ToolError
from .. import media
from ..images import ImageError

SHAPES = {"square": 1.0, "landscape": 3 / 2, "portrait": 2 / 3, "wide": 16 / 9, "tall": 9 / 16}
ONLINE = Tool("use_online_image_model", "", {}, None, "images", changes=True, label="Make a picture online")


def size_for(row, shape, width, height):
    """Width and height for a shape, keeping the number of pixels the model is good at."""
    s = row.get("settings") or {}
    if width and height:
        return int(width), int(height)
    base_w, base_h = int(s.get("width") or 512), int(s.get("height") or 512)
    ratio = SHAPES.get(str(shape or "").lower())
    if not ratio or ratio == 1.0:
        return base_w, base_h
    side = math.sqrt(base_w * base_h)
    return int(round(side * math.sqrt(ratio) / 16) * 16), int(round(side / math.sqrt(ratio) / 16) * 16)


async def _model(ctx, name, image):
    """The image model for this request. An online one asks first; declined, a model on this computer is used."""
    images = ctx.app.images
    row = images.get(name) if name else images.default_model()
    if name and not row:
        row = next((m for m in images.models() if str(name).lower() in (m["name"].lower(), m["remote_id"].lower())), None)
        if not row:
            raise ToolError(f"There is no image model called {name!r}. Leave `model` out to use the default. "
                            "Installed: " + (", ".join(m["id"] for m in images.models()) or "none"))
    if not row:
        raise ToolError("No image model is set up. Tell the user to open Images and download a model for this "
                        "computer, or to switch on an online one (OpenAI or Google).")
    if row["kind"] != "online":
        return row
    sends = "the prompt and the picture" if image else "the prompt"
    if not image and ctx.app.db.setting("cloud_privacy", "ask") == "auto":
        return row
    cost = (row.get("settings") or {}).get("per_image")
    description = (f"Make this with {row['name']} (online)? It is sent {sends}"
                   + (f", and costs about US${cost:g} per picture" if cost else "") + ".")
    if await ctx.approve(ONLINE, {"model": row["name"], "sends": sends}, description):
        return row
    local = next((m for m in images.models() if m["kind"] == "local" and m["ready"]), None)
    if not local:
        raise ToolDenied("The user declined the online image model, and there is no image model on this computer.")
    ctx.emit("status", text=f"Using {local['name']} on this computer instead.")
    return local


async def _make(ctx, prompt, image=None, mask=None, model=None, shape=None, width=None, height=None, count=1,
                seed=None, negative_prompt="", strength=None):
    source = mask_path = None
    if image:
        source = ctx.resolve(image)
        if not source.is_file() or media.kind_of(source) != "image":
            raise ToolError(f"{source} is not a picture file")
    if mask:
        mask_path = ctx.resolve(mask)
        if not mask_path.is_file():
            raise ToolError(f"No file at {mask_path}")
    row = await _model(ctx, model, source)
    if not source or shape or (width and height):               # a change keeps the picture's own shape
        width, height = size_for(row, shape, width, height)

    def status(text):
        ctx.emit("status", text=text)
        if ctx.job_id:
            ctx.app.jobs.progress(ctx.job_id, text)
    try:
        records = await ctx.app.images.generate(
            prompt, model=row["id"], out_dir=ctx.primary / "images", negative=negative_prompt or "", width=width,
            height=height, seed=seed, count=count, image=source, mask=mask_path, strength=strength,
            chat_id=ctx.chat["id"], job_id=ctx.job_id, on_status=status)
    except ImageError as exc:
        raise ToolError(str(exc)) from exc
    ctx.notes.setdefault("media", []).extend(
        {"id": r["id"], "kind": "image", "path": r["path"], "name": r["name"], "prompt": r["prompt"],
         "model": r["model_name"], "source": r["source"], "params": r["params"]} for r in records)
    p = records[0]["params"]
    facts = [f"{p['width']}x{p['height']}"]
    if p.get("seed") is not None:
        facts.append(f"seed {p['seed']}")
    if p.get("steps"):
        facts.append(f"{p['steps']} steps")
    facts.append(f"{p['seconds']:g} s")
    if p.get("cost"):
        facts.append(f"about US${p['cost']:g}")
    lines = [f"{'Changed' if source else 'Made'} {len(records)} picture{'s' if len(records) != 1 else ''} with "
             f"{row['name']} ({', '.join(facts)}):"]
    lines += [f"- {r['path']}" for r in records]
    lines.append("The user sees them in the chat. If you can see images, check the result with look_at_image; to "
                 "change one, call edit_image with its path. Do not paste the file's contents into your answer.")
    return "\n".join(lines)


async def generate_image(ctx, prompt, shape=None, count=1, negative_prompt="", seed=None, model=None, width=None,
                         height=None):
    return await _make(ctx, prompt, model=model, shape=shape, width=width, height=height, count=count, seed=seed,
                       negative_prompt=negative_prompt)


async def edit_image(ctx, image, prompt, mask=None, strength=None, count=1, seed=None, model=None, negative_prompt=""):
    return await _make(ctx, prompt, image=image, mask=mask, model=model, count=count, seed=seed,
                       negative_prompt=negative_prompt, strength=strength)


S = lambda props, req: {"type": "object", "properties": props, "required": req}  # noqa: E731
COMMON = {
    "count": {"type": "integer", "minimum": 1, "maximum": 4, "description": "How many versions (1 to 4). Default 1."},
    "seed": {"type": "integer", "description": "Repeat a picture exactly by giving the seed it was made with."},
    "negative_prompt": {"type": "string", "description": "What must not appear (not every model uses it)."},
    "model": {"type": "string", "description": "An image model's id. Leave out to use the user's default."},
}
TOOLS = [
    Tool("generate_image",
         "Make a new picture from a description. Describe the subject, the setting, the style (photo, drawing, "
         "logo…), the light and the colours in plain sentences; say exactly any words that must appear in it.",
         S({"prompt": {"type": "string"},
            "shape": {"type": "string", "enum": list(SHAPES), "description": "Default square."}, **COMMON},
           ["prompt"]),
         generate_image, "images", label="Make picture", summary=lambda a: str(a.get("prompt", ""))[:160]),
    Tool("edit_image",
         "Change a picture that exists (one the user attached, or one made earlier): say what should be different. "
         "With `mask` (a black picture of the same size, white where the change may happen) only that part changes.",
         S({"image": {"type": "string", "description": "Path of the picture to change."},
            "prompt": {"type": "string", "description": "What to change, or what the picture should show afterwards."},
            "mask": {"type": "string"},
            "strength": {"type": "number", "minimum": 0.1, "maximum": 1,
                         "description": "How far it may move from the original (0.3 a touch-up, 0.8 a new picture)."},
            **COMMON}, ["image", "prompt"]),
         edit_image, "images", label="Change picture",
         summary=lambda a: f"{Path(str(a.get('image', ''))).name}: {str(a.get('prompt', ''))[:140]}"),
]
