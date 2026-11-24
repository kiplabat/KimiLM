"""Stand-in for acestep.cpp's two programs in the tests: `fake_ace.py lm …` is ace-lm, `fake_ace.py synth …` is
ace-synth. They take exactly the options of the real programs (read from their source at the pinned commit), find
their models by file name in --models the way the real registry does, write the numbered files the real ones
write (request.json → request0.json → request00.mp3) and report on stderr in the same form.

A caption containing "explode" fails in the planner; one containing "silent" fails in the renderer."""
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

OPTIONS = {"lm": {"--models": 1, "--request": 1, "--max-seq": 1, "--no-fsm": 0, "--no-fa": 0, "--no-batch-cfg": 0, "--clamp-fp16": 0},
           "synth": {"--models": 1, "--request": 1, "--adapters": 1, "--src-audio": 1, "--ref-audio": 1, "--vae-chunk": 1,
                     "--vae-overlap": 1, "--no-fa": 0, "--no-batch-cfg": 0, "--clamp-fp16": 0}}


def fatal(tag, text):
    sys.stderr.write(f"[{tag}] FATAL: {text}\n")
    sys.exit(1)


def main(argv):
    tool, args, got, i = argv[0], argv[1:], {}, 0
    tag = "Ace-LM" if tool == "lm" else "Ace-Synth"
    while i < len(args):
        if args[i] not in OPTIONS[tool]:
            sys.stderr.write(f"[CLI] ERROR: unknown option {args[i]}\n")
            return 1
        n = OPTIONS[tool][args[i]]
        got[args[i]] = args[i + 1] if n else True
        i += 1 + n
    for need in ("--models", "--request"):
        if need not in got:
            sys.stderr.write(f"[CLI] ERROR: {need} required\n")
            return 1
    models = Path(got["--models"])
    if not models.is_dir():
        fatal(tag, f"cannot scan --models {models}")
    names = sorted(p.name for p in models.glob("*.gguf"))
    path = Path(got["--request"])
    try:
        req = json.loads(path.read_text())
    except Exception:
        fatal(tag, f"failed to parse {path}")
    base = str(path)[: -len(".json")]
    if tool == "lm":
        lms = [n for n in names if "-lm-" in n]
        if not lms:
            fatal(tag, f"no LM models found under {models}")
        if req.get("lm_model") and req["lm_model"] not in lms:
            fatal(tag, f"lm_model '{req['lm_model']}' not found in registry")
        if "explode" in req.get("caption", ""):
            fatal(tag, "the planner could not continue")
        for step in (1, 2, 3):
            sys.stderr.write(f"[LM-Phase2] Step {step}, 1 active, {step * 5} total codes, 21.5 tok/s\n")
        out = {**req, "bpm": req.get("bpm") or 96, "keyscale": req.get("keyscale") or "A minor", "timesignature": "4",
               "vocal_language": req.get("vocal_language") or "en", "audio_codes": "12,7,33,4",
               "lyrics": req.get("lyrics") or "[Verse]\nA song the stand-in wrote\n[Chorus]\nLa la la"}
        Path(base + "0.json").write_text(json.dumps(out))
        return 0
    if not req.get("caption"):
        fatal(tag, f"caption is empty in {path}")
    if not any(n.startswith("acestep-v15") for n in names) or not any(n.startswith("vae") for n in names) \
            or not any("Embedding" in n for n in names):
        fatal(tag, "registry needs DiT, text-encoder and VAE models")
    if req.get("synth_model") and req["synth_model"] not in names:
        fatal(tag, f"synth_model '{req['synth_model']}' not found in registry")
    if not req.get("audio_codes"):
        fatal(tag, "no audio codes in the request (run ace-lm first)")
    if "silent" in req["caption"]:
        sys.stderr.write("[Ace-Synth] ERROR: batch run failed\n")
        return 1
    fmt = req.get("output_format", "mp3")
    if fmt not in ("mp3", "wav16", "wav24", "wav32"):
        fatal(tag, f"invalid output_format '{fmt}' (use: mp3, wav16, wav24, wav32)")
    import numpy as np
    from kimilm.sound import write_audio
    seconds = float(req.get("duration") or 120)
    t = np.arange(int(48000 * min(seconds, 20))) / 48000
    tone = (0.3 * np.sin(2 * math.pi * 220 * t)).astype("float32")
    for step in range(1, int(req.get("inference_steps") or 8) + 1):
        sys.stderr.write(f"[DiT] step {step}/{req.get('inference_steps') or 8}\n")
    write_audio(base + "0" + (".mp3" if fmt == "mp3" else ".wav"), np.stack([tone, tone], axis=1), 48000)
    sys.stderr.write("[Ace-Synth] All done\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
