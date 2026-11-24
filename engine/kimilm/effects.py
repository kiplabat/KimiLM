"""Changes to a picture that need no model (2.15.0).

"Make the picture glow on its outline", "make it black and white", "resize it to 800 x 600": these are image
operations, not something to redraw. Done in code they take a second instead of half an hour on a processor, the
picture keeps its size and its file format, and nothing else in it changes. An image model is for what code
cannot do: new content.

  find(text)    the effect an instruction asks for, with its settings, or None when the instruction asks for
                more than one of these effects (then a model is needed);
  apply(...)    does it and saves the result beside the others, in the same format as the original.

The outline glow looks for the subject in this order: the edge of what is not transparent (a cut-out); what
differs from a plain background; otherwise the edges inside the picture are made to glow."""
import re
import time
from pathlib import Path

from PIL import Image, ImageChops, ImageEnhance, ImageFilter, ImageOps, ImageStat

COLOURS = {"white": (255, 255, 255), "yellow": (255, 232, 96), "gold": (255, 205, 80), "golden": (255, 205, 80),
           "orange": (255, 150, 50), "red": (255, 60, 60), "pink": (255, 110, 190), "magenta": (255, 60, 220),
           "purple": (170, 90, 255), "violet": (170, 90, 255), "blue": (70, 140, 255), "cyan": (60, 230, 255),
           "turquoise": (60, 220, 200), "green": (70, 240, 110), "lime": (170, 255, 70), "black": (0, 0, 0),
           "warm": (255, 214, 140), "cold": (150, 210, 255), "cool": (150, 210, 255)}
STRONG = re.compile(r"\b(strong|strongly|bright|intense|heavy|bold|a lot|very|much)\b", re.I)
SOFT = re.compile(r"\b(soft|softly|subtle|subtly|faint|slight|slightly|gentle|gently|a bit|a little|light)\b", re.I)
# effect -> the words that ask for it (checked in this order: the first that matches is the effect)
ASKS = [
    ("glow", r"\bglow\w*|\bhalo\b|\baura\b|neon outline|outline of light|light around"),
    ("grayscale", r"black[- ]and[- ]white|black ?& ?white|gr[ae]yscale|monochrome|\bb&w\b|\bbw\b"),
    ("sepia", r"\bsepia\b"),
    ("blur", r"\bblur\w*"),
    ("sharpen", r"\bsharp\w*|\bcrisp\w*"),
    ("brighten", r"\bbright\w*|\blighten\b|\blighter\b"),
    ("darken", r"\bdark\w*"),
    ("contrast", r"\bcontrast\w*"),
    ("saturate", r"\bsaturat\w*|more colou?r\w*|\bvivid\b|\bvibrant\b"),
    ("invert", r"\binvert\w*|\bnegative\b"),
    ("mirror", r"\bmirror\w*|flip\w* (it )?horizontal\w*|horizontal\w* flip"),
    ("flip", r"\bflip\w*|upside[- ]down"),
    ("rotate", r"\brotat\w*|\bturn (it |the \w+ )?(left|right|around|\d+)"),
    ("format", r"\b(convert|save|export|turn)\b.{0,40}\b(png|jpe?g|webp)\b|\bas an? (png|jpe?g|webp)\b"),
    ("resize", r"\bresiz\w*|\brescal\w*|\bscale\b|\bshrink\w*|\benlarg\w*|\bsmaller\b|\blarger\b|\bbigger\b|"
               r"\d+\s*[x×]\s*\d+|half the size|double the size|twice the size|\d+\s*(%|percent)"),
    ("border", r"\bborder\w*|\bframe\b"),
]
NAMES = [name for name, _ in ASKS]
# words that carry no content of their own in an instruction about one of the effects
FILLER = set("""a an and apply around as at be bit by can could deliver effect effects edge edges file for format from
    give it its just keep little make me more less of on only outline outlines out picture pictures image images
    photo photos please px pixel pixels put return same save should size so some that the then this to turn way
    whole with would you add in into is degrees degree wide thick thin thicker thinner width height high colour
    color coloured colored original back result version one new everything else nothing unchanged change changed
    left right around clockwise anticlockwise counterclockwise horizontally vertically upside down
    logo icon drawing artwork graphic pic img all entire white black""".split())
EXT = {"jpeg": ".jpg", "jpg": ".jpg", "png": ".png", "webp": ".webp"}
LABEL = {"glow": "a glow on its outline", "grayscale": "black and white", "sepia": "sepia", "blur": "blurred",
         "sharpen": "sharpened", "brighten": "brighter", "darken": "darker", "contrast": "more contrast",
         "saturate": "stronger colours", "invert": "inverted", "mirror": "mirrored", "flip": "flipped upside down",
         "rotate": "rotated", "format": "another file format", "resize": "another size", "border": "a border"}


class EffectError(ValueError):
    """Something a user can act on."""


def find(text):
    """The effect an instruction asks for: (name, settings), or None. None also when the instruction says more
    than the effect (other things to change, things to add): code cannot do those, a model has to."""
    text = " ".join(str(text or "").split())
    low = text.lower()
    hit = next(((name, m) for name, words in ASKS for m in [re.search(words, low)] if m), None)
    if not hit:
        return None
    name, m = hit
    rest = (low[:m.start()] + " " + low[m.end():])
    for _, words in ASKS:                      # a second effect in the same sentence is not content either
        rest = re.sub(words, " ", rest)
    rest = STRONG.sub(" ", SOFT.sub(" ", rest))
    left = [w for w in re.findall(r"[a-z]{2,}", rest) if w not in FILLER and w not in COLOURS]
    if left:                                   # it names something else ("the background", "a dragon"): not for code
        return None
    s = {}
    colour = next((c for c in COLOURS if re.search(rf"\b{c}\b", low)), None)
    if colour:
        s["colour"] = colour
    s["amount"] = 1.6 if STRONG.search(low) else 0.6 if SOFT.search(low) else 1.0
    if re.search(r"\b(thick|thicker|wide|wider|big|large)\b", low):
        s["reach"] = 1.8
    elif re.search(r"\b(thin|thinner|narrow|fine)\b", low):
        s["reach"] = 0.5
    if name == "resize":
        m2 = re.search(r"(\d{2,5})\s*[x×]\s*(\d{2,5})", low)
        pc = re.search(r"(\d{1,3})\s*(%|percent)", low)
        if m2:
            s["width"], s["height"] = int(m2.group(1)), int(m2.group(2))
        elif pc:
            s["scale"] = int(pc.group(1)) / 100
        elif re.search(r"half|smaller|shrink", low):
            s["scale"] = 0.5
        elif re.search(r"double|twice|larger|bigger|enlarg", low):
            s["scale"] = 2.0
        else:
            return None
    if name == "rotate":
        deg = re.search(r"(-?\d{1,3})\s*(degrees?|°)?", low)
        s["degrees"] = int(deg.group(1)) if deg else 90
        if re.search(r"\b(right|clockwise)\b", low) and "anti" not in low and "counter" not in low:
            s["degrees"] = -abs(s["degrees"])
        if "around" in low and not deg:
            s["degrees"] = 180
    if name == "format":
        s["format"] = re.search(r"\b(png|jpe?g|webp)\b", low).group(1)
    return name, s


def _plain_background(rgb):
    """The colour of a plain background, or None: nine in ten pixels of the picture's rim are close to one colour."""
    small = rgb.copy()
    small.thumbnail((200, 200))
    w, h = small.size
    px = small.load()
    rim = [px[x, y] for x in range(w) for y in (0, h - 1)] + [px[x, y] for y in range(h) for x in (0, w - 1)]
    mid = tuple(sorted(c[i] for c in rim)[len(rim) // 2] for i in range(3))
    near = sum(max(abs(c[i] - mid[i]) for i in range(3)) <= 26 for c in rim)
    return mid if near >= len(rim) * 0.9 else None


def _subject(img):
    """A mask of the picture's subject (white where it is) and how it was found, or (None, why)."""
    rgba = img.convert("RGBA")
    alpha = rgba.getchannel("A")
    if alpha.getextrema()[0] < 250:
        return alpha.point(lambda v: 255 if v > 40 else 0), "the edge of the cut-out"
    rgb = rgba.convert("RGB")
    bg = _plain_background(rgb)
    if bg is None:
        return None, "no plain background"
    diff = ImageChops.difference(rgb, Image.new("RGB", rgb.size, bg)).convert("L")
    mask = diff.point(lambda v: 255 if v > 30 else 0).filter(ImageFilter.MedianFilter(3))
    covered = ImageStat.Stat(mask).mean[0] / 255
    if covered < 0.01 or covered > 0.97:
        return None, "no clear subject"
    return mask, "what stands out from the plain background"


def glow(img, colour="warm", amount=1.0, reach=1.0):
    """A glow on the outline. Returns (picture, how the outline was found)."""
    rgb_colour = COLOURS.get(colour, COLOURS["warm"]) if isinstance(colour, str) else tuple(colour)
    w, h = img.size
    size = max(3.0, min(w, h) * 0.022 * reach)
    rgba = img.convert("RGBA")
    mask, how = _subject(img)
    if mask is None:
        # no subject to go around: the edges inside the picture are what glows
        edges = ImageOps.autocontrast(rgba.convert("L").filter(ImageFilter.FIND_EDGES))
        edges = edges.point(lambda v: 255 if v > 70 else 0).filter(ImageFilter.GaussianBlur(size / 3))
        edges = edges.point(lambda v: min(255, int(v * 2.2 * amount)))
        light = Image.merge("RGB", [edges.point(lambda v, c=c: v * c // 255) for c in rgb_colour])
        out = ImageChops.screen(rgba.convert("RGB"), light)
        return out, "the edges inside the picture (it has no plain background and no cut-out to go around)"
    # the halo: the subject's shape, grown and softened, shown only outside the subject
    grown = mask.filter(ImageFilter.GaussianBlur(size / 2)).point(lambda v: 255 if v > 12 else 0)
    halo = grown.filter(ImageFilter.GaussianBlur(size)).point(lambda v: min(255, int(v * 1.5 * amount)))
    rim = mask.filter(ImageFilter.GaussianBlur(max(1.0, size / 6))).point(lambda v: min(255, int(v * 1.2 * amount)))
    halo = ImageChops.lighter(halo, rim)
    outside = ImageOps.invert(mask.filter(ImageFilter.GaussianBlur(1)))
    layer = Image.new("RGBA", img.size, rgb_colour + (0,))
    if rgba.getchannel("A").getextrema()[0] < 250:        # a cut-out: the glow goes behind it, on what was empty
        layer.putalpha(halo)
        return Image.alpha_composite(layer, rgba), how
    layer.putalpha(ImageChops.multiply(halo, outside))
    return Image.alpha_composite(rgba, layer), how


def _do(img, name, s):
    amount = float(s.get("amount") or 1.0)
    note = ""
    if name == "glow":
        img, note = glow(img, s.get("colour") or "warm", amount, float(s.get("reach") or 1.0))
    elif name == "grayscale":
        alpha = img.getchannel("A") if img.mode == "RGBA" else None
        img = ImageOps.grayscale(img.convert("RGB")).convert("RGB")
        if alpha is not None:
            img.putalpha(alpha)
    elif name == "sepia":
        img = ImageOps.colorize(ImageOps.grayscale(img.convert("RGB")), (40, 26, 13), (255, 240, 210))
    elif name == "blur":
        img = img.filter(ImageFilter.GaussianBlur(max(1.0, min(img.size) * 0.008 * amount * float(s.get("reach") or 1))))
    elif name == "sharpen":
        img = img.filter(ImageFilter.UnsharpMask(radius=2, percent=int(140 * amount), threshold=2))
    elif name in ("brighten", "darken"):
        img = ImageEnhance.Brightness(img).enhance(1 + 0.25 * amount if name == "brighten" else 1 / (1 + 0.3 * amount))
    elif name == "contrast":
        img = ImageEnhance.Contrast(img).enhance(1 + 0.3 * amount)
    elif name == "saturate":
        img = ImageEnhance.Color(img).enhance(1 + 0.4 * amount)
    elif name == "invert":
        alpha = img.getchannel("A") if img.mode == "RGBA" else None
        img = ImageOps.invert(img.convert("RGB"))
        if alpha is not None:
            img.putalpha(alpha)
    elif name == "mirror":
        img = ImageOps.mirror(img)
    elif name == "flip":
        img = ImageOps.flip(img)
    elif name == "rotate":
        img = img.rotate(float(s.get("degrees") or 90), expand=True, resample=Image.BICUBIC)
    elif name == "resize":
        if s.get("width") and s.get("height"):
            size = (int(s["width"]), int(s["height"]))
        else:
            k = float(s.get("scale") or 1.0)
            size = (max(1, round(img.width * k)), max(1, round(img.height * k)))
        if max(size) > 12000:
            raise EffectError("That is larger than 12,000 pixels on a side.")
        img = img.resize(size, Image.LANCZOS)
    elif name == "border":
        width = max(2, round(min(img.size) * 0.025 * float(s.get("reach") or 1.0)))
        img = ImageOps.expand(img.convert("RGB"), border=width, fill=COLOURS.get(s.get("colour") or "white", (255, 255, 255)))
    elif name != "format":
        raise EffectError(f"There is no effect called {name!r}. There are: {', '.join(NAMES)}.")
    return img, note


def apply(image, out_dir, name, settings=None):
    """Do one effect on a picture file and save the result in `out_dir`, in the original's file format (or the
    one asked for) and, except for a resize or a rotation, its size. Returns (path, facts)."""
    src = Path(image)
    s = dict(settings or {})
    began = time.monotonic()
    with Image.open(src) as opened:
        opened.load()
        img = ImageOps.exif_transpose(opened)
    before = img.size
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA" if "A" in img.getbands() or img.info.get("transparency") is not None else "RGB")
    img, note = _do(img, name, s)
    ext = EXT.get(str(s.get("format") or "").lower()) or (src.suffix.lower() if src.suffix.lower() in
                                                          (".png", ".jpg", ".jpeg", ".webp") else ".png")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^A-Za-z0-9_-]+", "-", src.stem).strip("-")[:40] or "picture"
    path = out_dir / f"{stem}-{name}-{time.strftime('%Y%m%d-%H%M%S')}{ext}"
    n = 2
    while path.exists():
        path = path.with_name(f"{path.stem}-{n}{ext}")
        n += 1
    if ext in (".jpg", ".jpeg"):
        if img.mode == "RGBA":                 # this format has no transparency: on white
            flat = Image.new("RGB", img.size, (255, 255, 255))
            flat.paste(img, mask=img.getchannel("A"))
            img = flat
        img.convert("RGB").save(path, quality=95)
    else:
        img.save(path)
    return path, {"effect": name, "what": LABEL.get(name, name), "how": note, "width": img.width, "height": img.height,
                  "was": f"{before[0]} × {before[1]}", "format": ext.lstrip("."),
                  "seconds": round(time.monotonic() - began, 2)}
