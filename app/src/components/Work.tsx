// What a task is doing, behind the scenes (2.14.0). Every piece of background work is a task: a chat reply, a
// scheduled or long run, a picture, a recording, a video, a download, an install. A task keeps a trail (each step with
// its time, and what a program printed), a plan (what it will do, with what, and what is known so far) and a sign of
// life, so that "busy with a long step" and "stuck" can be told apart by looking.
import React, { useEffect, useRef, useState } from "react";
import { AlertTriangle, CheckCircle2, Circle, Clock, Eye, Loader2, ShieldCheck, Square, XCircle } from "lucide-react";
import { api, fileUrl } from "../api";
import { go, useEvent } from "../store";
import { Badge, Button, Modal, Spinner } from "./ui";
import { MediaStrip } from "./Audio";

export const ACTIVE = ["queued", "running", "waiting"];
export const KIND_NAME: any = {
  chat: "chat", agent: "agent", schedule: "scheduled", pipeline: "pipeline", plugin: "plugin", mission: "long task", action: "plugin action", eval: "evaluation",
  images: "picture", sound: "sound", video: "video", download: "download", install: "install",
};
export const STATUS_ICON: any = {
  running: <Loader2 size={15} className="spin accent" />, queued: <Clock size={15} className="muted" />,
  waiting: <ShieldCheck size={15} className="warn" />, done: <CheckCircle2 size={15} className="ok" />,
  failed: <XCircle size={15} className="bad" />, cancelled: <Square size={13} className="muted" />, interrupted: <XCircle size={15} className="muted" />,
};
export const STATUS_NAME: any = { running: "running", queued: "waiting to start", waiting: "needs your approval", done: "finished", failed: "failed", cancelled: "stopped", interrupted: "cut off" };

export const span = (s: number) => {
  s = Math.max(0, Math.round(s));
  return s < 60 ? `${s} s` : s < 3600 ? `${Math.floor(s / 60)} min ${s % 60} s` : `${Math.floor(s / 3600)} h ${Math.floor((s % 3600) / 60)} min`;
};
const stamp = (iso?: string) => { const d = iso ? new Date(iso) : null; return d && !isNaN(+d) ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : ""; };
const secondsBetween = (a?: string, b?: string) => (a && b ? (+new Date(b) - +new Date(a)) / 1000 : 0);

// After how long without a sign of life a running task is called quiet, and possibly stuck.
export const QUIET = 20, STUCK = 90;

export function useTick(on = true, every = 1000) {
  const [, set] = useState(0);
  useEffect(() => { if (!on) return; const t = setInterval(() => set((n) => n + 1), every); return () => clearInterval(t); }, [on, every]);
}

// A running task's pulse: "working" while it reports, then how long it has been quiet.
// `pace` is what the work itself says about its silences (2.15.0): a drawing step on a processor takes minutes, and
// the engine says nothing until the step is done. Silence within about one and a half such steps is "working";
// it is called possibly stuck only after three of them.
export function Pulse({ seenAt, running, long, now, pace }: { seenAt?: number | null; running: boolean; long?: boolean; now?: string; pace?: number | null }) {
  useTick(running);
  if (!running || !seenAt) return null;
  const quiet = (Date.now() - seenAt) / 1000;
  const calm = pace ? pace * 1.5 + 30 : QUIET, stuck = pace ? pace * 3 + 90 : STUCK;
  // work that goes one at a time says when it waits its turn: quiet then is expected, not a sign of trouble
  if ((now || "").startsWith("Waiting for")) return <span className="pulse pulse-quiet" title="It starts when the work before it is finished"><i />waiting its turn{quiet >= QUIET ? ` for ${span(quiet)}` : ""}</span>;
  if (quiet < QUIET) return <span className="pulse pulse-ok" title="It reported in the last seconds"><i />working</span>;
  if (quiet < calm) return <span className="pulse pulse-ok" title="The program says nothing while it works on a step. This long a silence is normal here."><i />working on a step · {span(quiet)} so far, a step takes about {span(pace || 0)}</span>;
  if (quiet < stuck) return <span className="pulse pulse-quiet" title="Time since its last sign of life"><i />no news for {span(quiet)}{pace ? ` (a step takes about ${span(pace)})` : ""}</span>;
  return (
    <span className="pulse pulse-stuck" title="Some steps are silent for a while (loading a large model, a slow processor). If this keeps growing, it is probably stuck: stop it and try again.">
      <AlertTriangle size={13} />no sign of life for {span(quiet)}{long ? ": it may be stuck" : ""}
    </span>
  );
}

// One task, kept live: the row, its trail, its plan, its last sign of life.
export function useTask(id?: string | null) {
  const [job, setJob] = useState<any>(null);
  const [trail, setTrail] = useState<any[]>([]);
  const [detail, setDetail] = useState<any>({});
  const [now, setNow] = useState<string>("");
  const [fraction, setFraction] = useState<number | null>(null);
  const [seenAt, setSeenAt] = useState<number | null>(null);
  const [pace, setPace] = useState<number | null>(null);
  const load = () => {
    if (!id) return;
    api.get(`/jobs/${id}`).then((j) => {
      setJob(j); setTrail(j.trail || []); setDetail(j.detail || {}); setNow(j.progress || ""); setFraction(j.fraction ?? null);
      setPace(j.pace ?? null);
      setSeenAt(j.quiet == null ? null : Date.now() - j.quiet * 1000);
    }).catch(() => setJob({ id, missing: true }));
  };
  useEffect(() => { setJob(null); setTrail([]); setDetail({}); setNow(""); setFraction(null); load(); }, [id]);
  useEvent((e) => {
    if (!id || e.id !== id) return;
    if (e.kind === "job_trail") { setTrail((t) => [...t, e.entry].slice(-800)); setSeenAt(Date.now()); if (e.entry.kind === "step") { setNow(e.entry.text); setFraction(null); setPace(null); } }
    if (e.kind === "job_detail") { setDetail(e.detail || {}); setSeenAt(Date.now()); }
    if (e.kind === "job_progress") { if (e.text) setNow(e.text); setSeenAt(Date.now()); if (e.fraction != null) setFraction(e.fraction); setPace(e.pace ?? null); }
    if (e.kind === "job") load();
  }, [id]);
  return { job, trail, detail, now, fraction, seenAt, pace, reload: load };
}

const LABEL: any = {
  picture: "The picture", picture_run: "How it is made", pictures: "The pictures", narration: "The narration", clip: "The clip", edit: "The change",
  storyboard: "The scenes to write", speech: "The speech", transcription: "The transcription", transcript: "What was said",
  music: "The music", music_plan: "What the model planned", mix: "The mix", podcast: "The podcast", video: "The video", video_run: "How it is made",
  written: "Written so far",
};
const nice = (k: string) => (k.charAt(0).toUpperCase() + k.slice(1)).replace(/_/g, " ");
function value(v: any): React.ReactNode {
  if (v == null || v === "") return null;
  if (Array.isArray(v)) return v.every((x) => typeof x !== "object") ? v.join(", ") : <div className="work-sub">{v.map((x, i) => <div key={i}>{value(x)}</div>)}</div>;
  if (typeof v === "object") return <span>{Object.entries(v).filter(([, x]) => x != null && x !== "").map(([k, x]) => `${nice(k).toLowerCase()}: ${typeof x === "object" ? JSON.stringify(x) : x}`).join(" · ")}</span>;
  if (typeof v === "boolean") return v ? "yes" : "no";
  return String(v);
}
function Facts({ title, data }: { title: string; data: any }) {
  const rows = Object.entries(data || {}).filter(([, v]) => v != null && v !== "" && !(Array.isArray(v) && !v.length));
  if (!rows.length) return null;
  return (
    <div className="work-block">
      <div className="work-h">{title}</div>
      <div className="work-facts">{rows.map(([k, v]) => <React.Fragment key={k}><span className="muted">{nice(k)}</span><span>{value(v)}</span></React.Fragment>)}</div>
    </div>
  );
}

// The plan and what is known so far, in words: the stages, then one block per part of the work.
export function WorkPlan({ detail, running }: { detail: any; running: boolean }) {
  const d = detail || {};
  const stages: string[] = d.stages || [];
  const at = typeof d.stage === "number" ? d.stage : -1;
  const blocks = Object.keys(d).filter((k) => !["stages", "stage", "episode", "script", "notes"].includes(k) && d[k] && typeof d[k] === "object" && !Array.isArray(d[k]));
  return (
    <>
      {stages.length > 0 && (
        <div className="work-block">
          <div className="work-h">Plan</div>
          <div className="work-stages">
            {stages.map((s, i) => {
              const state = !running ? "done" : i < at ? "done" : i === at ? "now" : "todo";
              return <div key={s} className={`work-stage work-stage-${state}`}>{state === "done" ? <CheckCircle2 size={14} className="ok" /> : state === "now" ? <Loader2 size={14} className="spin accent" /> : <Circle size={14} className="muted" />}{s}</div>;
            })}
          </div>
        </div>
      )}
      {blocks.map((k) => <Facts key={k} title={LABEL[k] || nice(k)} data={d[k]} />)}
      {d.episode && (
        <div className="work-block">
          <div className="work-h">The episode’s outline</div>
          <div><b>{d.episode.title}</b>{d.episode.summary ? <span className="muted"> · {d.episode.summary}</span> : null}</div>
          <ol className="work-outline">{(d.episode.parts || []).map((p: any, i: number) => (
            <li key={i} className={d.written && i < d.written.parts ? "ok-text" : ""}><b>{p.title}</b>{p.points?.length ? <ul>{p.points.map((x: string, j: number) => <li key={j} className="small muted">{x}</li>)}</ul> : null}</li>
          ))}</ol>
        </div>
      )}
      {Array.isArray(d.notes) && d.notes.length > 0 && (
        <details className="work-block"><summary className="work-h">Notes taken from the document ({d.notes.length})</summary>
          <ul className="work-notes">{d.notes.map((n: string, i: number) => <li key={i} className="small">{n}</li>)}</ul></details>
      )}
      {Array.isArray(d.script) && d.script.length > 0 && (
        <details className="work-block" open={running}><summary className="work-h">The script so far ({d.script.length} turns)</summary>
          <div className="work-script">{d.script.map((l: any, i: number) => <div key={i} className="small"><b>{l.speaker}:</b> {l.text}</div>)}</div></details>
      )}
    </>
  );
}

// The trail: each step with the time it started and how long it took; notes and errors in between.
export function WorkTrail({ trail, running }: { trail: any[]; running: boolean }) {
  useTick(running);
  const steps = trail.filter((e) => e.kind !== "output");
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => { if (running && box.current) box.current.scrollTop = box.current.scrollHeight; }, [steps.length]);   // the list only, not the page
  if (!steps.length) return <div className="small muted">Nothing has been written to the trail yet.</div>;
  return (
    <div className="work-trail" ref={box}>
      {steps.map((e, i) => {
        // a step lasts until the next step; the last one until now (running) or until the trail's last line
        const next = steps.slice(i + 1).find((x) => x.kind === "step");
        const last = !next;
        const took = e.kind !== "step" ? 0 : next ? secondsBetween(e.t, next.t)
          : running ? (Date.now() - +new Date(e.t)) / 1000 : secondsBetween(e.t, steps[steps.length - 1].t);
        return (
          <div key={i} className={`work-step work-${e.kind}${last && running && e.kind === "step" ? " work-current" : ""}`}>
            <span className="work-t">{stamp(e.t)}</span>
            <span className="work-text">{e.text}</span>
            <span className="work-took">{took >= 1 ? span(took) : ""}</span>
          </div>
        );
      })}
    </div>
  );
}

// What the program itself printed (the image engine, the music programs, the sound worker).
export function WorkOutput({ trail, running }: { trail: any[]; running: boolean }) {
  const lines = trail.filter((e) => e.kind === "output");
  const box = useRef<HTMLPreElement>(null);
  useEffect(() => { if (running && box.current) box.current.scrollTop = box.current.scrollHeight; }, [lines.length]);
  if (!lines.length) return null;
  return (
    <details className="work-block" open={running}>
      <summary className="work-h">What the program printed ({lines.length} lines)</summary>
      <pre className="work-output" ref={box}>{lines.slice(-300).map((e) => `${stamp(e.t)}  ${e.text}`).join("\n")}</pre>
    </details>
  );
}

// A task, opened: what it is, where it is, the plan, the trail, the program's own words, the result.
export function WorkDetail({ id, onClose }: { id: string; onClose: () => void }) {
  const { job, trail, detail, now, fraction, seenAt, pace } = useTask(id);
  const running = !!job && ACTIVE.includes(job.status);
  useTick(running);
  if (!job) return <Modal wide title="Task" onClose={onClose}><Spinner /></Modal>;
  if (job.missing) return <Modal wide title="Task" onClose={onClose}><div className="muted">This task is no longer in the list.</div></Modal>;
  const began = job.started || job.created;
  const took = began ? (running ? (Date.now() - +new Date(began)) / 1000 : secondsBetween(began, job.finished)) : 0;
  const result = job.result || {};
  const files: string[] = (result.files || []).filter(Boolean);
  return (
    <Modal wide title={job.title} onClose={onClose} footer={<>
      {job.chat_id && <Button onClick={() => { onClose(); go("chat", job.chat_id); }}>Open the chat</Button>}
      {running && <Button kind="danger" onClick={() => api.post(`/jobs/${job.id}/cancel`)}>Stop</Button>}
      <Button kind="primary" onClick={onClose}>Close</Button>
    </>}>
      <div className="work">
        <div className="row gap-s wrap">
          {STATUS_ICON[job.status] || STATUS_ICON.queued}<b>{STATUS_NAME[job.status] || job.status}</b>
          <Badge>{KIND_NAME[job.kind] || job.kind}</Badge>
          {job.harness && <Badge>{job.harness}</Badge>}
          <span className="small muted">{began ? `started ${stamp(began)}` : ""}{took >= 1 ? ` · ${running ? "running for" : "took"} ${span(took)}` : ""}{job.model ? ` · ${job.model}` : ""}</span>
          <Pulse seenAt={seenAt} running={job.status === "running"} long now={now} pace={pace} />
        </div>
        {running && now && <div className="work-now"><Loader2 size={14} className="spin accent" /><span>{now}</span></div>}
        {running && fraction != null && <div className="run-bar"><span style={{ width: `${Math.max(2, Math.round(fraction * 100))}%` }} /></div>}
        {job.status === "waiting" && <div className="work-now"><ShieldCheck size={14} className="warn" /><span>It waits for your approval{job.chat_id ? ": open the chat to answer." : "."}</span></div>}
        {job.error && <div className="bad small">{job.error}</div>}
        <WorkPlan detail={detail} running={running} />
        <div className="work-block">
          <div className="work-h">What it did, step by step</div>
          <WorkTrail trail={trail} running={running} />
        </div>
        <WorkOutput trail={trail} running={running} />
        {(result.media?.length > 0 || files.length > 0 || result.text) && (
          <div className="work-block">
            <div className="work-h">Result</div>
            <MediaStrip items={result.media} />
            {result.text && <div className="small work-script">{result.text}</div>}
            {files.map((f) => <div key={f} className="small"><a href={fileUrl(f)} target="_blank" rel="noreferrer">{f.split(/[\\/]/).pop()}</a></div>)}
          </div>
        )}
        {job.log && <details className="work-block"><summary className="work-h">Log</summary><pre className="work-output">{job.log}</pre></details>}
      </div>
    </Modal>
  );
}

// Inside a job card (Images, Sound, Video): how long it has been on this step, whether it still reports, and a way
// to open the whole task.
export function WorkLine({ jobId, running, since }: { jobId?: string; running: boolean; since?: number }) {
  const [open, setOpen] = useState(false);
  const [seenAt, setSeenAt] = useState<number | null>(since || Date.now());
  const [stepAt, setStepAt] = useState<number>(Date.now());
  const [pace, setPace] = useState<number | null>(null);
  useTick(running);
  useEvent((e) => {
    if (!jobId || e.id !== jobId) return;
    if (e.kind === "job_trail" || e.kind === "job_progress" || e.kind === "job_detail") setSeenAt(Date.now());
    if (e.kind === "job_trail" && e.entry.kind === "step") { setStepAt(Date.now()); setPace(null); }
    if (e.kind === "job_progress") setPace(e.pace ?? null);
  }, [jobId]);
  if (!jobId) return null;
  return (
    <span className="work-line">
      {running && <span className="small muted">{span((Date.now() - stepAt) / 1000)} on this step</span>}
      <Pulse seenAt={seenAt} running={running} pace={pace} />
      <button className="link" onClick={() => setOpen(true)}><Eye size={13} /> {running ? "What is it doing?" : "What it did"}</button>
      {open && <WorkDetail id={jobId} onClose={() => setOpen(false)} />}
    </span>
  );
}
