import React, { useEffect, useState } from "react";
import { Activity as ActivityIcon, Ban, CheckCircle2, ChevronDown, ChevronRight, Clock, Loader2, Play, ShieldCheck, Square, Workflow, XCircle } from "lucide-react";
import { api, fmtTime } from "../api";
import { go, useEvent } from "../store";
import { Badge, Button, Empty, Modal, PageHeader, Spinner, Tabs } from "../components/ui";

const ICON: any = {
  running: <Loader2 size={15} className="spin accent" />, queued: <Clock size={15} className="muted" />,
  waiting: <ShieldCheck size={15} className="warn" />, done: <CheckCircle2 size={15} className="ok" />,
  failed: <XCircle size={15} className="bad" />, cancelled: <Square size={13} className="muted" />, interrupted: <XCircle size={15} className="muted" />,
};

const kTokens = (n: number) => !n ? "0" : n >= 10000 ? `${Math.round(n / 1000)}k` : n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);
const dur = (s: number) => s < 90 ? `${Math.max(0, Math.round(s))} s` : s < 5400 ? `${Math.round(s / 60)} min` : `${(s / 3600).toFixed(1)} h`;
const RUN_LABEL: any = { done: "finished", running: "running", stopped: "stopped", interrupted: "cut off", superseded: "replaced", dismissed: "dismissed" };
const STEP_ICON: any = {
  done: <CheckCircle2 size={14} className="ok" />, failed: <XCircle size={14} className="bad" />, cancelled: <Ban size={14} className="muted" />,
  running: <Loader2 size={14} className="spin accent" />, review: <Loader2 size={14} className="spin accent" />,
};

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

export default function Activity() {
  const [tab, setTab] = useState("tasks");
  const [jobs, setJobs] = useState<any[] | null>(null);
  const [progress, setProgress] = useState<Record<string, string>>({});
  const [log, setLog] = useState<any>(null);
  const load = () => api.get("/jobs").then((r) => setJobs(r.jobs));
  useEffect(() => { load(); }, []);
  useEvent((e) => {
    if (e.kind === "job") load();
    if (e.kind === "job_progress") setProgress((p) => ({ ...p, [e.id]: e.text }));
  });
  if (!jobs) return <div className="page"><Spinner /></div>;
  return (
    <div className="page">
      <PageHeader title="Activity" subtitle="Everything the agent is doing or has done: chat replies, pipelines, scheduled runs and plugin actions. Tasks keep going when the window is closed." />
      <Tabs value={tab} onChange={setTab} tabs={[{ id: "tasks", label: "Tasks" }, { id: "runs", label: "Planned runs" }, { id: "records", label: "Models' record" }]} />
      {tab === "runs" && <Runs />}
      {tab === "records" && <Records />}
      {tab === "tasks" && jobs.length === 0 && <Empty icon={<ActivityIcon size={32} />} title="Nothing yet">Start a chat or a scheduled task.</Empty>}
      {tab === "tasks" && <div className="card">
        {jobs.map((j) => (
          <div key={j.id} className="job-row">
            {ICON[j.status] || ICON.queued}
            <div className="grow">
              <div className="row gap-s"><b className="ellipsis">{j.title}</b><Badge>{j.kind}</Badge>{j.status === "waiting" && <Badge kind="warn">needs your approval</Badge>}</div>
              <div className="small muted">{fmtTime(j.created)}{j.harness ? ` · ${j.harness}` : ""}{j.model ? ` · ${j.model}` : ""}
                {["running", "waiting"].includes(j.status) && (progress[j.id] || j.progress) ? ` · ${progress[j.id] || j.progress}` : ""}
                {j.error ? <span className="bad"> · {j.error}</span> : null}</div>
            </div>
            <div className="row gap-s">
              {j.chat_id && <Button small onClick={() => go("chat", j.chat_id)}>Open</Button>}
              {!j.chat_id && <Button small onClick={async () => setLog(await api.get(`/jobs/${j.id}`))}>Log</Button>}
              {["running", "waiting", "queued"].includes(j.status) && <Button small kind="danger" onClick={() => api.post(`/jobs/${j.id}/cancel`)}>Stop</Button>}
            </div>
          </div>
        ))}
      </div>}
      {log && <Modal wide title={log.title} onClose={() => setLog(null)}><pre className="log">{log.log || JSON.stringify(log.result, null, 2)}</pre></Modal>}
    </div>
  );
}
