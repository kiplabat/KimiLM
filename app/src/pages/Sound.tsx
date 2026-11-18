// Sound (2.12.0): speak texts and scripts, write down recordings, make music, keep the recordings, and choose
// the voices and models: on this computer, or online (OpenAI, Google).
import React, { useEffect, useMemo, useRef, useState } from "react";
import { AudioLines, Cloud, Copy, Cpu, Download, ExternalLink, FileText, FolderOpen, Layers, Mic, Music, Play, Save, Plus, Podcast as PodcastIcon, Square, Star, Trash2, Upload, UserRound, X } from "lucide-react";
import { api, desktop, fileUrl, fmtBytes, fmtTime } from "../api";
import { go, useApp, useEvent } from "../store";
import { Badge, Button, ConfirmButton, Empty, Field, Modal, PageHeader, Progress, Spinner, Tabs } from "../components/ui";
import { AudioClip, RecordButton, composerTracks, newTrack, saveComposer, soundJob } from "../components/Audio";
import { WorkLine } from "../components/Work";

const ROLE: Record<string, string> = { tts: "Speech", stt: "Transcription", music: "Music", clone: "Voice from a recording" };
function external(url: string) { return (e: React.MouseEvent) => { if (desktop) { e.preventDefault(); desktop.openExternal(url); } }; }
const speakersOf = (text: string) => {
  const names: string[] = [];
  for (const line of text.split("\n")) {
    const m = line.trim().match(/^\**([A-Za-z][\w .'-]{0,30}?)\**\s*:\s+\S/);
    if (m && !/^(https?|note|warning|e\.g)$/i.test(m[1]) && !names.includes(m[1])) names.push(m[1]);
  }
  return names.length > 1 ? names : [];
};

function VoiceSelect({ voices, value, onChange, auto }: any) {
  const groups: [string, any[]][] = [["My recordings", voices.filter((v: any) => v.kind === "recording")],
    ["On this computer", voices.filter((v: any) => v.kind === "local")], ["Online", voices.filter((v: any) => v.kind === "online")]];
  return (
    <select className="input" value={value} onChange={(e) => onChange(e.target.value)}>
      {auto && <option value="">{auto}</option>}
      {groups.filter(([, list]) => list.length).map(([label, list]) => (
        <optgroup key={label} label={label}>
          {list.map((v: any) => <option key={v.id} value={v.id} disabled={!v.ready}>{v.name}{v.kind === "local" ? ` · ${v.language}${v.gender ? `, ${v.gender}` : ""}` : v.kind === "online" ? ` · ${v.model_name}` : ""}</option>)}
        </optgroup>
      ))}
    </select>
  );
}

function Jobs({ jobs, kinds, render }: any) {
  const mine = jobs.filter((j: any) => kinds.includes(j.kind));
  if (!mine.length) return <div className="card muted">What you make appears here, and stays in the Library.</div>;
  return <>{[...mine].reverse().map((j: any) => (
    <div key={j.id} className="card stack-s">
      <div className="row between gap-s">
        <div className="ellipsis" title={j.title}><b>{j.title}</b></div>
        {j.state === "running" ? <Button small icon={<Square size={12} />} onClick={() => api.del(`/sound/jobs/${j.id}`)}>Stop</Button>
          : <Badge kind={j.state === "done" ? "ok" : j.state === "failed" ? "bad" : "neutral"}>{j.state}</Badge>}
      </div>
      {j.state === "running" && <div className="note"><Spinner size={14} /> {j.text}</div>}
      {j.error && <div className="small bad">{j.error}</div>}
      <WorkLine jobId={j.job_id} running={j.state === "running"} />
      {j.result && render(j.result)}
    </div>
  ))}</>;
}

function NeedsModel({ what, toModels }: any) {
  return (
    <Empty icon={<AudioLines size={34} />} title={`Choose what does the ${what} first`}>
      <p>Download a model for this computer (it then works without the internet), or switch on an online one.</p>
      <Button kind="primary" onClick={toModels}>Set up sound models</Button>
    </Empty>
  );
}

// ---------------------------------------------------------------------------------------------- Podcast
function Podcast({ state, jobs, toModels }: any) {
  const { toast } = useApp();
  const [docs, setDocs] = useState<any[]>([]);
  const [text, setText] = useState("");
  const [paste, setPaste] = useState(false);
  const [minutes, setMinutes] = useState(5);
  const [style, setStyle] = useState("conversation");
  const [focus, setFocus] = useState("");
  const [music, setMusic] = useState(false);
  const [more, setMore] = useState(false);
  const [hosts, setHosts] = useState(["Alex", "Sam"]);
  const [cast, setCast] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [getting, setGetting] = useState<any>(null);
  const file = useRef<HTMLInputElement>(null);
  useEvent((e) => { if (e.kind === "sound_download" && e.key === "kokoro-v1") setGetting(e); });
  const add = async (list: FileList | null) => {
    setBusy(true);
    for (const f of Array.from(list || [])) {
      try { const up = await api.upload("/sound/upload", f); setDocs((d) => [...d, { ...up, name: f.name }]); } catch (e: any) { toast(e.message, "error"); }
    }
    setBusy(false);
  };
  const start = async () => {
    const names = style === "narration" ? hosts.slice(0, 1) : hosts;
    try {
      await api.post("/sound/jobs", { kind: "podcast", sources: docs.map((d) => d.path), text: paste ? text : "", minutes, style, focus, music,
        hosts: names, voices: Object.fromEntries(names.map((n, i) => [n, cast[String(i)]]).filter(([, v]) => v)) });
    } catch (e: any) { toast(e.message, "error"); }
  };
  const ready = state.can.tts && state.text_model;
  const active = getting && ["starting", "downloading", "unpacking"].includes(getting.state);
  return (
    <div className="grid-2 image-studio">
      <div className="card stack-s">
        <p>Give KimiLM a document and get a podcast about it: it reads the document, writes a conversation between two hosts from what the document says, and records it. Everything happens on this computer.</p>
        {!ready && <div className="note stack-s" style={{ alignItems: "flex-start" }}>
          <b>Two things are needed once:</b>
          <div className="row gap-s">{state.text_model ? <Badge kind="ok">ready</Badge> : <Badge kind="warn">missing</Badge>} A text model, to read the document and write the script.
            {!state.text_model && <Button small onClick={() => go("models")}>Get one in Models</Button>}</div>
          <div className="row gap-s wrap">{state.can.tts ? <Badge kind="ok">ready</Badge> : <Badge kind="warn">missing</Badge>} Voices (Kokoro, 334 MB, downloaded once).
            {!state.can.tts && !active && <Button small kind="primary" icon={<Download size={13} />} onClick={() => api.post("/sound/catalog/kokoro-v1/download").then(setGetting).catch((e) => toast(e.message, "error"))}>Get the voices</Button>}
            {active && <span className="row gap-s"><Progress value={getting.done} total={getting.total || 1} /><span className="small muted">{getting.state === "unpacking" ? "unpacking…" : `${fmtBytes(getting.done)} of ${fmtBytes(getting.total)}`}</span></span>}</div>
          {getting?.state === "failed" && <div className="small bad">{getting.error}</div>}
        </div>}
        <div className="drop" onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); add(e.dataTransfer.files); }} onClick={() => file.current?.click()}>
          <FileText size={22} /><div><b>{busy ? "Adding…" : "Choose a document, or drop it here"}</b><div className="small muted">PDF, Word, slides, text. Several documents become one episode.</div></div>
          <input ref={file} type="file" multiple accept=".pdf,.docx,.odt,.pptx,.txt,.md,.html,.htm,.csv" hidden onChange={(e) => { add(e.target.files); e.target.value = ""; }} />
        </div>
        {docs.map((d, i) => <div key={d.path} className="row between gap-s"><span className="ellipsis"><FileText size={13} /> {d.name}</span><button className="icon-btn" onClick={() => setDocs(docs.filter((_, j) => j !== i))}><X size={14} /></button></div>)}
        <a className="small" onClick={() => setPaste(!paste)}>{paste ? "No pasted text" : "Or paste a text instead"}</a>
        {paste && <textarea className="input" rows={5} value={text} onChange={(e) => setText(e.target.value)} placeholder="Paste an article, notes, a report…" />}
        <div className="row gap-m wrap">
          <Field label="Length">
            <select className="input narrow-auto" value={minutes} onChange={(e) => setMinutes(Number(e.target.value))}>{[[3, "About 3 minutes"], [5, "About 5 minutes"], [10, "About 10 minutes"], [15, "About 15 minutes"]].map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
          </Field>
          <Field label="Kind">
            <select className="input narrow-auto" value={style} onChange={(e) => setStyle(e.target.value)}><option value="conversation">Two hosts talking</option><option value="interview">An interview</option><option value="narration">One narrator</option></select>
          </Field>
        </div>
        <a className="small" onClick={() => setMore(!more)}>{more ? "Fewer settings" : "More settings"}</a>
        {more && <div className="stack-s">
          <Field label="What matters most to the listener (optional)"><input className="input" value={focus} onChange={(e) => setFocus(e.target.value)} placeholder="e.g. what it means for rural households" /></Field>
          {(style === "narration" ? [0] : [0, 1]).map((i) => (
            <div key={i} className="row gap-s"><input className="input cast-name" value={hosts[i]} onChange={(e) => setHosts(hosts.map((h, j) => j === i ? e.target.value : h))} />
              <VoiceSelect voices={state.voices} value={cast[String(i)] || ""} onChange={(v: string) => setCast({ ...cast, [String(i)]: v })} auto="Voice chosen for you" /></div>))}
          {state.can.music && <label className="row gap-s"><input type="checkbox" checked={music} onChange={(e) => setMusic(e.target.checked)} /> Open with a short piece of music</label>}
        </div>}
        <div className="small muted">A 5-minute episode takes a few minutes to write and a few to record on a laptop; longer documents take longer to read.</div>
        <div><Button kind="primary" icon={<PodcastIcon size={14} />} disabled={!ready || (!docs.length && !(paste && text.trim()))} onClick={start}>Make the podcast</Button></div>
      </div>
      <div className="stack-s"><Jobs jobs={jobs} kinds={["podcast"]} render={(r: any) => (
        <div className="stack-s">
          <div><b>{r.params.title}</b><div className="small muted">{r.params.summary}</div></div>
          <AudioClip item={r} />
          <div className="row gap-s wrap small muted"><span>{r.params.parts} parts · {r.params.words} words · made in {Math.round(r.params.took / 60) || 1} min</span>
            {desktop && <a onClick={() => desktop.showItem(r.params.script)}>Show the script</a>}</div>
          {r.lines && <details><summary className="small">Read the script</summary><div className="transcript">{r.lines.map((l: any, i: number) => <div key={i}><b>{l.speaker}:</b> {l.text}</div>)}</div></details>}
        </div>)} /></div>
    </div>
  );
}

// ---------------------------------------------------------------------------------------------- Speak
function Speak({ state, jobs, toModels }: any) {
  const { toast } = useApp();
  const [text, setText] = useState("");
  const [voice, setVoice] = useState("");
  const [cast, setCast] = useState<Record<string, string>>({});
  const [speed, setSpeed] = useState(1);
  const [format, setFormat] = useState("mp3");
  const speakers = useMemo(() => speakersOf(text), [text]);
  const voices = state.voices;
  if (!state.can.tts) return <NeedsModel what="speaking" toModels={toModels} />;
  const start = async () => {
    try { await api.post("/sound/jobs", { kind: "speak", text, voice: voice || undefined, voices: speakers.length ? cast : undefined, speed, format }); }
    catch (e: any) { toast(e.message, "error"); }
  };
  return (
    <div className="grid-2 image-studio">
      <div className="card stack-s">
        <Field label="What should be said?" help={'For a podcast or a dialogue, write one line per turn with the speaker\'s name first: "Host: Welcome…" then "Guest: Thank you…". Each speaker gets a voice.'}>
          <textarea className="input" rows={9} autoFocus value={text} onChange={(e) => setText(e.target.value)}
            placeholder={"A text to read out, or a script:\n\nHost: Welcome to the show.\nGuest: Thank you for having me."} />
        </Field>
        {speakers.length === 0 ? <Field label="Voice"><VoiceSelect voices={voices} value={voice} onChange={setVoice} auto={`Default (${voices.find((v: any) => v.id === state.default_voice)?.name || "first voice"})`} /></Field>
          : <div className="stack-s">
            <div className="field-label">Voices of the {speakers.length} speakers</div>
            {speakers.map((s) => (
              <div key={s} className="row gap-s"><span className="cast-name ellipsis">{s}</span>
                <VoiceSelect voices={voices} value={cast[s] || ""} onChange={(v: string) => setCast({ ...cast, [s]: v })} auto="Chosen for you (a different voice each)" /></div>
            ))}
          </div>}
        <div className="row gap-m wrap">
          <Field label={`Speed: ${speed.toFixed(2)}×`}><input type="range" min={0.7} max={1.4} step={0.05} value={speed} onChange={(e) => setSpeed(Number(e.target.value))} /></Field>
          <Field label="Save as">
            <select className="input narrow" value={format} onChange={(e) => setFormat(e.target.value)}><option value="mp3">MP3</option><option value="wav">WAV</option><option value="m4a">M4A</option></select>
          </Field>
        </div>
        {state.note && <div className="small muted">{state.note}</div>}
        <div><Button kind="primary" icon={<AudioLines size={14} />} disabled={!text.trim()} onClick={start}>{speakers.length ? "Record the script" : "Speak"}</Button></div>
      </div>
      <div className="stack-s"><Jobs jobs={jobs} kinds={["speak", "mix"]} render={(r: any) => <AudioClip item={r} />} /></div>
    </div>
  );
}

// ---------------------------------------------------------------------------------------------- Transcribe
function Transcribe({ state, jobs, toModels }: any) {
  const { toast } = useApp();
  const [language, setLanguage] = useState("");
  const file = useRef<HTMLInputElement>(null);
  if (!state.can.stt) return <NeedsModel what="transcription" toModels={toModels} />;
  const run = async (up: any) => {
    try { await api.post("/sound/jobs", { kind: "transcribe", audio: up.path, language: language || undefined }); } catch (e: any) { toast(e.message, "error"); }
  };
  const pick = async (f?: File) => { if (f) try { run(await api.upload("/sound/upload", f)); } catch (e: any) { toast(e.message, "error"); } };
  return (
    <div className="grid-2 image-studio">
      <div className="card stack-s">
        <p>Turn a recording, or the sound of a video, into text with times: a meeting, an interview, a voice note.</p>
        <div className="row gap-s wrap">
          <Button icon={<Upload size={14} />} onClick={() => file.current?.click()}>Choose a recording or video…</Button>
          <RecordButton small={false} label="Record now" onFile={run} />
          <input ref={file} type="file" accept="audio/*,video/*" hidden onChange={(e) => { pick(e.target.files?.[0]); e.target.value = ""; }} />
        </div>
        <Field label="Language" help="Leave empty to let the model find it. Two letters: en, sw, fr…">
          <input className="input narrow" value={language} maxLength={5} onChange={(e) => setLanguage(e.target.value.trim())} placeholder="auto" />
        </Field>
        <div className="small muted">With {state.models.find((m: any) => m.id === state.defaults.stt)?.name}. {state.models.find((m: any) => m.id === state.defaults.stt)?.kind === "online" ? "The recording is sent to the provider." : "Nothing leaves this computer."}</div>
      </div>
      <div className="stack-s"><Jobs jobs={jobs} kinds={["transcribe"]} render={(r: any) => (
        <div className="stack-s">
          <div className="transcript">{r.text || "(no speech was found)"}</div>
          <div className="row gap-s wrap small muted">
            <span>{r.seconds} s · {r.model} · {r.took} s to write down</span>
            <a onClick={() => { navigator.clipboard?.writeText(r.text); toast("Copied", "ok"); }}><Copy size={12} /> Copy</a>
            {desktop && r.file && <a onClick={() => desktop.showItem(r.file)}>Show the text{r.subtitles ? " and subtitle" : ""} file</a>}
          </div>
        </div>)} /></div>
    </div>
  );
}

// ---------------------------------------------------------------------------------------------- Music
function MusicTab({ state, jobs, toModels }: any) {
  const { toast } = useApp();
  const [prompt, setPrompt] = useState("");
  const [seconds, setSeconds] = useState(30);
  const [instrumental, setInstrumental] = useState(true);
  const [lyrics, setLyrics] = useState("");
  if (!state.can.music) return (
    <Empty icon={<Music size={34} />} title="Choose what makes the music first">
      <p>On this computer: install the music engine and download ACE-Step (about 3.3 GB); music is then made without the internet. Or switch on an online music model (Google's Lyria).</p>
      <Button kind="primary" onClick={toModels}>Set up music</Button>
    </Empty>
  );
  const model = state.models.find((m: any) => m.id === state.defaults.music);
  return (
    <div className="grid-2 image-studio">
      <div className="card stack-s">
        <Field label="Describe the music" help="The style, the instruments, the mood and the tempo.">
          <textarea className="input" rows={4} autoFocus value={prompt} onChange={(e) => setPrompt(e.target.value)} placeholder="e.g. Warm acoustic guitar and soft percussion, hopeful, 90 beats a minute: a bed for a podcast intro." />
        </Field>
        <div className="row gap-m wrap">
          <Field label="About how long (seconds)"><input className="input narrow" type="number" min={10} max={600} value={seconds} onChange={(e) => setSeconds(Number(e.target.value))} /></Field>
          <Field label="Singing">
            <select className="input narrow-auto" value={instrumental ? "no" : "yes"} onChange={(e) => setInstrumental(e.target.value === "no")}><option value="no">No singing</option><option value="yes">With singing</option></select>
          </Field>
        </div>
        {!instrumental && <Field label="Words to sing (optional)" help="Mark the parts: [Verse], [Chorus], [Bridge]"><textarea className="input" rows={4} value={lyrics} onChange={(e) => setLyrics(e.target.value)} /></Field>}
        {model?.kind === "online" ? <div className="small muted"><Cloud size={12} /> Online: the description is sent to {model?.name}; about US${model?.settings?.per_item} per piece from this month's online budget (an estimate: check the provider's prices). The provider marks its music with an inaudible watermark.</div>
          : <div className="small muted"><Cpu size={12} /> On this computer with {model?.name}: nothing leaves it. It plans the piece first and then renders it; on a processor alone expect several minutes for half a minute of music. Between 10 seconds and 10 minutes.</div>}
        <div><Button kind="primary" icon={<Music size={14} />} disabled={!prompt.trim()} onClick={() => api.post("/sound/jobs", { kind: "music", prompt, seconds, instrumental, lyrics }).catch((e) => toast(e.message, "error"))}>Make the music</Button></div>
      </div>
      <div className="stack-s"><Jobs jobs={jobs} kinds={["music"]} render={(r: any) => <><AudioClip item={r} />{r.params?.words && <div className="small muted">{r.params.words}</div>}</>} /></div>
    </div>
  );
}

// ---------------------------------------------------------------------------------------------- Composer
// Recordings laid out on a timeline: drag a clip to move it, cut it, set its volume and fades, listen, save.
function Wave({ path, from, to, seconds }: any) {
  const [peaks, setPeaks] = useState<number[]>([]);
  useEffect(() => { api.get(`/sound/peaks?n=300&path=${encodeURIComponent(path)}`).then((r) => setPeaks(r.peaks)).catch(() => setPeaks([])); }, [path]);
  const a = Math.floor((from / (seconds || 1)) * peaks.length), b = Math.max(a + 1, Math.ceil((to / (seconds || 1)) * peaks.length));
  const part = peaks.slice(a, b), top = Math.max(0.05, ...part);
  return (
    <svg className="wave" viewBox={`0 0 ${Math.max(1, part.length)} 20`} preserveAspectRatio="none">
      {part.map((v, i) => <rect key={i} x={i} y={10 - (v / top) * 9} width={0.7} height={Math.max(0.6, (v / top) * 18)} />)}
    </svg>
  );
}

function Composer({ state }: any) {
  const { toast } = useApp();
  const [tracks, setTracks] = useState<any[]>(composerTracks());
  const [picking, setPicking] = useState<any[] | null>(null);
  const [open, setOpen] = useState("");
  const [busy, setBusy] = useState("");
  const [format, setFormat] = useState("mp3");
  const [name, setName] = useState("My mix");
  const lane = useRef<HTMLDivElement>(null);
  const player = useRef<HTMLAudioElement | null>(null);
  const file = useRef<HTMLInputElement>(null);
  useEffect(() => { saveComposer(tracks); }, [tracks]);
  useEffect(() => { const again = () => setTracks(composerTracks()); window.addEventListener("kimilm-composer", again); return () => { window.removeEventListener("kimilm-composer", again); player.current?.pause(); }; }, []);
  const total = Math.max(10, ...tracks.map((t) => t.start + (t.to - t.from)));
  const set = (id: string, change: any) => setTracks((list) => list.map((t) => t.id === id ? { ...t, ...change } : t));
  const add = async (item: any) => {
    let seconds = item.params?.seconds || 0;
    if (!seconds) try { seconds = (await api.get(`/sound/peaks?n=20&path=${encodeURIComponent(item.path)}`)).seconds; } catch { /* length unknown */ }
    setTracks((list) => [...list, newTrack({ ...item, seconds }, list)]);
  };
  const drag = (t: any) => (e: React.PointerEvent) => {
    const width = lane.current?.getBoundingClientRect().width || 600, x0 = e.clientX, start0 = t.start;
    const move = (ev: PointerEvent) => set(t.id, { start: Math.max(0, Math.round((start0 + ((ev.clientX - x0) / width) * total) * 10) / 10) });
    const up = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); };
    window.addEventListener("pointermove", move); window.addEventListener("pointerup", up);
  };
  const body = (preview: boolean) => ({ kind: "mix", preview, name, format, length: "longest", fade: 0,
    tracks: tracks.map((t) => ({ path: t.path, start: t.start, from: t.from, to: t.to, volume: t.volume, fade_in: t.fade_in, fade_out: t.fade_out, mute: t.mute })) });
  const listen = async () => {
    if (busy === "playing") { player.current?.pause(); setBusy(""); return; }
    setBusy("preview");
    try {
      const r = await soundJob(body(true));
      const a = new Audio(fileUrl(r.path) + `&t=${Date.now()}`);
      player.current = a; a.onended = () => setBusy(""); await a.play(); setBusy("playing");
    } catch (e: any) { toast(e.message, "error"); setBusy(""); }
  };
  const save = async () => {
    setBusy("saving");
    try { const r = await soundJob(body(false)); toast(`Saved “${r.name}” to the Library`, "ok"); } catch (e: any) { toast(e.message, "error"); }
    setBusy("");
  };
  const num = (t: any, key: string, label: string, max?: number) => (
    <Field label={label}><input className="input narrow" type="number" step={0.1} min={0} max={max} value={t[key]} onChange={(e) => set(t.id, { [key]: Math.max(0, Number(e.target.value)) })} /></Field>);
  return (
    <div className="stack-s">
      <div className="card stack-s">
        <div className="row between gap-s wrap">
          <div className="row gap-s wrap">
            <Button small icon={<Plus size={13} />} onClick={() => api.get("/sound/library?limit=100").then((r) => setPicking(r.items.filter((i: any) => i.exists)))}>From the Library</Button>
            <Button small icon={<Upload size={13} />} onClick={() => file.current?.click()}>A file…</Button>
            <RecordButton label="Record" onFile={(up: any) => add({ ...up, name: up.name || "recording" })} />
            <input ref={file} type="file" accept="audio/*,video/*" hidden onChange={async (e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) try { add({ ...(await api.upload("/sound/upload", f)), name: f.name }); } catch (err: any) { toast(err.message, "error"); } }} />
            {tracks.length > 0 && <ConfirmButton small kind="ghost" onConfirm={() => setTracks([])}>Start afresh</ConfirmButton>}
          </div>
          <div className="row gap-s wrap">
            <input className="input narrow-auto" value={name} onChange={(e) => setName(e.target.value)} title="Name of the result" />
            <select className="input narrow" value={format} onChange={(e) => setFormat(e.target.value)}><option value="mp3">MP3</option><option value="wav">WAV</option><option value="m4a">M4A</option></select>
            <Button small icon={busy === "playing" ? <Square size={12} /> : <Play size={13} />} busy={busy === "preview"} disabled={!tracks.length || busy === "saving"} onClick={listen}>{busy === "playing" ? "Stop" : "Listen"}</Button>
            <Button small kind="primary" icon={<Save size={13} />} busy={busy === "saving"} disabled={!tracks.length || busy === "preview"} onClick={save}>Save to the Library</Button>
          </div>
        </div>
        {tracks.length === 0 ? <Empty icon={<Layers size={34} />} title="Put recordings together">
          Add a voice, music or any recording, then move, cut and fade them. Speech, podcasts and music you make in the other tabs open here with the <Layers size={13} /> button. {state.can.music ? "" : "Music can be made on this computer once the music engine is installed (Voices & models)."}
        </Empty> : <>
          <div className="ruler" ref={lane}>{Array.from({ length: 6 }, (_, i) => <span key={i} style={{ left: `${i * 20}%` }}>{Math.round((total * i) / 5)} s</span>)}</div>
          {tracks.map((t) => (
            <div key={t.id} className={`track${t.mute ? " muted-track" : ""}`}>
              <div className="track-head">
                <div className="ellipsis" title={t.name}><b>{t.name}</b></div>
                <div className="row gap-s">
                  <input type="range" min={0} max={1.5} step={0.05} value={t.volume} title={`Volume ${Math.round(t.volume * 100)}%`} onChange={(e) => set(t.id, { volume: Number(e.target.value) })} />
                  <span className="small muted">{Math.round(t.volume * 100)}%</span>
                  <a className="small" onClick={() => set(t.id, { mute: !t.mute })}>{t.mute ? "Unmute" : "Mute"}</a>
                  <a className="small" onClick={() => setOpen(open === t.id ? "" : t.id)}>{open === t.id ? "Done" : "Cut & fade"}</a>
                  <button className="icon-btn" title="Take out" onClick={() => setTracks(tracks.filter((x) => x.id !== t.id))}><X size={14} /></button>
                </div>
              </div>
              <div className="lane">
                <div className="clip" style={{ left: `${(t.start / total) * 100}%`, width: `${Math.max(1, ((t.to - t.from) / total) * 100)}%` }} onPointerDown={drag(t)} title="Drag to move">
                  <Wave path={t.path} from={t.from} to={t.to} seconds={t.seconds} />
                </div>
              </div>
              {open === t.id && <div className="row gap-m wrap">
                {num(t, "start", "Starts at (s)")}{num(t, "from", "Use from (s)", t.seconds)}{num(t, "to", "…to (s)", t.seconds)}{num(t, "fade_in", "Fade in (s)")}{num(t, "fade_out", "Fade out (s)")}
              </div>}
            </div>
          ))}
          <div className="small muted">{tracks.length} track{tracks.length === 1 ? "" : "s"} · {Math.round(Math.max(...tracks.map((t) => t.start + (t.to - t.from))))} seconds. Drag a clip to move it. Music under a voice sounds right at 15 to 25%.</div>
        </>}
      </div>
      {picking && <Modal wide title="Add from the Library" onClose={() => setPicking(null)}>
        {picking.length === 0 && <div className="muted">Nothing in the Library yet.</div>}
        {picking.map((i) => <div key={i.id} className="list-row" onClick={() => { add(i); setPicking(null); }}><div className="grow ellipsis">{i.name}</div><span className="small muted">{i.params?.mode} · {Math.round(i.params?.seconds || 0)} s</span></div>)}
      </Modal>}
    </div>
  );
}

// ---------------------------------------------------------------------------------------------- Library
function Library() {
  const [items, setItems] = useState<any[] | null>(null);
  const load = () => api.get("/sound/library?limit=200").then((r) => setItems(r.items.filter((i: any) => i.exists)));
  useEffect(() => { load(); }, []);
  useEvent((e) => { if (e.kind === "sound_changed") load(); });
  if (!items) return <Spinner />;
  if (!items.length) return <Empty icon={<AudioLines size={34} />} title="No recordings yet">Speech, music and mixes made here or by agents in chats are kept in this list.</Empty>;
  return (
    <div className="card stack-s">
      {items.map((i) => (
        <div key={i.id}>
          <AudioClip item={i} onRemove={<ConfirmButton small kind="ghost" onConfirm={() => api.del(`/sound/library/${i.id}?file=1`).then(load)}><Trash2 size={13} /></ConfirmButton>} />
          <div className="small muted ellipsis" title={i.prompt}>{fmtTime(i.created)}{i.chat_id ? <> · <a onClick={() => go("chat", i.chat_id)}>chat</a></> : null} · {i.prompt}</div>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------------------------- Voices and models
function AddVoice({ onClose }: any) {
  const { toast } = useApp();
  const [name, setName] = useState("");
  const [audio, setAudio] = useState<any>(null);
  const [whose, setWhose] = useState("mine");
  const [confirmed, setConfirmed] = useState(false);
  const [person, setPerson] = useState("");
  const [proof, setProof] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const file = useRef<HTMLInputElement>(null), proofFile = useRef<HTMLInputElement>(null);
  const upload = async (f: File | undefined, set: (x: any) => void) => { if (f) try { set(await api.upload("/sound/upload", f)); } catch (e: any) { toast(e.message, "error"); } };
  const save = async () => {
    setBusy(true);
    try {
      await api.post("/sound/voices", { name, audio: audio?.path, consent: whose === "mine" ? { whose, confirmed } : { whose, person, consent_audio: proof?.path } });
      onClose();
    } catch (e: any) { toast(e.message, "error"); }
    setBusy(false);
  };
  const ok = name.trim() && audio && (whose === "mine" ? confirmed : person.trim() && proof);
  return (
    <Modal title="A voice from a recording" onClose={onClose} footer={<Button kind="primary" busy={busy} disabled={!ok} onClick={save}>Make the voice</Button>}>
      <p className="small muted">10 to 30 seconds of clear speech in a quiet room, one person, no music. The voice then reads English texts on this computer; the recording stays here.</p>
      <Field label="Name of the voice"><input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. My voice" /></Field>
      <Field label="The recording">
        <div className="row gap-s wrap">
          <RecordButton onFile={setAudio} label="Record now" />
          <Button small icon={<Upload size={13} />} onClick={() => file.current?.click()}>Choose a file…</Button>
          <input ref={file} type="file" accept="audio/*,video/*" hidden onChange={(e) => upload(e.target.files?.[0], setAudio)} />
          {audio && <Badge kind="ok">{audio.name || "recorded"}</Badge>}
        </div>
      </Field>
      <Field label="Whose voice is it?">
        <select className="input" value={whose} onChange={(e) => setWhose(e.target.value)}>
          <option value="mine">My own voice</option><option value="other">Another person's voice, with their consent</option>
        </select>
      </Field>
      {whose === "mine" ? <label className="row gap-s"><input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} /> I confirm that this recording is my own voice.</label> : <>
        <Field label="Who is it?"><input className="input" value={person} onChange={(e) => setPerson(e.target.value)} placeholder="Their name" /></Field>
        <Field label="Their consent, recorded" help={'A recording in which the person says that they agree, for example: "I, …, agree that my voice is used in KimiLM by …". It is kept with the voice.'}>
          <div className="row gap-s wrap">
            <RecordButton onFile={setProof} label="Record their consent" />
            <Button small icon={<Upload size={13} />} onClick={() => proofFile.current?.click()}>Choose a file…</Button>
            <input ref={proofFile} type="file" accept="audio/*,video/*" hidden onChange={(e) => upload(e.target.files?.[0], setProof)} />
            {proof && <Badge kind="ok">{proof.name || "recorded"}</Badge>}
          </div>
        </Field>
      </>}
    </Modal>
  );
}

function OnlineCard({ provider, reload }: any) {
  const { toast } = useApp();
  const [key, setKey] = useState("");
  const [role, setRole] = useState(provider.roles[0]);
  const [list, setList] = useState<any>(null);
  const [pick, setPick] = useState("");
  useEffect(() => {
    if (!provider.ready) return;
    setList(null);
    api.get(`/sound/online/${provider.id}/${role}/models`).then((r) => { setList(r); setPick(r.models[0] || ""); }).catch(() => setList({ models: [] }));
  }, [provider.ready, role]);
  return (
    <div className="card stack-s">
      <div className="row between"><b>{provider.name}</b>{provider.ready ? <Badge kind="ok">key saved</Badge> : <Badge>no key yet</Badge>}</div>
      {!provider.ready ? (
        <div className="row gap-s">
          <input className="input" type="password" placeholder={`${provider.name} API key`} value={key} onChange={(e) => setKey(e.target.value)} />
          <Button small kind="primary" disabled={!key.trim()} onClick={async () => { try { await api.put(`/online/providers/${provider.id}/key`, { key }); setKey(""); reload(); } catch (e: any) { toast(e.message, "error"); } }}>Save</Button>
        </div>
      ) : (
        <div className="row gap-s">
          <select className="input narrow-auto" value={role} onChange={(e) => setRole(e.target.value)}>{provider.roles.map((r: string) => <option key={r} value={r}>{ROLE[r]}</option>)}</select>
          {!list ? <Spinner /> : <>
            <input className="input" list={`sound-${provider.id}-${role}`} value={pick} onChange={(e) => setPick(e.target.value)} placeholder="Model" />
            <datalist id={`sound-${provider.id}-${role}`}>{list.models.map((m: string) => <option key={m} value={m} />)}</datalist>
            <Button small icon={<Plus size={13} />} disabled={!pick.trim()} onClick={async () => { try { await api.post("/sound/models", { provider: provider.id, role, remote_id: pick }); reload(); } catch (e: any) { toast(e.message, "error"); } }}>Add</Button>
          </>}
        </div>
      )}
      <div className="small muted">{list?.from === "suggestions" ? "The provider's list could not be read: these are suggestions, type another name if needed. " : ""}The same key as for chat. Texts and recordings you give it are sent to {provider.name}.</div>
    </div>
  );
}

function ModelsTab({ state, reload }: any) {
  const { toast } = useApp();
  const [adding, setAdding] = useState(false);
  const [downloads, setDownloads] = useState<any>({});
  useEvent((e) => { if (e.kind === "sound_download") setDownloads((d: any) => ({ ...d, [e.key]: e })); });
  const mine = state.voices.filter((v: any) => v.kind === "recording");
  const eng = state.music_engine;
  const [engine, setEngine] = useState<any>(null);
  useEvent((e) => { if (e.kind === "music_engine") setEngine(e.action === "installed" ? null : e); });
  const installing = eng.installing || (engine?.action === "progress" ? engine : null);
  return (
    <div className="stack">
      <div className="card stack-s">
        <div className="row between"><h3><UserRound size={16} /> Voices from recordings</h3><Button small icon={<Mic size={13} />} disabled={!state.can.clone} onClick={() => setAdding(true)}>Add a voice</Button></div>
        {!state.can.clone && <div className="small muted">Download “Pocket TTS” below first: it is the model that speaks with a voice from a recording.</div>}
        {mine.length === 0 && state.can.clone && <div className="small muted">None yet. Record 10 to 30 seconds of your own voice and KimiLM can read texts with it.</div>}
        {mine.map((v: any) => (
          <div key={v.id} className="list-row" style={{ cursor: "default" }}>
            <div><b>{v.name}</b> <Badge>{v.whose === "mine" ? "my own voice" : "with recorded consent"}</Badge></div>
            <div className="row gap-s">
              {!v.default && <Button small kind="ghost" icon={<Star size={13} />} title="Use by default" onClick={() => api.put("/sound/settings", { voice: v.id }).then(reload)} />}
              <ConfirmButton small kind="ghost" onConfirm={() => api.del(`/sound/voices/${encodeURIComponent(v.id)}`).then(reload)}><Trash2 size={13} /></ConfirmButton>
            </div>
          </div>
        ))}
        <Field label="Default voice" inline>
          <select className="input narrow-auto" value={state.voices.find((v: any) => v.default)?.id || ""} onChange={(e) => api.put("/sound/settings", { voice: e.target.value }).then(reload).catch((err) => toast(err.message, "error"))}>
            <option value="">Chosen for you</option>
            {state.voices.filter((v: any) => v.ready).map((v: any) => <option key={v.id} value={v.id}>{v.name} · {v.kind === "recording" ? "recording" : v.kind === "online" ? v.model_name : v.language}</option>)}
          </select>
        </Field>
      </div>

      <div className="card stack-s">
        <div className="row between"><h3><Cpu size={16} /> On this computer</h3><span className="small muted">{state.library ? `speech library ${state.library}` : "the speech library is not installed"}</span></div>
        <p className="small muted">Speech and transcription run on the processor, in a process of their own that ends when the work is done. No internet is needed once a model is here.</p>
        {!state.library && <div className="note bad">The speech library is missing. In the engine folder run: .venv\Scripts\python.exe -m pip install -r requirements.txt</div>}
        {state.models.filter((m: any) => m.kind === "local").map((m: any) => (
          <div key={m.id} className="list-row" style={{ cursor: "default" }}>
            <div><b>{m.name}</b> <Badge>{ROLE[m.role]}</Badge> {m.default && <Badge kind="accent">default</Badge>} {!m.ready && <Badge kind="warn">{m.why}</Badge>}
              <div className="small muted">{fmtBytes(m.size)} · {m.licence}</div></div>
            <div className="row gap-s">
              {!m.default && m.role !== "clone" && <Button small kind="ghost" icon={<Star size={13} />} title="Use by default" onClick={() => api.put(`/sound/models/${m.id}`, { default: true }).then(reload)} />}
              <ConfirmButton small kind="ghost" onConfirm={() => api.del(`/sound/models/${m.id}`).then(reload)}><Trash2 size={13} /></ConfirmButton>
            </div>
          </div>
        ))}
        <div className="field-label" style={{ marginTop: 6 }}>Models to download</div>
        {state.catalog.filter((c: any) => !c.installed).sort((a: any, b: any) => Number(b.usable) - Number(a.usable)).map((c: any) => {
          const d = downloads[c.key] || c.download, active = d && ["starting", "downloading", "unpacking"].includes(d.state);
          return (
            <div key={c.key} className="recipe">
              <div className="grow">
                <div className="row gap-s wrap"><b>{c.name}</b><Badge>{ROLE[c.role]}</Badge><span className="small muted">{c.mb >= 1000 ? `${(c.mb / 1000).toFixed(1)} GB` : `${c.mb} MB`}{c.voices ? ` · ${c.voices} voice${c.voices === 1 ? "" : "s"}` : ""}</span>
                  {c.recommended && <Badge kind="accent">a good start</Badge>}{!c.usable && <Badge kind="warn" title={state.note}>not with this speech library</Badge>}
                  {c.usable && !c.fits && <Badge kind="warn">may be too big for this computer</Badge>}</div>
                <div className="small">{c.about}</div>
                <div className="small muted">Licence: {c.licence} · <a href={c.page} target="_blank" rel="noreferrer" onClick={external(c.page)}>about the model <ExternalLink size={11} /></a></div>
                {active && <div className="row gap-s"><Progress value={d.done} total={d.total || 1} /><span className="small muted">{d.state === "unpacking" ? "unpacking…" : `${fmtBytes(d.done)} of ${fmtBytes(d.total)}`}</span></div>}
                {d?.state === "failed" && <div className="small bad">{d.error}</div>}
              </div>
              {active ? <Button small onClick={() => api.del(`/sound/catalog/${c.key}/download`)}>Cancel</Button>
                : c.usable && <Button small icon={<Download size={13} />} onClick={async () => { try { setDownloads({ ...downloads, [c.key]: await api.post(`/sound/catalog/${c.key}/download`) }); } catch (e: any) { toast(e.message, "error"); } }}>Download</Button>}
            </div>
          );
        })}
        {state.note && <div className="small muted">{state.note}</div>}
      </div>

      <div className="card stack-s">
        <div className="row between"><h3><Music size={16} /> Music on this computer</h3>{eng.installed ? <Badge kind="ok">engine ready · {eng.source}</Badge> : <Badge kind="warn">engine not installed</Badge>}</div>
        <p className="small muted">Music is made by ACE-Step 1.5 through the acestep.cpp programs, on the processor or a graphics card, without the internet. Its authors publish no ready-made programs, so KimiLM uses programs built from the published source by your own repository ({eng.repo}): nothing is downloaded from anyone else.</p>
        <div className="row gap-s wrap">
          {!eng.installed && !installing && <Button small kind="primary" icon={<Download size={13} />} onClick={() => { setEngine(null); api.post("/sound/music-engine").then(reload).catch((e) => toast(e.message, "error")); }}>Install the music engine</Button>}
          {installing && <span className="row gap-s"><Progress value={installing.done || 0} total={installing.total || 1} /><span className="small muted">{installing.total ? `${fmtBytes(installing.done || 0)} of ${fmtBytes(installing.total)}` : "starting…"}</span></span>}
          {desktop && <Button small icon={<FolderOpen size={13} />} onClick={async () => { const p = await desktop.pickFolder(); if (p) api.post("/sound/music-engine", { folder: p }).then(reload).catch((e) => toast(e.message, "error")); }}>I have the programs: choose their folder…</Button>}
          {eng.installed && <ConfirmButton small kind="ghost" onConfirm={() => api.del("/sound/music-engine").then(reload)}>Remove</ConfirmButton>}
        </div>
        {engine?.action === "failed" && <div className="small bad">{engine.error} <a href={eng.workflow} target="_blank" rel="noreferrer" onClick={external(eng.workflow)}>Open the workflow <ExternalLink size={11} /></a></div>}
        <div className="small muted">Then download “ACE-Step 1.5 (light)” in the list above. Built from {eng.built_from} at {String(eng.commit).slice(0, 7)}.</div>
      </div>

      <div className="stack-s">
        <h3><Cloud size={16} /> Online</h3>
        {state.models.some((m: any) => m.kind === "online") && <div className="card">{state.models.filter((m: any) => m.kind === "online").map((m: any) => (
          <div key={m.id} className="list-row" style={{ cursor: "default" }}>
            <div><b>{m.name}</b> <Badge>{ROLE[m.role]}</Badge> {m.default && <Badge kind="accent">default</Badge>} {!m.ready && <Badge kind="warn">{m.why}</Badge>}
              <div className="small muted">about US${m.settings?.per_minute ? `${m.settings.per_minute} a minute` : `${m.settings?.per_item} a piece`} (an estimate)</div></div>
            <div className="row gap-s">
              {!m.default && <Button small kind="ghost" icon={<Star size={13} />} title="Use by default" onClick={() => api.put(`/sound/models/${m.id}`, { default: true }).then(reload)} />}
              <ConfirmButton small kind="ghost" onConfirm={() => api.del(`/sound/models/${m.id}`).then(reload)}><Trash2 size={13} /></ConfirmButton>
            </div>
          </div>))}</div>}
        <div className="grid-2 image-online">{state.online.map((p: any) => <OnlineCard key={p.id} provider={p} reload={reload} />)}</div>
        <div className="card stack-s"><div className="row between"><b>Anthropic (Claude)</b><Badge>text and pictures in, text out</Badge></div><div className="small muted">{state.claude}</div></div>
      </div>
      {adding && <AddVoice onClose={() => { setAdding(false); reload(); }} />}
    </div>
  );
}

export default function Sound() {
  const [state, setState] = useState<any>(null);
  const [jobs, setJobs] = useState<any[]>([]);
  const [tab, setTab] = useState(() => { const t = sessionStorage.getItem("kimilm-sound-tab") || ""; sessionStorage.removeItem("kimilm-sound-tab"); return t; });
  useEffect(() => { const to = () => setTab("composer"); window.addEventListener("kimilm-composer", to); return () => window.removeEventListener("kimilm-composer", to); }, []);
  const load = () => api.get("/sound").then((s) => { setState(s); setJobs((j) => j.length ? j : s.jobs); });
  useEffect(() => { load(); }, []);
  useEvent((e) => {
    if (e.kind === "sound_changed" || (e.kind === "sound_download" && e.state !== "downloading")) load();
    if (e.kind === "sound_job") {          // the event's `kind` is its name; the job's own kind travels as `what`
      const job = { ...e, kind: e.what };
      setJobs((list) => list.some((j) => j.id === job.id) ? list.map((j) => j.id === job.id ? job : j) : [...list, job]);
    }
  });
  if (!state) return <div className="page"><Spinner /></div>;
  const current = tab || "podcast";
  return (
    <div className="page">
      <PageHeader title="Sound" subtitle="Turn documents into podcasts, speak texts with many voices or your own, write down recordings, make music. On this computer or with an online model; agents in chats use the same voices." />
      <Tabs value={current} onChange={setTab} tabs={[{ id: "podcast", label: "Podcast" }, { id: "speak", label: "Speak" }, { id: "transcribe", label: "Transcribe" }, { id: "music", label: "Music" },
        { id: "composer", label: "Composer" }, { id: "library", label: "Library" }, { id: "models", label: "Voices & models", count: state.models.length }]} />
      {current === "podcast" && <Podcast state={state} jobs={jobs} toModels={() => setTab("models")} />}
      {current === "speak" && <Speak state={state} jobs={jobs} toModels={() => setTab("models")} />}
      {current === "transcribe" && <Transcribe state={state} jobs={jobs} toModels={() => setTab("models")} />}
      {current === "music" && <MusicTab state={state} jobs={jobs} toModels={() => setTab("models")} />}
      {current === "composer" && <Composer state={state} />}
      {current === "library" && <Library />}
      {current === "models" && <ModelsTab state={state} reload={load} />}
    </div>
  );
}
