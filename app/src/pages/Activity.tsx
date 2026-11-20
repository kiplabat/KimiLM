import React, { useEffect, useState } from "react";
import { Activity as ActivityIcon, Ban, CheckCircle2, ChevronDown, ChevronRight, Clock, Cpu, Eye, Loader2, Play, Square, Workflow, X, XCircle } from "lucide-react";
import { api, fmtTime } from "../api";
import { go, useApp, useEvent, useShown } from "../store";
import { Badge, Button, Empty, PageHeader, Spinner, Tabs } from "../components/ui";
import { ACTIVE, KIND_NAME, PauseButton, PausedMark, Pulse, STATUS_ICON, WorkDetail, span, useTick } from "../components/Work";

const kTokens = (n: number) => !n ? "0" : n >= 10000 ? `${Math.round(n / 1000)}k` : n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);
const dur = (s: number) => s < 90 ? `${Math.max(0, Math.round(s))} s` : s < 5400 ? `${Math.round(s / 60)} min` : `${(s / 3600).toFixed(1)} h`;
const RUN_LABEL: any = { done: "finished", running: "running", stopped: "stopped", interrupted: "cut off", superseded: "replaced", dismissed: "dismissed" };
const STEP_ICON: any = {
  done: <CheckCircle2 size={14} className="ok" />, failed: <XCircle size={14} className="bad" />, cancelled: <Ban size={14} className="muted" />,
  running: <Loader2 size={14} className="spin accent" />, review: <Loader2 size={14} className="spin accent" />,
};

// The programs agents run (2.19.0): code, commands and pipelines, each with what it started. A program ends with
// its task, at its time limit, or here.
function Programs({ jobs, onOpen }: { jobs: any[]; onOpen: (id: string) => void }) {
  const [rows, setRows] = useState<any[]>([]);
  const load = () => api.get("/procs").then((r) => setRows(r.procs)).catch(() => {});
  const shown = useShown();          // asked only while you see it, and afresh when you come back (2.21.0)
  useEffect(() => { if (!shown) return; load(); const t = setInterval(load, 4000); return () => clearInterval(t); }, [shown]);
  if (!rows.length) return null;
  const title = (id: string) => jobs.find((j) => j.id === id)?.title;
  return (
    <>
      <div className="work-h"><Cpu size={14} /> Programs agents are running ({rows.length})</div>
      <div className="card">
        {rows.map((r) => (
          <div key={r.pid} className="list-row" style={{ cursor: "default" }}>
            <div><b>{r.kind === "python" ? "Python" : r.kind === "command" ? "Command" : r.kind}</b> <span className="small muted">{r.label}</span>
              <div className="small muted">{r.processes} process{r.processes === 1 ? "" : "es"} · {dur(r.cpu_seconds)} of processor time · {r.memory_mb} MB · since {fmtTime(r.since)}
                {r.owner && title(r.owner) ? <> · for <button className="link" onClick={() => onOpen(r.owner)}>{title(r.owner)}</button></> : null}</div></div>
            <Button small kind="danger" icon={<Square size={12} />} onClick={() => api.del(`/procs/${r.pid}`).then(load).catch(load)} title="End it and every program it started: the agent is told it failed">End</Button>
          </div>
        ))}
      </div>
    </>
  );
}

// One run, opened: every step with how long it took and what it read, took from the cache and wrote. The bar is
// the step's share of the run's model time, so the slow step is visible at a glance.
function RunTimeline({ id, onChanged }: { id: string; onChanged: () => void }) {
  const [run, setRun] = useState<any>(null);
  const [showLog, setShowLog] = useState(false);
  useEffect(() => { api.get(`/runs/${id}`).then(setRun).catch(() => {}); }, [id]);
  if (!run) return <div className="run-detail"><Spinner /></div>;
  const longest = Math.max(1, ...run.steps.map((s: any) => s.used?.seconds || 0));
  return (
    <div className="run-detail">
      {run.request && <div className="small muted clamp-2" title={run.request}>{run.request}</div>}
      <div className="run-steps">
        {run.steps.map((s: any, i: number) => {
          const u = s.used || {};
          return (
            <div key={s.id} className="run-step" onClick={() => s.chat_id && go("chat", s.chat_id)} title={s.chat_id ? "Open this step's agent chat" : undefined}>
              <span className="run-step-n">{i + 1}</span>
              <div className="grow">
                <div className="row gap-s">{STEP_ICON[s.status] || <Clock size={14} className="muted" />}<b>{s.name}</b>
                  <span className="small muted">{s.partial ? "kept as best attempt" : s.status === "cancelled" ? "not run" : s.status}{s.attempts > 1 ? ` · ${s.attempts} rounds` : ""} · {s.harness}{s.model_name ? ` · ${s.model_name}` : ""}</span></div>
                {u.calls ? <>
                  <div className="run-bar"><span style={{ width: `${Math.max(2, 100 * (u.seconds || 0) / longest)}%` }} /></div>
                  <div className="small muted">{dur(u.seconds || 0)} · read {kTokens(u.read || 0)} · from cache {kTokens(u.cached || 0)} · wrote {kTokens(u.written || 0)} · {u.calls} model call{u.calls === 1 ? "" : "s"}{u.tools ? ` · ${u.tools} tool${u.tools === 1 ? "" : "s"}` : ""}
                    {s.forecast?.tokens ? ` · forecast ${kTokens(s.forecast.tokens)} tokens, ${dur(s.forecast.seconds || 0)}` : ""}</div>
                </> : null}
                {s.kit?.length ? <div className="small muted">tools: {s.kit.join(", ")}</div> : null}
                {s.note && <div className="small muted clamp-2">{s.note}</div>}
              </div>
            </div>
          );
        })}
      </div>
      <div className="row gap-s wrap">
        <Button small onClick={() => go("chat", run.chat_id)}>Open the chat</Button>
        {run.can_continue && <Button small icon={<Play size={13} />} onClick={async () => { await api.post(`/chats/${run.chat_id}/run/resume`); onChanged(); go("chat", run.chat_id); }}>Continue run</Button>}
        {run.log?.length > 0 && <Button small kind="ghost" onClick={() => setShowLog(!showLog)}>{showLog ? "Hide" : "Show"} the planner's log ({run.log.length})</Button>}
      </div>
      {showLog && <div className="orch-log">{run.log.map((l: any, i: number) => <div key={i} className="small"><span className="muted">{Math.round(l.at)}s</span> {l.text}</div>)}</div>}
    </div>
  );
}

function Runs() {
  const [runs, setRuns] = useState<any[] | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const load = () => api.get("/runs").then((r) => setRuns(r.runs)).catch(() => setRuns([]));
  useEffect(() => { load(); }, []);
  useEvent((e) => { if (e.kind === "job") load(); });
  if (!runs) return <Spinner />;
  if (!runs.length) return <Empty icon={<Workflow size={32} />} title="No planned runs yet">When a request is done in several steps, the run is kept here with what each step cost.</Empty>;
  return (
    <div className="card">
      {runs.map((r) => (
        <div key={r.id}>
          <div className="job-row" style={{ cursor: "pointer" }} onClick={() => setOpen(open === r.id ? null : r.id)}>
            {open === r.id ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
            <div className="grow">
              <div className="row gap-s"><b className="ellipsis">{r.title || "Chat"}</b><Badge kind={r.status === "done" && !r.failed ? "ok" : undefined}>{RUN_LABEL[r.status] || r.status}</Badge>
                {r.failed > 0 && <Badge kind="warn">{r.failed} failed</Badge>}{r.incomplete > 0 && <Badge kind="warn">{r.incomplete} incomplete</Badge>}</div>
              <div className="small muted">{fmtTime(r.created)} · {r.done} of {r.total} steps · {r.rounds} round{r.rounds === 1 ? "" : "s"} · {dur(r.seconds)} of model time · read {kTokens(r.read)} · from cache {kTokens(r.cached)}{r.read + r.cached ? ` (${Math.round(100 * r.cached / (r.read + r.cached))}%)` : ""} · wrote {kTokens(r.written)}</div>
            </div>
          </div>
          {open === r.id && <RunTimeline id={r.id} onChanged={load} />}
        </div>
      ))}
    </div>
  );
}

// What each kind of step has done on each model on this computer: the record model choice learns from.
function Records() {
  const [rows, setRows] = useState<any[] | null>(null);
  useEffect(() => { api.get("/models/records").then((r) => setRows(r.records)).catch(() => setRows([])); }, []);
  if (!rows) return <Spinner />;
  if (!rows.length) return <Empty icon={<Workflow size={32} />} title="Nothing measured yet">After a few planned runs, this shows how often each kind of step passed on each model, and how long it took.</Empty>;
  return (
    <div className="card">
      <p className="muted small">A step whose model nobody fixed runs on the model that did such steps best here: a model that passes under half the time gives way to one that passes clearly more often, and a reliable model gives way to an equally reliable one that is much faster. A model needs three judged steps of a kind before it is compared.</p>
      <div className="rec-row rec-head small muted"><span>Kind of step</span><span>Model</span><span>Passed</span><span>Rounds</span><span>Time per step</span><span>Reads / writes</span></div>
      {rows.map((r, i) => (
        <div key={i} className="rec-row small">
          <span>{r.harness}</span><span className="ellipsis">{r.model_name}</span>
          <span>{r.judged ? <><b>{r.passed} of {r.judged}</b> ({Math.round(100 * r.passed / r.judged)}%)</> : <span className="muted">not judged yet</span>}</span>
          <span>{r.rounds || "–"}</span><span>{r.seconds ? dur(r.seconds) : "–"}</span>
          <span>{r.read_tps || "–"} / {r.write_tps || "–"} tokens a second</span>
        </div>
      ))}
    </div>
  );
}

// Which tasks a chip shows. Everything that works in the background is a task: nothing runs out of sight.
const GROUPS: { id: string; label: string; kinds?: string[] }[] = [
  { id: "all", label: "Everything" },
  { id: "chat", label: "Chats and agents" },             // everything that is none of the kinds below
  { id: "schedule", label: "Scheduled", kinds: ["schedule"] },
  { id: "images", label: "Pictures", kinds: ["images"] },
  { id: "sound", label: "Sound", kinds: ["sound"] },
  { id: "video", label: "Video", kinds: ["video"] },
  { id: "setup", label: "Downloads and installs", kinds: ["download", "install"] },
];

function TaskRow({ j, live, onOpen }: { j: any; live: any; onOpen: () => void }) {
  const { toast } = useApp();
  const running = ACTIVE.includes(j.status);
  useTick(running);
  const began = j.started || j.created;
  const took = began ? (running ? (Date.now() - +new Date(began)) / 1000 : j.finished ? (+new Date(j.finished) - +new Date(began)) / 1000 : 0) : 0;
  const now = live?.text || j.progress;
  const fraction = live && "fraction" in live ? live.fraction : j.fraction;
  return (
    <div className={`job-row job-open${running ? " job-live" : ""}`} onClick={onOpen} title="Open: the plan, every step with its time, and what the program printed">
      {STATUS_ICON[j.status] || STATUS_ICON.queued}
      <div className="grow">
        <div className="row gap-s"><b className="ellipsis">{j.title}</b><Badge>{KIND_NAME[j.kind] || j.kind}</Badge>
          {j.status === "waiting" && <Badge kind="warn">needs your approval</Badge>}
          {j.status === "running" && j.paused && <PausedMark />}
          {j.status === "running" && !j.paused && <Pulse seenAt={live?.seenAt ?? (j.quiet == null ? null : j.loaded - j.quiet * 1000)} running now={now} pace={live && "pace" in live ? live.pace : j.pace} />}</div>
        {running && now ? <div className="small job-now">{now}</div> : null}
        {running && fraction != null ? <div className="run-bar"><span style={{ width: `${Math.max(2, Math.round(fraction * 100))}%` }} /></div> : null}
        <div className="small muted">{fmtTime(j.created)}{took >= 1 ? ` · ${running ? "running for" : "took"} ${span(took)}` : ""}{j.harness ? ` · ${j.harness}` : ""}{j.model ? ` · ${j.model}` : ""}
          {j.error ? <span className="bad"> · {j.error}</span> : null}</div>
      </div>
      <div className="row gap-s" onClick={(e) => e.stopPropagation()}>
        <Button small icon={<Eye size={13} />} onClick={onOpen}>Details</Button>
        {j.chat_id && <Button small onClick={() => go("chat", j.chat_id)}>Open chat</Button>}
        <PauseButton job={j} small />
        {running && <Button small kind="danger" onClick={() => api.post(`/jobs/${j.id}/cancel`)}>Stop</Button>}
        {!running && j.can_continue && <Button small kind="primary" icon={<Play size={13} />} title="Run the tasks that were not done; finished ones are kept"
          onClick={() => api.post(`/evals/${j.eval_id}/continue`).catch((e: any) => toast(e.message, "error"))}>Continue</Button>}
        {!running && <button className="icon-btn" title="Take it off the list" onClick={() => api.del(`/jobs/${j.id}`).catch((e: any) => toast(e.message, "error"))}><X size={13} /></button>}
      </div>
    </div>
  );
}

export default function Activity() {
  const [tab, setTab] = useState("tasks");
  const [group, setGroup] = useState("all");
  const [jobs, setJobs] = useState<any[] | null>(null);
  const [live, setLive] = useState<Record<string, any>>({});
  const [open, setOpen] = useState<string | null>(null);
  const load = () => api.get("/jobs").then((r) => setJobs(r.jobs.map((j: any) => ({ ...j, loaded: Date.now() }))));
  useEffect(() => { load(); }, []);
  useEvent((e) => {
    if (e.kind === "job") load();
    if (e.kind === "job_progress") setLive((p) => ({ ...p, [e.id]: { ...p[e.id], text: e.text || p[e.id]?.text, fraction: e.fraction ?? p[e.id]?.fraction, pace: e.pace ?? null, seenAt: Date.now() } }));
    if (e.kind === "job_trail") setLive((p) => ({ ...p, [e.id]: { ...p[e.id], ...(e.entry.kind === "step" ? { text: e.entry.text, fraction: null, pace: null } : {}), seenAt: Date.now() } }));
    if (e.kind === "job_detail") setLive((p) => ({ ...p, [e.id]: { ...p[e.id], seenAt: Date.now() } }));
  });
  if (!jobs) return <div className="page"><Spinner /></div>;
  const named = GROUPS.flatMap((g) => g.kinds || []);
  const inGroup = (j: any, g: any) => g.id === "all" || (g.kinds ? g.kinds.includes(j.kind) : !named.includes(j.kind));
  const shown = jobs.filter((j) => inGroup(j, GROUPS.find((g) => g.id === group)));
  const running = shown.filter((j) => ACTIVE.includes(j.status)), earlier = shown.filter((j) => !ACTIVE.includes(j.status));
  const count = (g: any) => jobs.filter((j) => ACTIVE.includes(j.status) && inGroup(j, g)).length;
  return (
    <div className="page">
      <PageHeader title="Activity" subtitle="Everything that is working or has worked: chat replies, scheduled and long tasks, pictures, sound, video, downloads and installs. Open any of them to see its plan, every step with its time, and what the program itself printed. Tasks keep going when the window is closed." />
      <Tabs value={tab} onChange={setTab} tabs={[{ id: "tasks", label: "Tasks" }, { id: "runs", label: "Planned runs" }, { id: "records", label: "Models' record" }]} />
      {tab === "runs" && <Runs />}
      {tab === "records" && <Records />}
      {tab === "tasks" && <>
        <div className="chips">
          {GROUPS.map((g) => <button key={g.id} className={`chip${group === g.id ? " chip-on" : ""}`} onClick={() => setGroup(g.id)}>{g.label}{count(g) ? <span className="chip-n">{count(g)}</span> : null}</button>)}
        </div>
        <div className="work-h">Working now{running.length ? ` (${running.length})` : ""}</div>
        {running.length === 0
          ? <div className="card small muted">Nothing is working right now.</div>
          : <div className="card">{running.map((j) => <TaskRow key={j.id} j={j} live={live[j.id]} onOpen={() => setOpen(j.id)} />)}</div>}
        <Programs jobs={jobs} onOpen={setOpen} />
        <div className="work-h" style={{ marginTop: 18 }}>Earlier</div>
        {earlier.length === 0
          ? <Empty icon={<ActivityIcon size={32} />} title="Nothing yet">Start a chat, make a picture or a recording, or plan a scheduled task.</Empty>
          : <div className="card">{earlier.map((j) => <TaskRow key={j.id} j={j} live={null} onOpen={() => setOpen(j.id)} />)}</div>}
      </>}
      {open && <WorkDetail id={open} onClose={() => setOpen(null)} />}
    </div>
  );
}
