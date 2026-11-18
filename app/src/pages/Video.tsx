// Video (2.14.0): make a video from scenes (pictures, narration, captions, music) on this computer, change an
// existing one (cut, join, other sound, subtitles), ask a video model for a short clip, record the camera or the
// screen, and keep them all. Every request is a task: "What is it doing?" opens its plan, its steps and the
// program's own output.
import React, { useEffect, useRef, useState } from "react";
import { Camera, Clapperboard, Cloud, Cpu, Download, ExternalLink, FileText, Film, FolderOpen, Image as ImageIcon, Monitor, Plus, Scissors, Sparkles, Square, Trash2, Upload, Wand2, X } from "lucide-react";
import { api, desktop, fileUrl, fmtBytes, fmtTime } from "../api";
import { go, useApp, useEvent } from "../store";
import { Badge, Button, ConfirmButton, Empty, Field, PageHeader, Progress, Spinner, Tabs, Toggle } from "../components/ui";
import { VideoClip } from "../components/Audio";
import { WorkButtons, WorkLine, WorkNote } from "../components/Work";

function external(url: string) { return (e: React.MouseEvent) => { if (desktop) { e.preventDefault(); desktop.openExternal(url); } }; }
const mmss = (s: number) => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`;
const SHAPE_NAME: any = { landscape: "Landscape (16:9)", portrait: "Portrait (9:16, for phones)", square: "Square" };
const emptyScene = () => ({ narration: "", picture: "", image: "", caption: "" });

function Jobs({ jobs, kinds, render, pauseStop, onForget }: any) {
  const mine = jobs.filter((j: any) => kinds.includes(j.what));
  if (!mine.length) return <div className="card muted">What you make appears here, and stays in the Library.</div>;
  return <>{[...mine].reverse().map((j: any) => (
    <div key={j.id} className="card stack-s">
      <div className="row between gap-s">
        <div className="ellipsis" title={j.title}><b>{j.title}</b></div>
        <WorkButtons base="/video" job={j} onForget={() => onForget && onForget(j.id)} />
      </div>
      <WorkNote job={j} pauseStop={pauseStop} />
      {j.error && <div className="small bad">{j.error}</div>}
      {j.what === "make" && !["done", "stopped"].includes(j.state) && j.made?.length > 0 && <div className="small muted">The pictures of {j.made.length} scene{j.made.length === 1 ? "" : "s"} are finished and kept.</div>}
      <WorkLine jobId={j.job_id} running={j.state === "running" || j.state === "paused"} paused={j.state === "paused"} />
      {j.result && render(j.result)}
    </div>
  ))}</>;
}

function Ready({ ok, children }: any) {
  return <div className="row gap-s wrap">{ok ? <Badge kind="ok">ready</Badge> : <Badge kind="warn">not set up</Badge>} {children}</div>;
}

// Make: an idea or a document becomes scenes (a text model writes them, or you do); the scenes become a video.
function Make({ state, jobs, voices, forget }: any) {
  const { toast } = useApp();
  const [idea, setIdea] = useState("");
  const [docs, setDocs] = useState<any[]>([]);
  const [seconds, setSeconds] = useState(45);
  const [title, setTitle] = useState("");
  const [scenes, setScenes] = useState<any[]>([emptyScene(), emptyScene()]);
  const [shape, setShape] = useState("landscape");
  const [voice, setVoice] = useState("");
  const [music, setMusic] = useState("");
  const [captions, setCaptions] = useState(true);
  const [motion, setMotion] = useState(true);
  const [writing, setWriting] = useState<string | null>(null);
  const file = useRef<HTMLInputElement>(null);
  const can = state.can;
  // the scenes a storyboard job wrote go into the editor, once
  useEffect(() => {
    const done = jobs.find((j: any) => j.id === writing && j.state !== "running");
    if (!done) return;
    setWriting(null);
    if (done.state === "done" && done.result?.scenes) {
      setScenes(done.result.scenes.map((s: any) => ({ ...emptyScene(), ...s })));
      setTitle(done.result.title || "");
      toast(`${done.result.scenes.length} scenes written: read them, change what you like, then make the video`, "ok");
    }
  }, [jobs, writing]);
  const set = (i: number, patch: any) => setScenes(scenes.map((s, j) => j === i ? { ...s, ...patch } : s));
  const addDoc = async (list: FileList | null) => {
    for (const f of Array.from(list || [])) try { const up = await api.upload("/sound/upload", f); setDocs((d) => [...d, { ...up, name: f.name }]); } catch (e: any) { toast(e.message, "error"); }
  };
  const picture = async (i: number, f?: File) => { if (f) try { set(i, { image: (await api.upload("/video/upload", f)).path, imageName: f.name }); } catch (e: any) { toast(e.message, "error"); } };
  const write = async () => {
    try { setWriting((await api.post("/video/jobs", { kind: "storyboard", idea, sources: docs.map((d) => d.path), minutes: seconds / 60 })).id); }
    catch (e: any) { toast(e.message, "error"); }
  };
  const filled = scenes.filter((s) => s.narration.trim() || s.picture.trim() || s.image || s.caption.trim());
  const make = async () => {
    try { await api.post("/video/jobs", { kind: "make", title, scenes: filled, shape, voice: voice || null, music: music.trim() || null, captions, motion }); }
    catch (e: any) { toast(e.message, "error"); }
  };
  const writingJob = jobs.find((j: any) => j.id === writing);
  return (
    <div className="grid-2 image-studio">
      <div className="stack-s">
        <div className="card stack-s">
          <p>A video built on this computer: each scene is a picture with a voice over it, captions in step with the voice, and music underneath if you like. No video model is needed.</p>
          <div className="note stack-s" style={{ alignItems: "flex-start" }}>
            <Ready ok={can.pictures}>Pictures by the image model{!can.pictures && <> (without one, scenes use your own pictures or become title cards) <Button small onClick={() => go("images")}>Images</Button></>}</Ready>
            <Ready ok={can.voice}>A voice for the narration{!can.voice && <> (without one, the words are shown as captions only) <Button small onClick={() => go("sound")}>Sound</Button></>}</Ready>
            <Ready ok={can.write}>A text model to write the scenes for you{!can.write && <> (or write them yourself below) <Button small onClick={() => go("models")}>Models</Button></>}</Ready>
          </div>
          <Field label="What is the video about?">
            <textarea className="input" rows={2} value={idea} onChange={(e) => setIdea(e.target.value)} placeholder="e.g. how solar water pumps work, for farmers who have never seen one" />
          </Field>
          <div className="row gap-s wrap">
            <Button small icon={<FileText size={13} />} onClick={() => file.current?.click()}>Or from a document</Button>
            <input ref={file} type="file" multiple accept=".pdf,.docx,.odt,.pptx,.txt,.md,.html,.htm,.csv" hidden onChange={(e) => { addDoc(e.target.files); e.target.value = ""; }} />
            {docs.map((d, i) => <span key={d.path} className="chip chip-toggle"><FileText size={12} /> <span className="ellipsis">{d.name}</span><button className="icon-btn" onClick={() => setDocs(docs.filter((_, j) => j !== i))}><X size={12} /></button></span>)}
            <select className="input narrow-auto" value={seconds} onChange={(e) => setSeconds(Number(e.target.value))}>{[[20, "About 20 seconds"], [45, "About 45 seconds"], [90, "About a minute and a half"], [180, "About 3 minutes"]].map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
            <Button kind="primary" icon={<Sparkles size={14} />} disabled={!can.write || !!writing || (!idea.trim() && !docs.length)} onClick={write}>Write the scenes</Button>
          </div>
          {writingJob && <div className="note"><Spinner size={14} /> {writingJob.text} <WorkLine jobId={writingJob.job_id} running /></div>}
        </div>
        <div className="card stack-s">
          <div className="row between"><b>Scenes</b><span className="small muted">{filled.length} scene{filled.length === 1 ? "" : "s"} · about {mmss(filled.reduce((n, s) => n + (s.narration.trim() ? 1.4 + s.narration.trim().split(/\s+/).length / 2.5 : 4), 0))}</span></div>
          <Field label="Title"><input className="input" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="The video's name" /></Field>
          {scenes.map((s, i) => (
            <div key={i} className="scene">
              <div className="scene-n">{i + 1}</div>
              <div className="grow stack-s">
                <textarea className="input" rows={3} value={s.narration} onChange={(e) => set(i, { narration: e.target.value })} placeholder="What the voice says in this scene (one or two short sentences)" />
                {s.image
                  ? <div className="row gap-s"><img className="scene-thumb" src={fileUrl(s.image)} alt="" /><span className="small ellipsis">{s.imageName || s.image.split(/[\\/]/).pop()}</span><button className="link" onClick={() => set(i, { image: "", imageName: "" })}>Use a described picture instead</button></div>
                  : <div className="row gap-s">
                    <input className="input" value={s.picture} onChange={(e) => set(i, { picture: e.target.value })} placeholder={can.pictures ? "What the scene shows (the image model makes it)" : "No image model: add your own picture, or leave empty for a title card"} />
                    <label className="btn btn-small" title="Use your own picture for this scene"><ImageIcon size={13} /><input type="file" accept="image/*" hidden onChange={(e) => { picture(i, e.target.files?.[0]); e.target.value = ""; }} /></label>
                  </div>}
              </div>
              <button className="icon-btn" title="Remove this scene" onClick={() => setScenes(scenes.filter((_, j) => j !== i))}><X size={15} /></button>
            </div>
          ))}
          <div><Button small icon={<Plus size={13} />} onClick={() => setScenes([...scenes, emptyScene()])}>Add a scene</Button></div>
          <div className="row gap-m wrap">
            <Field label="Shape"><select className="input narrow-auto" value={shape} onChange={(e) => setShape(e.target.value)}>{Object.keys(state.shapes).map((k) => <option key={k} value={k}>{SHAPE_NAME[k] || k}</option>)}</select></Field>
            {can.voice && <Field label="Voice"><select className="input narrow-auto" value={voice} onChange={(e) => setVoice(e.target.value)}><option value="">The default voice</option>{voices.map((v: any) => <option key={v.id} value={v.id}>{v.name}{v.language ? ` · ${v.language}` : ""}</option>)}</select></Field>}
          </div>
          {can.music && <Field label="Music under it (optional)"><input className="input" value={music} onChange={(e) => setMusic(e.target.value)} placeholder="Describe it, e.g. calm acoustic guitar, hopeful" /></Field>}
          <div className="row gap-m wrap">
            <Toggle checked={captions} onChange={setCaptions} label="Captions" />
            <Toggle checked={motion} onChange={setMotion} label="Slow movement over the pictures" />
          </div>
          <div className="small muted">On a laptop a picture takes the image model minutes each; the voice and putting the video together take seconds.</div>
          <div><Button kind="primary" icon={<Clapperboard size={14} />} disabled={!filled.length} onClick={make}>Make the video</Button></div>
        </div>
      </div>
      <div className="stack-s"><Jobs jobs={jobs} kinds={["make"]} pauseStop={state.pause_stop_minutes} onForget={forget} render={(r: any) => <VideoClip item={r} large />} /></div>
    </div>
  );
}

// Change: a part of a video, several joined, another sound, subtitles.
function Change({ state, jobs, library, forget }: any) {
  const { toast } = useApp();
  const [clips, setClips] = useState<any[]>([]);
  const [mute, setMute] = useState(false);
  const [sound, setSound] = useState<any>(null);
  const [volume, setVolume] = useState(0.2);
  const [subtitles, setSubtitles] = useState(false);
  const [shape, setShape] = useState("");
  const [busy, setBusy] = useState(false);
  const file = useRef<HTMLInputElement>(null), audio = useRef<HTMLInputElement>(null);
  const add = async (list: FileList | null) => {
    setBusy(true);
    for (const f of Array.from(list || [])) try {
      const up = await api.upload("/video/upload", f);
      setClips((c) => [...c, { path: up.path, name: f.name, seconds: up.info?.seconds || 0, start: 0, end: up.info?.seconds || 0, info: up.info }]);
    } catch (e: any) { toast(e.message, "error"); }
    setBusy(false);
  };
  const addOwn = async (v: any) => {
    try { const info = await api.get(`/video/info?path=${encodeURIComponent(v.path)}`); setClips((c) => [...c, { path: v.path, name: v.name, seconds: info.seconds, start: 0, end: info.seconds, info }]); }
    catch (e: any) { toast(e.message, "error"); }
  };
  const set = (i: number, patch: any) => setClips(clips.map((c, j) => j === i ? { ...c, ...patch } : c));
  const total = clips.reduce((n, c) => n + Math.max(0, (c.end || c.seconds) - (c.start || 0)), 0);
  const run = async () => {
    try {
      await api.post("/video/jobs", { kind: "edit", clips: clips.map((c) => ({ path: c.path, start: Number(c.start) || 0, end: Number(c.end) || c.seconds })), mute,
        sound: sound?.path || null, sound_volume: sound ? volume : null, subtitles: subtitles ? "auto" : null, shape: shape || null });
    } catch (e: any) { toast(e.message, "error"); }
  };
  return (
    <div className="grid-2 image-studio">
      <div className="card stack-s">
        <p>Keep a part of a video, join several, give it another sound, and burn subtitles in. The result is a new .mp4; your file is not changed.</p>
        <div className="drop" onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); add(e.dataTransfer.files); }} onClick={() => file.current?.click()}>
          <Film size={22} /><div><b>{busy ? "Adding…" : "Choose a video, or drop it here"}</b><div className="small muted">Several videos are joined in the order shown.</div></div>
          <input ref={file} type="file" multiple accept="video/*,.mkv" hidden onChange={(e) => { add(e.target.files); e.target.value = ""; }} />
        </div>
        {library.length > 0 && <select className="input" value="" onChange={(e) => { const v = library.find((x: any) => x.id === e.target.value); if (v) addOwn(v); }}>
          <option value="">Or one from the library…</option>{library.map((v: any) => <option key={v.id} value={v.id}>{v.name}</option>)}</select>}
        {clips.map((c, i) => (
          <div key={c.path + i} className="scene">
            <div className="scene-n">{i + 1}</div>
            <div className="grow stack-s">
              <div className="row between gap-s"><span className="ellipsis"><b>{c.name}</b> <span className="small muted">{mmss(c.seconds)}{c.info ? ` · ${c.info.width} × ${c.info.height}${c.info.audio ? "" : " · no sound"}` : ""}</span></span></div>
              <div className="row gap-s small">Keep from <input className="input narrow" type="number" min={0} step={0.5} value={c.start} onChange={(e) => set(i, { start: e.target.value })} /> to
                <input className="input narrow" type="number" min={0} step={0.5} value={c.end} onChange={(e) => set(i, { end: e.target.value })} /> seconds</div>
            </div>
            <button className="icon-btn" onClick={() => setClips(clips.filter((_, j) => j !== i))}><X size={15} /></button>
          </div>
        ))}
        <Toggle checked={mute} onChange={setMute} label="Take the video's own sound away" />
        <div className="row gap-s wrap">
          <Button small icon={<Upload size={13} />} onClick={() => audio.current?.click()}>{sound ? "Another recording" : "Add a narration or music"}</Button>
          <input ref={audio} type="file" accept="audio/*" hidden onChange={async (e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) try { setSound({ ...(await api.upload("/sound/upload", f)), name: f.name }); } catch (err: any) { toast(err.message, "error"); } }} />
          {sound && <><span className="small ellipsis">{sound.name}</span>
            <span className="small muted">volume</span><input type="range" min={0.05} max={1} step={0.05} value={volume} onChange={(e) => setVolume(Number(e.target.value))} /><span className="small">{Math.round(volume * 100)}%</span>
            <button className="icon-btn" onClick={() => setSound(null)}><X size={14} /></button></>}
        </div>
        <Toggle checked={subtitles} onChange={setSubtitles} disabled={!state.can.subtitles} label={state.can.subtitles ? "Subtitles, written from what is said in the video" : "Subtitles need a transcription model (Sound → Voices & models)"} />
        <Field label="Shape"><select className="input narrow-auto" value={shape} onChange={(e) => setShape(e.target.value)}><option value="">As the video is</option>{Object.keys(state.shapes).map((k) => <option key={k} value={k}>{SHAPE_NAME[k] || k}</option>)}</select></Field>
        <div><Button kind="primary" icon={<Scissors size={14} />} disabled={!clips.length} onClick={run}>{clips.length > 1 ? `Join ${clips.length} videos` : "Make the new video"}{total ? ` (${mmss(total)})` : ""}</Button></div>
      </div>
      <div className="stack-s"><Jobs jobs={jobs} kinds={["edit"]} pauseStop={state.pause_stop_minutes} onForget={forget} render={(r: any) => <VideoClip item={r} large />} /></div>
    </div>
  );
}

// Clip: a few seconds made by a video model from a description.
function Clip({ state, jobs, toModels, forget }: any) {
  const { toast } = useApp();
  const [prompt, setPrompt] = useState("");
  const [shape, setShape] = useState("landscape");
  const [model, setModel] = useState("");
  const local = state.models.filter((m: any) => m.ready);
  if (!state.can.clip) return (
    <Empty icon={<Film size={34} />} title="A clip needs a video model">
      <p>A video model makes a few seconds of moving picture from a description. Download one for this computer (slow without a graphics card), or switch on Google’s (it costs money).</p>
      <p className="muted">For explainers, stories and summaries you do not need one: <a onClick={() => go("video", "make")}>Make</a> builds a video from pictures, a voice and music.</p>
      <Button kind="primary" onClick={toModels}>Choose a video model</Button>
    </Empty>
  );
  const start = async () => {
    try { await api.post("/video/jobs", { kind: "clip", prompt, shape, model: model || null }); } catch (e: any) { toast(e.message, "error"); }
  };
  const online = !local.length || model === "google";
  return (
    <div className="grid-2 image-studio">
      <div className="card stack-s">
        <Field label="Describe the clip"><textarea className="input" rows={4} value={prompt} onChange={(e) => setPrompt(e.target.value)} placeholder="What is seen, how it moves, the light, the style. e.g. a paper boat drifting down a rain gutter, close up, soft evening light" /></Field>
        <div className="row gap-m wrap">
          <Field label="Made by"><select className="input narrow-auto" value={model} onChange={(e) => setModel(e.target.value)}>
            {local.map((m: any) => <option key={m.id} value={m.id === local[0].id ? "" : m.id}>{m.name} (this computer)</option>)}
            {state.online.ready && <option value={local.length ? "google" : ""}>Google · {state.online.model} (online)</option>}</select></Field>
          {online && <Field label="Shape"><select className="input narrow-auto" value={shape} onChange={(e) => setShape(e.target.value)}><option value="landscape">Landscape (16:9)</option><option value="portrait">Portrait (9:16)</option></select></Field>}
        </div>
        <div className="small muted">{online ? `Sent to Google with your key. Counted as US$${state.online.cost.toFixed(2)} of the online budget (an amount you set in Models here, not Google’s price).`
          : "Made on this computer: minutes on a graphics card, an hour or more a clip on the processor alone. “What is it doing?” shows the engine’s own lines while it works."}</div>
        <div><Button kind="primary" icon={<Wand2 size={14} />} disabled={!prompt.trim()} onClick={start}>Make the clip</Button></div>
      </div>
      <div className="stack-s"><Jobs jobs={jobs} kinds={["clip"]} pauseStop={state.pause_stop_minutes} onForget={forget} render={(r: any) => <VideoClip item={r} large />} /></div>
    </div>
  );
}

// Record: the camera or the screen, kept in the library.
function Record({ onSaved }: any) {
  const { toast } = useApp();
  const [live, setLive] = useState<MediaStream | null>(null);
  const [what, setWhat] = useState("");
  const [seconds, setSeconds] = useState(0);
  const [saving, setSaving] = useState(false);
  const rec = useRef<MediaRecorder | null>(null);
  const view = useRef<HTMLVideoElement>(null);
  useEffect(() => { if (view.current) view.current.srcObject = live; }, [live]);
  useEffect(() => { if (!live) return; const t = setInterval(() => setSeconds((s) => s + 1), 1000); return () => clearInterval(t); }, [live]);
  const start = async (kind: "camera" | "screen") => {
    try {
      const stream = kind === "camera" ? await navigator.mediaDevices.getUserMedia({ video: true, audio: true })
        : await navigator.mediaDevices.getDisplayMedia({ video: true, audio: true });
      if (kind === "screen") try {         // the microphone over a screen recording, when it is allowed
        const mic = await navigator.mediaDevices.getUserMedia({ audio: true });
        if (!stream.getAudioTracks().length) mic.getAudioTracks().forEach((t) => stream.addTrack(t)); else mic.getTracks().forEach((t) => t.stop());
      } catch { /* a screen recording without a voice is still a recording */ }
      const chunks: BlobPart[] = [];
      const r = new MediaRecorder(stream);
      r.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
      r.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        setLive(null);
        if (!chunks.length) return;
        setSaving(true);
        const type = r.mimeType || "video/webm";
        const name = `${kind}-${new Date().toISOString().slice(0, 16).replace(/[:T]/g, "-")}.${type.includes("mp4") ? "mp4" : "webm"}`;
        try { await api.upload("/video/upload?keep=1", new File(chunks, name, { type })); toast("The recording is in the library", "ok"); onSaved(); }
        catch (e: any) { toast(e.message, "error"); }
        setSaving(false);
      };
      stream.getVideoTracks()[0]?.addEventListener("ended", () => r.state !== "inactive" && r.stop());      // "Stop sharing" in the system's own bar
      rec.current = r; setSeconds(0); setWhat(kind); setLive(stream); r.start(1000);
    } catch (e: any) {
      toast(e.name === "NotAllowedError" ? `The ${kind} was not allowed. Allow it in the system’s privacy settings and try again.`
        : e.name === "NotFoundError" ? `No ${kind} was found on this computer.` : `The ${kind} could not be recorded: ${e.message}`, "error");
    }
  };
  return (
    <div className="card stack-s">
      <p>Record yourself or what is on the screen. The recording is kept in the library; cut it, add music or subtitles in <a onClick={() => go("video", "change")}>Change</a>.</p>
      {!live && <div className="row gap-s wrap">
        <Button kind="primary" icon={<Camera size={14} />} disabled={saving} onClick={() => start("camera")}>Record the camera</Button>
        <Button icon={<Monitor size={14} />} disabled={saving} onClick={() => start("screen")}>Record the screen</Button>
        {saving && <span className="note"><Spinner size={14} /> Saving the recording…</span>}
      </div>}
      {live && <>
        <video ref={view} className="video-live" autoPlay muted playsInline />
        <div className="row gap-s"><span className="rec-dot" /> Recording the {what} · {mmss(seconds)}
          <Button kind="danger" icon={<Square size={12} />} onClick={() => rec.current?.stop()}>Stop and keep</Button></div>
      </>}
    </div>
  );
}

function Library({ items, reload }: any) {
  if (!items.length) return <Empty icon={<Film size={34} />} title="No videos yet">The videos you make, change and record are kept here.</Empty>;
  return (
    <div className="video-grid">
      {items.map((v: any) => (
        <div key={v.id} className="card stack-s">
          {v.exists ? <VideoClip item={v} /> : <div className="small muted">{v.name}: the file is no longer there.</div>}
          <div className="row between gap-s">
            <span className="small muted">{fmtTime(v.created)}{v.params?.mode ? ` · ${v.params.mode === "assembled" ? "made from scenes" : v.params.mode}` : ""}</span>
            <ConfirmButton small kind="ghost" onConfirm={async () => { await api.del(`/video/library/${v.id}?file=1`); reload(); }}><Trash2 size={13} /></ConfirmButton>
          </div>
        </div>
      ))}
    </div>
  );
}

function Models({ state, reload }: any) {
  const { toast } = useApp();
  const [cost, setCost] = useState(String(state.online.cost));
  const save = (patch: any) => api.post("/video/settings", patch).then(reload).catch((e) => toast(e.message, "error"));
  return (
    <div className="stack">
      <div className="card stack-s">
        <b>Videos from scenes need no video model</b>
        <div className="small muted">Make uses the image model (Images), the voices and the music model (Sound), and puts the video together itself with the media library that ships with KimiLM (H.264 video, AAC sound).</div>
      </div>
      <div className="card stack-s">
        <div className="row gap-s"><Cpu size={16} /><b>A video model on this computer</b></div>
        <div className="small muted">Run by the image engine (stable-diffusion.cpp){state.engine ? "" : ": install it in Images → Engine first"}. Honest expectation: a clip of about two seconds; minutes on a graphics card, an hour or more on the processor alone.</div>
        {state.models.map((m: any) => (
          <div key={m.id} className="row between gap-s">
            <span><b>{m.name}</b> <span className="small muted">{fmtBytes(m.size)}{m.ready ? "" : m.missing?.length ? ` · files missing: ${m.missing.join(", ")}` : " · the image engine is not installed"}</span></span>
            <span className="row gap-s">{m.ready ? <Badge kind="ok">ready</Badge> : <Badge kind="warn">not ready</Badge>}
              <ConfirmButton small kind="ghost" onConfirm={async () => { await api.del(`/video/models/${m.id}`); reload(); }}><Trash2 size={13} /></ConfirmButton></span>
          </div>
        ))}
        {state.recipes.map((r: any) => {
          const d = r.download, active = d && ["starting", "downloading"].includes(d.state);
          return (
            <div key={r.key} className="stack-s">
              <div className="row between gap-s wrap">
                <span><b>{r.name}</b> <span className="small muted">about {r.approx_gb} GB · {r.licence} · <a href={r.page} target="_blank" rel="noreferrer" onClick={external(r.page)}>its page <ExternalLink size={11} /></a></span></span>
                {r.installed ? <Badge kind="ok">installed</Badge>
                  : active ? <Button small onClick={() => api.del(`/video/recipes/${r.key}/download`).then(reload)}>Stop</Button>
                    : <Button small icon={<Download size={13} />} onClick={() => api.post(`/video/recipes/${r.key}/download`).then(reload).catch((e) => toast(e.message, "error"))}>Download</Button>}
              </div>
              <div className="small muted">{r.about}{r.fits ? "" : ` This computer has less than the ${r.min_ram_gb} GB of memory it wants.`}</div>
              {active && <div className="row gap-s"><Progress value={d.done} total={d.total || 1} /><span className="small muted">{d.file} · {fmtBytes(d.done)} of {fmtBytes(d.total)}</span></div>}
              {d?.state === "failed" && <div className="small bad">{d.error}</div>}
            </div>
          );
        })}
      </div>
      <div className="card stack-s">
        <div className="row gap-s"><Cloud size={16} /><b>Online: Google</b></div>
        <div className="small muted">{state.online.model} makes a clip of a few seconds, with sound, from a description (and optionally a picture). The description is sent to Google and paid with your key. {state.notes.openai}</div>
        {!state.online.key ? <div className="row gap-s"><Badge kind="warn">no key</Badge> Add your Google key first. <Button small onClick={() => go("models", "online")}>Models → Online</Button></div>
          : <Toggle checked={state.online.on} onChange={(v: boolean) => save({ online: v })} label="Allow clips to be made by Google’s video model" />}
        <div className="row gap-s wrap small">Count each clip as US$
          <input className="input narrow" type="number" min={0} step={0.05} value={cost} onChange={(e) => setCost(e.target.value)} onBlur={() => save({ cost: Number(cost) || 0 })} />
          of the monthly online budget.</div>
        <div className="small muted">{state.notes.cost}</div>
      </div>
    </div>
  );
}

export default function Video({ tab: first }: { tab?: string }) {
  const [state, setState] = useState<any>(null);
  const [jobs, setJobs] = useState<any[]>([]);
  const [library, setLibrary] = useState<any[]>([]);
  const [voices, setVoices] = useState<any[]>([]);
  const [tab, setTab] = useState(first || "make");
  useEffect(() => { if (first) setTab(first); }, [first]);
  const load = () => {
    api.get("/video").then((s) => { setState(s); setJobs((old) => { const seen = new Set(old.map((j) => j.id)); return [...old, ...s.jobs.filter((j: any) => !seen.has(j.id))]; }); }).catch(() => {});
    api.get("/video/library").then((r) => setLibrary(r.videos)).catch(() => {});
  };
  useEffect(() => { load(); api.get("/sound").then((s) => setVoices((s.voices || []).filter((v: any) => v.ready))).catch(() => {}); }, []);
  useEvent((e) => {
    if (e.kind === "video_job") setJobs((list) => list.some((j) => j.id === e.id) ? list.map((j) => j.id === e.id ? e : j) : [...list, e]);
    if (["video_changed", "images_changed", "sound_changed", "image_download"].includes(e.kind) || (e.kind === "video_job" && e.state !== "running")) load();
  });
  const forget = (id: string) => setJobs((list) => list.filter((j) => j.id !== id));
  if (!state) return <div className="page"><Spinner /></div>;
  return (
    <div className="page">
      <PageHeader title="Video" subtitle="Make videos from pictures, a voice and music on this computer, change the ones you have, record the camera or the screen, or ask a video model for a short clip." />
      <Tabs value={tab} onChange={setTab} tabs={[{ id: "make", label: "Make" }, { id: "change", label: "Change" }, { id: "clip", label: "Clip" }, { id: "record", label: "Record" },
        { id: "library", label: `Library${library.length ? ` (${library.length})` : ""}` }, { id: "models", label: "Models" }]} />
      {tab === "make" && <Make state={state} jobs={jobs} voices={voices} forget={forget} />}
      {tab === "change" && <Change state={state} jobs={jobs} library={library.filter((v) => v.exists)} forget={forget} />}
      {tab === "clip" && <Clip state={state} jobs={jobs} toModels={() => setTab("models")} forget={forget} />}
      {tab === "record" && <Record onSaved={load} />}
      {tab === "library" && <Library items={library} reload={load} />}
      {tab === "models" && <Models state={state} reload={load} />}
    </div>
  );
}
