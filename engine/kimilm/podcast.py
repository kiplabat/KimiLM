"""A podcast from documents, in one request (2.13.0).

The user gives a document (a PDF, a Word file, slides, a text, a web page saved as a file) or a pasted text, and
gets a recording of two people talking about it. Nothing has to be set up beyond a text model and a voice:

  1. Read      the documents' text is taken out (the same reader as the knowledge base) and cut into pieces.
  2. Notes     a long document is read piece by piece: the model writes down the facts, numbers and claims of
               each piece (a short one is used as it is).
  3. Outline   from the notes the model plans the episode: a title, and segments that fit the length asked for.
  4. Script    each segment is written as a conversation, with the pieces of the document that belong to it put
               in front of the model again (found by the words the segment and the pieces share), so that what
               is said comes from the document and not from memory.
  5. Sound     people do not talk like a text: the script is asked to hesitate now and then ("Hmm", "Um", "Right"),
               to react briefly and to pause to think ("..."); if the model wrote none of that, a little is added
               here. Hesitations are spelled the way the voices can say them, a pause in a turn becomes silence,
               and the silence between turns varies: short before a quick reaction, long where a new part begins.
  6. Record    the script is spoken with two voices that sound different (kimilm/sound.py), optionally with a short
               piece of music to open it, and saved with the script beside it.

The text model is the chat's own when an agent asks (tool make_podcast), else the one the router picks. Every step
is a small request with a fixed answer shape, so that a small model on a laptop can do it."""
import asyncio
import math
import re
import time
from pathlib import Path

from .sound import SoundError, slug

WORDS_A_MINUTE = 150
STYLES = {
    "conversation": "two co-hosts who both read the document: {a} leads and explains, {b} asks what a curious listener "
                    "would ask, reacts, and sums up",
    "interview": "an interview: {a} is the host who asks, {b} is the expert who knows the document and answers",
    "narration": "one narrator, {a}, who tells the listener what the document says",
}
STOP = set("the a an and or of to in on for with by is are was were be been it its this that these those as at from "
           "we you they he she i not no but if then so than which who what when where how why can will would should "
           "have has had do does did about into over under more most less very also their our your his her".split())


def words(text):
    return [w for w in re.findall(r"[a-z0-9]{3,}", text.lower()) if w not in STOP]


def pieces(text, size=4200):
    """The text in pieces of about `size` characters, cut at page marks and paragraphs; each with its page."""
    out, page, current = [], None, ""
    for block in re.split(r"\n+|(?=--- page \d+)", text):
        mark = re.match(r"--- page (\d+)", block.strip())
        block_page = int(mark.group(1)) if mark else page
        block = re.sub(r"--- page \d+ ---?", "", block).strip()
        if mark:
            page = block_page
        if not block:
            continue
        while len(block) > size:                       # a paragraph longer than a piece
            cut = block.rfind(". ", 0, size) + 1 or size
            out.append({"text": (current + "\n" + block[:cut]).strip(), "page": page})
            current, block = "", block[cut:].strip()
        if len(current) + len(block) > size and current:
            out.append({"text": current.strip(), "page": page})
            current = ""
        current += "\n" + block
    if current.strip():
        out.append({"text": current.strip(), "page": page})
    return out


def nearest(query, chunks, k=2):
    """The k pieces that share the most (rare) words with the query."""
    q = set(words(query))
    if not q or not chunks:
        return chunks[:k]
    seen = {}
    for c in chunks:
        for w in set(words(c["text"])):
            seen[w] = seen.get(w, 0) + 1
    def score(c):
        have = set(words(c["text"]))
        return sum(math.log(1 + len(chunks) / seen[w]) for w in q & have)
    scores = [score(c) for c in chunks]
    ranked = [i for i in sorted(range(len(chunks)), key=lambda i: -scores[i])[:k] if scores[i] > 0]
    return [chunks[i] for i in sorted(ranked)] or chunks[:k]          # in the document's order


# hesitations as the voices on this computer can pronounce them (their dictionary has um, uh, hmm, mm, mhm, oh, ah…)
FILLERS = {"uhm": "um", "uhmm": "um", "erm": "um", "ehm": "um", "em": "um", "uhh": "uh", "uhhh": "uh", "ummm": "umm",
           "mm-hmm": "mhm", "mmhmm": "mhm", "mhmm": "mhm", "mm-hm": "mhm", "mmm": "mm", "hmmmm": "hmm", "hmmmmm": "hmm"}
STARTS = re.compile(r"^(hmm+|hm|um+|uh|oh|ah|mm|mhm|well|right|so|yeah|okay|ok|wow|huh|aha|ooh|uh-huh|exactly|really)\b", re.I)
PAUSE = re.compile(r"\s*(?:\.{3,}|…|—\s*—|\[(?:pause|beat|silence)[^\]]*\]|\((?:pause|beat|silence)[^)]*\))\s*", re.I)
STAGE = re.compile(r"[\[(]\s*(?:laugh|chuckl|sigh|giggl|music|applause|clears|smil|nod)[^\])]*[\])]", re.I)


def parts_of(text):
    """What is said in a turn, as the pieces between its pauses: stage directions gone, marks that cannot be
    spoken gone, hesitations spelled so that they are pronounced."""
    out = []
    for piece in PAUSE.split(STAGE.sub(" ", str(text or ""))):
        piece = re.sub(r"\s+", " ", re.sub(r"[*_#\[\]]", "", piece)).strip(" ,;")
        piece = re.sub(r"[A-Za-z]+(?:-[A-Za-z]+)?", lambda m: (
            FILLERS[m.group(0).lower()].capitalize() if m.group(0)[0].isupper() else FILLERS[m.group(0).lower()])
            if m.group(0).lower() in FILLERS else m.group(0), piece)
        piece = re.sub(r"\s+([,.!?])", r"\1", piece)
        if re.search(r"\w", piece):
            out.append(piece)
    return out


def natural(lines, lead="A"):
    """Make a script sound like talk when the model wrote it too cleanly: if hardly any turn starts with a small
    word, the other host sometimes thinks first ("Hmm…", "Right."), and the leading host sometimes starts with
    "So," or "Well,". Nothing is changed in what is said."""
    if len(lines) < 4 or sum(bool(STARTS.match(ln["parts"][0])) for ln in lines) * 5 >= len(lines):
        return lines
    plain = {"the", "a", "an", "this", "that", "these", "those", "it", "in", "on", "for", "when", "what", "we", "they", "there",
             "one", "most", "many", "some", "after", "before", "each", "every", "our", "their", "if", "as", "at", "by", "from",
             "with", "to", "you", "now", "here"}
    lower = lambda t: t[0].lower() + t[1:] if t.split()[0].lower() in plain else t        # noqa: E731
    agree, begin, r, b = ("Right.", "Mm.", "Yeah,"), ("So,", "Well,"), 0, 0
    for i, ln in enumerate(lines):
        first = ln["parts"][0]
        if i < 2 or STARTS.match(first) or len(first.split()) < 4:
            continue                                   # not the greeting, not a quick reaction, not twice
        whole = " ".join(ln["parts"])
        if ln["speaker"] != lead and i % 3 == 1:
            if "?" in whole or re.search(r"\b(but|though|however)\b", whole, re.I):
                ln["parts"] = ["Hmm"] + ln["parts"]    # thinks, then speaks
            else:
                word = agree[r % 3]
                ln["parts"][0] = word + " " + (first if word.endswith(".") else lower(first))
                r += 1
        elif ln["speaker"] == lead and i % 6 == 4 and len(first.split()) > 6 and \
                first.split()[0].lower() not in ("and", "but", "or", "yes", "no", "because", "then", "also"):
            ln["parts"][0] = begin[b % 2] + " " + lower(first)
            b += 1
    return lines


def timed(lines, names):
    """The lines to speak, each piece with the silence after it: a thinking pause inside a turn, a short gap
    before or after a quick reaction, a long one where a new part of the episode begins."""
    out, gaps = [], (0.32, 0.46, 0.38)
    for i, ln in enumerate(lines):
        nxt = lines[i + 1] if i + 1 < len(lines) else None
        quick = len(" ".join(ln["parts"]).split()) <= 3 or (nxt and len(" ".join(nxt["parts"]).split()) <= 3)
        after = 0.0 if not nxt else 0.9 if nxt.get("new_part") else 0.18 if quick else gaps[i % 3]
        for k, piece in enumerate(ln["parts"]):
            out.append({"speaker": names[ln["speaker"]], "text": piece,
                        "pause": (0.6 if len(piece.split()) <= 2 else 0.45) if k < len(ln["parts"]) - 1 else after})
    return out


def script_markdown(title, summary, sources, lines, names):
    out = [f"# {title}", "", summary, "", "Made from: " + ", ".join(sources), ""]
    out += [f"**{names.get(ln['speaker'], ln['speaker'])}:** {ln['text']}\n" for ln in lines]
    return "\n".join(out)


async def make(app, *, sources=(), text="", minutes=5, style="conversation", hosts=None, voices=None, focus="",
               model=None, music=False, out_dir, chat_id=None, job_id=None, on_status=None):
    """Returns the recording's record (see Sound._record), with params: title, summary, script (path), lines."""
    from .knowledge import extract_text
    status, track = app.jobs.reporter(on_status, job_id)
    sound = app.sound
    if not sound.can()["tts"]:
        raise SoundError("No voice is set up yet. Open Sound → Voices & models and download Kokoro (or press "
                         "“Get ready” in Sound → Podcast).")
    style = style if style in STYLES else "conversation"
    minutes = max(1, min(30, float(minutes or 5)))
    a, b = (list(hosts or []) + ["Alex", "Sam"])[:2] if len(hosts or []) < 2 else list(hosts)[:2]
    if style == "narration":
        b = a
    track.stages(["Read the document", "Take notes", "Plan the episode", "Write the script", "Record the voices"]
                 + (["Opening music"] if music else []))
    # 1. read
    track.at("Read the document")
    status("Reading the document…")
    docs, names = [], []
    for path in sources:
        path = Path(path)
        if not path.is_file():
            raise SoundError(f"There is no file at {path}")
        try:
            docs.append(await asyncio.to_thread(extract_text, path))
        except ValueError as exc:
            raise SoundError(f"{path.name}: {exc}") from exc
        names.append(path.name)
    if text and text.strip():
        docs.append(text.strip())
        names.append("the text you gave")
    full = re.sub(r"[ \t\xa0]+", " ", "\n\n".join(docs)).strip()        # some PDFs put tabs between words
    if len(full) < 200:
        raise SoundError("There is too little text to make a podcast from (under 200 characters).")
    chunks = pieces(full)
    model_id = model if model and model != "auto" else app.router.pick("worker")[0]
    if not model_id:
        raise SoundError("No text model is installed to write the script. Download one in Models first.")
    started = time.monotonic()
    writer = app.models.get_any(model_id)
    track.plan(podcast={"documents": names, "characters": len(full), "pieces": len(chunks), "minutes": minutes,
                        "style": style, "hosts": [a] if style == "narration" else [a, b], "focus": focus or None,
                        "writer": (writer or {}).get("name") or model_id})
    track.note(f"Read {len(full):,} characters from {', '.join(names)}; cut into {len(chunks)} piece{'s' if len(chunks) != 1 else ''} "
               "to look things up in while writing.")
    status(f"Starting the text model ({(writer or {}).get('name') or model_id})…")
    system = {"role": "system", "content": "You make podcasts from documents. You are accurate: every fact you use is in "
                                           "the material you are given. You write for the ear, in plain spoken English."}
    async with app.runtime.lease(model_id, as_loaded=True) as server:
        async def ask(prompt, schema, max_tokens, temperature=0.4, doing=""):
            written = [0, 0.0]

            def token(kind, value):               # the model is writing: a sign of life, with how far it is
                written[0] += len(str(value))
                if doing and time.monotonic() - written[1] > 1.0:
                    written[1] = time.monotonic()
                    status.beat(f"{doing} · about {written[0] // 6} words {'thought' if kind == 'reasoning' else 'written'} so far")
            return await app.llm.json([system, {"role": "user", "content": prompt}], schema, max_tokens=max_tokens,
                                      temperature=temperature, server=server, on_token=token)
        # 2. notes
        if len(full) <= 12000:
            notes = full
            track.note("The document is short enough to be planned from whole: no notes step.")
        else:
            track.at("Take notes")
            use = chunks if len(chunks) <= 24 else [chunks[round(i * (len(chunks) - 1) / 23)] for i in range(24)]
            found = []
            for i, c in enumerate(use):
                status(f"Taking notes: piece {i + 1} of {len(use)}…")
                got = await ask("Write down what matters in this part of a document: facts, numbers, names, findings and "
                                "claims, each as one full sentence that can be understood on its own. At most six."
                                + (f" The listener cares most about: {focus}." if focus else "")
                                + "\n\nPART:\n" + c["text"] + "\n\nJSON only.",
                                {"type": "object", "properties": {"points": {"type": "array", "maxItems": 6,
                                                                             "items": {"type": "string"}}},
                                 "required": ["points"]}, 500, 0.2, doing=f"Taking notes: piece {i + 1} of {len(use)}")
                found += [str(p).strip() for p in got.get("points") or [] if str(p).strip()]
                track.plan(notes=found[-60:])
            notes = "\n".join(f"- {p}" for p in found)[:14000]
        # 3. outline
        count = max(2, min(8, round(minutes / 1.5)))
        track.at("Plan the episode")
        status("Planning the episode…")
        outline = await ask(
            f"Plan a podcast episode of about {minutes:g} minutes about the document below, as {count} segments that "
            "follow each other naturally (what it is and why it matters first, the main findings in the middle, what "
            "follows from it last)." + (f" Focus on: {focus}." if focus else "") +
            " Give the episode a short title, one sentence that says what it is about, and for each segment a title "
            "and the two to five points from the document it covers.\n\nDOCUMENT"
            + (" (notes)" if notes is not full else "") + ":\n" + notes[:12000] + "\n\nJSON only.",
            {"type": "object", "required": ["title", "summary", "segments"], "properties": {
                "title": {"type": "string"}, "summary": {"type": "string"},
                "segments": {"type": "array", "minItems": 1, "maxItems": 8, "items": {
                    "type": "object", "required": ["title", "points"], "properties": {
                        "title": {"type": "string"}, "points": {"type": "array", "items": {"type": "string"}}}}}}},
            900, 0.3, doing="Planning the episode")
        segments = [s for s in outline.get("segments") or [] if s.get("title")][:8] or [{"title": "The document", "points": []}]
        title = str(outline.get("title") or Path(names[0]).stem)[:120]
        summary = str(outline.get("summary") or "")[:400]
        track.plan(episode={"title": title, "summary": summary,
                            "parts": [{"title": str(s["title"])[:160], "points": [str(p)[:300] for p in (s.get("points") or [])[:6]]}
                                      for s in segments]})
        track.note(f"Planned “{title}” in {len(segments)} part{'s' if len(segments) != 1 else ''}.")
        track.at("Write the script")
        # 4. script
        budget = max(60, int(minutes * WORDS_A_MINUTE / len(segments)))
        people = STYLES[style].format(a=a, b=b)
        speakers = ["A"] if style == "narration" else ["A", "B"]
        lines = []
        for i, seg in enumerate(segments):
            status(f"Writing part {i + 1} of {len(segments)}: {seg['title'][:60]}")
            points = "\n".join(f"- {p}" for p in seg.get("points") or [])
            source = "\n\n".join(c["text"][:3800] for c in nearest(seg["title"] + " " + points, chunks, 2))
            before = " ".join(f"{names_of(ln['speaker'], a, b)}: {' '.join(ln['parts'])}" for ln in lines[-2:])
            got = await ask(
                f"Write part {i + 1} of {len(segments)} of the podcast “{title}”: {people}. A is {a}"
                + ("" if style == "narration" else f", B is {b}") + f".\nThis part: {seg['title']}\nIt covers:\n{points}\n\n"
                "Rules: about " + str(budget) + " words in this part. Spoken language: short sentences, no lists, no "
                "headings, nothing that cannot be said aloud. Turns of one to three sentences. Say numbers the way people "
                "say them. Use only what the excerpts say; do not invent facts, names or numbers. "
                + ("" if style == "narration" else "Make it sound like two real people talking, not like reading: now and then a "
                   "turn starts with a small word (Hmm, Um, Right, Oh, Well, Mm-hm), the other host reacts in two or three "
                   "words (Really? Wow. That many?), someone finishes the other's thought, and a moment of thinking is "
                   "written as three dots ... inside the turn. Not in every turn. ")
                + ("Open the show: greet the listener, say what the document is and why it is worth ten minutes. " if i == 0
                   else "Do not greet again and do not repeat what was already said; carry on from: " + before[-500:] + " ")
                + ("End the show: the one thing to remember, and goodbye. " if i == len(segments) - 1
                   else "Do not say goodbye: the show goes on. ")
                + "\n\nEXCERPTS FROM THE DOCUMENT:\n" + source + "\n\nJSON only.",
                {"type": "object", "required": ["lines"], "properties": {"lines": {"type": "array", "minItems": 1, "items": {
                    "type": "object", "required": ["speaker", "text"], "properties": {
                        "speaker": {"type": "string", "enum": speakers}, "text": {"type": "string"}}}}}},
                int(budget * 2.2) + 250, 0.6, doing=f"Writing part {i + 1} of {len(segments)}")
            first = True
            for ln in got.get("lines") or []:
                said = parts_of(ln.get("text"))
                if said:
                    lines.append({"speaker": ln.get("speaker") if ln.get("speaker") in speakers else "A", "parts": said,
                                  "text": "… ".join(said), "new_part": first})
                    first = False
            track.plan(script=[{"speaker": names_of(ln["speaker"], a, b), "text": ln["text"][:600]} for ln in lines][-200:],
                       written={"parts": i + 1, "of": len(segments), "words": sum(len(ln["text"].split()) for ln in lines)})
    if not lines:
        raise SoundError("The model wrote no script. Try again, or use a larger text model.")
    # 5. record
    names_map = {"A": a, "B": b}
    if style != "narration":
        for ln in natural(lines):
            ln["text"] = "… ".join(ln["parts"])
    spoken = timed(lines, names_map)
    cast = {names_map[k]: v for k, v in (voices or {}).items() if k in names_map} | {k: v for k, v in (voices or {}).items() if k in (a, b)}
    out_dir = Path(out_dir)
    track.at("Record the voices")
    track.note(f"The script is written: {len(lines)} turns, {sum(len(ln['text'].split()) for ln in lines)} words. Recording it now.")
    record = await sound.speak(spoken, out_dir=out_dir, voices=cast or None, name=title, chat_id=chat_id, job_id=job_id,
                               on_status=on_status)
    if music and sound.can()["music"]:
        try:
            track.at("Opening music")
            status("Making the opening music…")
            tune = await sound.music(f"Short podcast opening theme for an episode called “{title}”: warm, modern, "
                                     "light percussion, positive", out_dir=out_dir, seconds=14, instrumental=True,
                                     chat_id=chat_id, job_id=job_id, on_status=on_status)
            mixed = await sound.mix([{"path": record["path"], "start": 6.0},
                                     {"path": tune["path"], "volume": 0.55, "fade_in": 0.5, "fade_out": 6.0}],
                                    out_dir=out_dir, name=title, length="longest", fade=0, chat_id=chat_id, job_id=job_id)
            for old in (record, tune):
                sound.forget(old["id"], delete_file=True)
            mixed["params"].update(voices=record["params"]["voices"])
            record = mixed
        except SoundError as exc:                      # the episode is still there without music
            app.log(f"podcast: no opening music: {exc}")
            status("The opening music could not be made; the episode is ready without it.")
    script = Path(record["path"]).with_suffix(".md")
    script.write_text(script_markdown(title, summary, names, lines, names_map), encoding="utf-8")
    params = {**record["params"], "mode": "podcast", "title": title, "summary": summary, "script": str(script),
              "sources": names, "words": sum(len(ln["text"].split()) for ln in lines), "parts": len(segments),
              "took": round(time.monotonic() - started, 1)}
    params.pop("segments", None)
    app.db.update("media", record["id"], {"params": params, "prompt": f"{title}: {summary}"[:4000]})
    return {**record, "params": params, "prompt": f"{title}: {summary}",
            "lines": [{"speaker": names_map[ln["speaker"]], "text": ln["text"]} for ln in lines]}


def names_of(speaker, a, b):
    return a if speaker == "A" else b
