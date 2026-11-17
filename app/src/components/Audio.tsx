// Sound in the interface (2.12.0): a player for recordings, recording from the microphone, dictation into a
// text field, and reading a reply aloud.
import React, { useEffect, useRef, useState } from "react";
import { Download, FolderOpen, Loader2, Mic, Square, Volume2 } from "lucide-react";
import { api, desktop, fileUrl, onEvent } from "../api";
import { useApp } from "../store";
import { PictureGrid } from "./Pictures";

const mmss = (s: number) => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`;

export function AudioClip({ item, onRemove }: any) {
  const p = item.params || {};
  const cast = p.voices ? Object.entries(p.voices).map(([who, v]) => who === "Voice" ? v : `${who}: ${v}`).join(", ") : "";
  const facts = [p.mode === "music" ? "music" : p.mode === "mix" ? "mix" : cast || item.model_name || item.model, p.seconds ? mmss(p.seconds) : "",
    p.cost ? `about US$${p.cost}` : "", item.source === "online" ? "made online" : item.source === "mixed" ? "partly online" : ""].filter(Boolean).join(" · ");
  return (
    <div className="audio-clip">
      <audio controls preload="metadata" src={fileUrl(item.path)} />
      <div className="audio-meta">
        <div className="ellipsis" title={item.prompt}>{item.name}</div>
        <div className="small muted ellipsis">{facts}</div>
      </div>
      <a className="icon-btn" href={fileUrl(item.path)} download={item.name} title="Download"><Download size={15} /></a>
      {desktop && <button className="icon-btn" title="Show in folder" onClick={() => desktop.showItem(item.path)}><FolderOpen size={15} /></button>}
      {onRemove}
    </div>
  );
}

// What a tool step made: pictures as thumbnails, recordings as players.
export function MediaStrip({ items }: any) {
  const pictures = (items || []).filter((m: any) => m.kind !== "audio"), clips = (items || []).filter((m: any) => m.kind === "audio");
  if (!pictures.length && !clips.length) return null;
  return (
    <div className="tool-media">
      <PictureGrid items={pictures} large={pictures.length === 1} />
      {clips.map((m: any) => <AudioClip key={m.id || m.path} item={m} />)}
    </div>
  );
}

// What sound can do right now (speech, transcription…), kept current for buttons all over the app.
let canNow: any = null;
const listeners = new Set<(c: any) => void>();
function refreshCan() { api.get("/sound").then((s) => { canNow = s.can; listeners.forEach((l) => l(canNow)); }).catch(() => {}); }
onEvent((e) => { if (e.kind === "sound_changed" && listeners.size) refreshCan(); });
export function useSoundCan() {
  const [can, setCan] = useState<any>(canNow || {});
  useEffect(() => { listeners.add(setCan); if (!canNow) refreshCan(); return () => { listeners.delete(setCan); }; }, []);
  return can;
}

// One job of the sound engine, awaited: resolves with its result, rejects with its message.
export function soundJob(body: any): Promise<any> {
  return new Promise(async (resolve, reject) => {
    let id = "";
    const off = onEvent((e) => {
      if (e.kind !== "sound_job" || e.id !== id || e.state === "running") return;
      off();
      if (e.state === "done") resolve(e.result); else reject(new Error(e.error || "Stopped"));
    });
    try { id = (await api.post("/sound/jobs", body)).id; } catch (err) { off(); reject(err); }
  });
}

export function useRecorder() {
  const [recording, setRecording] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const rec = useRef<MediaRecorder | null>(null);
  const done = useRef<((f: File | null) => void) | null>(null);
  useEffect(() => {
    if (!recording) return;
    const t = setInterval(() => setSeconds((s) => s + 1), 1000);
    return () => clearInterval(t);
  }, [recording]);
  const start = async () => {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const chunks: BlobPart[] = [];
    const r = new MediaRecorder(stream);
    r.ondataavailable = (e) => chunks.push(e.data);
    r.onstop = () => {
      stream.getTracks().forEach((t) => t.stop());
      const type = r.mimeType || "audio/webm";
      done.current?.(chunks.length ? new File(chunks, `recording.${type.includes("ogg") ? "ogg" : type.includes("mp4") ? "m4a" : "webm"}`, { type }) : null);
    };
    rec.current = r; setSeconds(0); setRecording(true); r.start();
  };
  const stop = () => new Promise<File | null>((resolve) => { done.current = resolve; setRecording(false); rec.current?.stop(); });
  return { recording, seconds, start, stop };
}

// Record → the recording as a file on the engine's side.
export function RecordButton({ onFile, label = "Record", small = true }: any) {
  const { toast } = useApp();
  const r = useRecorder();
  const click = async () => {
    try {
      if (!r.recording) return await r.start();
      const file = await r.stop();
      if (file) onFile(await api.upload("/sound/upload", file));
    } catch (e: any) { toast(e.name === "NotAllowedError" ? "The microphone is not allowed for this app" : e.message, "error"); }
  };
  return (
    <button className={`btn btn-${r.recording ? "primary" : "default"}${small ? " btn-sm" : ""}`} onClick={click}>
      {r.recording ? <Square size={13} /> : <Mic size={13} />}<span>{r.recording ? `Stop (${mmss(r.seconds)})` : label}</span>
    </button>
  );
}

// Speak instead of typing: shown when a transcription model is set up.
export function DictateButton({ onText }: any) {
  const { toast } = useApp();
  const can = useSoundCan();
  const r = useRecorder();
  const [busy, setBusy] = useState(false);
  if (!can.stt) return null;
  const click = async () => {
    try {
      if (!r.recording) return await r.start();
      const file = await r.stop();
      if (!file) return;
      setBusy(true);
      const up = await api.upload("/sound/upload", file);
      const result = await soundJob({ kind: "transcribe", audio: up.path });
      if (result.text) onText(result.text); else toast("No speech was heard", "info");
    } catch (e: any) { toast(e.name === "NotAllowedError" ? "The microphone is not allowed for this app" : e.message, "error"); }
    setBusy(false);
  };
  return (
    <button className={`icon-btn${r.recording ? " recording" : ""}`} title={r.recording ? "Stop and write down what was said" : "Dictate"} onClick={click} disabled={busy}>
      {busy ? <Loader2 size={16} className="spin" /> : r.recording ? <Square size={15} /> : <Mic size={16} />}
    </button>
  );
}

function plainText(markdown: string) {
  return (markdown || "").replace(/```[\s\S]*?```/g, " ").replace(/`([^`]*)`/g, "$1").replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/^\s*\|.*\|\s*$/gm, " ").replace(/^[#>\-*+]+\s*/gm, "").replace(/[*_~]/g, "").replace(/\s+\n/g, "\n").trim().slice(0, 6000);
}

// Read a reply aloud with the default voice: shown when a voice is set up.
export function ReadAloud({ text }: { text: string }) {
  const { toast } = useApp();
  const can = useSoundCan();
  const [state, setState] = useState<"idle" | "making" | "playing">("idle");
  const audio = useRef<HTMLAudioElement | null>(null);
  useEffect(() => () => { audio.current?.pause(); }, []);
  if (!can.tts || !plainText(text)) return null;
  const click = async () => {
    if (state === "playing") { audio.current?.pause(); setState("idle"); return; }
    if (state === "making") return;
    setState("making");
    try {
      const result = await soundJob({ kind: "speak", lines: [{ text: plainText(text) }], name: "read aloud" });
      const a = new Audio(fileUrl(result.path));
      audio.current = a; a.onended = () => setState("idle");
      await a.play(); setState("playing");
    } catch (e: any) { toast(e.message, "error"); setState("idle"); }
  };
  return (
    <button className="icon-btn" title={state === "playing" ? "Stop reading" : "Read aloud"} onClick={click}>
      {state === "making" ? <Loader2 size={13} className="spin" /> : state === "playing" ? <Square size={12} /> : <Volume2 size={13} />}
    </button>
  );
}
