import React, { useEffect, useRef, useState } from "react";
import {
  AlertTriangle, Ban, Brain, Check, CheckCircle2, ChevronDown, ChevronRight, Circle, Clock, Cloud, Copy, CornerDownRight,
  Cpu, Eye, ExternalLink, FileText, FolderOpen, ListChecks, Loader2, MoreHorizontal, Paperclip, Pause, Pin, Play, RotateCcw, Send,
  ShieldCheck, Sparkles, Trash2, Users, Workflow, X, XCircle,
} from "lucide-react";
import { api, desktop, fileUrl, fmtBytes, fmtTime } from "../api";
import { go, useApp, useEvent } from "../store";
import { Badge, Button, Markdown, Spinner, Toggle } from "../components/ui";
import { Composer, ConnectorPicker, FolderPicker, HarnessPicker, LimitsPicker, ModelPicker, TeamPicker, ThinkingToggle, uploadAll } from "../components/Pickers";

function ToolCard({ step, approval, onDecide }: any) {
  const [open, setOpen] = useState(false);
  const state = step.state === "running" ? "running" : step.declined ? "declined" : step.blocked ? "blocked" : step.ok === false ? "failed" : "ok";
  const icon = state === "running" ? <Loader2 size={14} className="spin" /> : state === "ok" ? <CheckCircle2 size={14} className="ok" />
    : state === "declined" ? <ShieldCheck size={14} className="muted" /> : state === "blocked" ? <Ban size={14} className="warn-c" /> : <XCircle size={14} className="bad" />;
  return (
    <div className={`tool-card tool-${state}`}>
      <div className="tool-head" onClick={() => setOpen(!open)}>
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}{icon}
        <span className="tool-label">{step.label || step.name}</span>
        <span className="tool-desc ellipsis">{step.description || summarize(step.arguments)}</span>
        {state === "declined" && <Badge>declined</Badge>}
        {state === "blocked" && <Badge kind="warn" title="The loop guard stopped a repeated or over-limit call">repeat stopped</Badge>}
      </div>
      {approval && (
        <div className="approval">
          {approval.tool === "send_to_online_model" ? <>
            <div className="approval-title"><Cloud size={15} /> {approval.agent ? <>Agent <b>{approval.agent}</b>: send this to an online model?</> : "Send this to an online model?"}</div>
            <pre className="approval-desc">{approval.description}</pre>
            <div className="row gap-s">
              <Button kind="primary" small onClick={() => onDecide(approval.approval_id, true, false)}>Allow</Button>
              <Button small onClick={() => onDecide(approval.approval_id, true, true)}>Always in this chat</Button>
              <Button kind="ghost" small onClick={() => onDecide(approval.approval_id, false, false)}>Keep it on this computer</Button>
            </div>
          </> : <>
            <div className="approval-title"><ShieldCheck size={15} /> {approval.agent ? <>Agent <b>{approval.agent}</b> asks: allow this?</> : "Allow this?"}</div>
            <pre className="approval-desc">{approval.description}</pre>
            <div className="row gap-s">
              <Button kind="primary" small onClick={() => onDecide(approval.approval_id, true, false)}>Allow</Button>
              <Button small onClick={() => onDecide(approval.approval_id, true, true)}>Always allow in this chat</Button>
              <Button kind="ghost" small onClick={() => onDecide(approval.approval_id, false, false)}>Decline</Button>
            </div>
          </>}
        </div>
      )}
      {open && (
        <div className="tool-body">
          <div className="small muted">Arguments</div>
          <pre>{JSON.stringify(step.arguments, null, 2)}</pre>
          {step.result !== undefined && <><div className="small muted">Result</div><pre>{step.result}</pre></>}
        </div>
      )}
    </div>
  );
}

function summarize(args: any) {
  if (!args) return "";
  const v = Object.values(args)[0];
  return typeof v === "string" ? v.slice(0, 120) : "";
}

function PlanView({ plan, current }: any) {
  if (!plan?.length) return null;
  return (
    <div className="plan">
      <div className="small muted">Plan</div>
      {plan.map((s: any, i: number) => (
        <div key={i} className={`plan-step plan-${s.state}`}>
          {s.state === "done" ? <Check size={14} className="ok" /> : s.state === "doing" || i === current ? <Loader2 size={14} className="spin accent" /> : <Circle size={12} className="muted" />}
          <span>{s.title}</span>
        </div>
      ))}
    </div>
  );
}

function PipelineResult({ step }: any) {
  const r = step.result || {};
  return (
    <div className="pipeline-result">
      {r.cover && <img src={fileUrl(r.cover)} alt="cover" />}
      <div className="row gap-s wrap">
        {r.dataset?.edit_url && <Button small kind="primary" onClick={() => desktop ? desktop.openExternal(r.dataset.edit_url) : window.open(r.dataset.edit_url)}>Open on Datavera</Button>}
        {r.run_folder && desktop && <Button small onClick={() => desktop.openPath(r.run_folder)}>Open run folder</Button>}
        <Badge kind={step.code === 0 ? "ok" : "warn"}>exit {step.code}</Badge>
      </div>
    </div>
  );
}

function LogView({ lines }: any) {
  const [open, setOpen] = useState(true);
  const ref = useRef<HTMLPreElement>(null);
  useEffect(() => { if (ref.current) ref.current.scrollTop = ref.current.scrollHeight; }, [lines?.length]);
  if (!lines?.length) return null;
  return (
    <div className="logview">
      <div className="tool-head" onClick={() => setOpen(!open)}>{open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}<span className="tool-label">Progress log</span><span className="muted small">{lines.length} lines</span></div>
      {open && <pre ref={ref}>{lines.join("\n")}</pre>}
    </div>
  );
}

function TeamCards({ agents }: any) {
  if (!agents?.length) return null;
  const icon = (st: string) => st === "done" ? <CheckCircle2 size={14} className="ok" /> : st === "failed" ? <XCircle size={14} className="bad" />
    : st === "stopped" ? <Ban size={14} className="muted" /> : st === "waiting" ? <Clock size={14} className="muted" /> : <Loader2 size={14} className="spin accent" />;
  return (
    <div className="team">
      <div className="small muted row gap-s"><Users size={13} /> Team · {agents.filter((a: any) => ["done", "failed", "stopped"].includes(a.status)).length}/{agents.length} finished</div>
      <div className="team-grid">
        {agents.map((a: any, i: number) => (
          <div key={i} className={`team-card team-${a.status}`}>
            <div className="row between"><span className="row gap-s">{icon(a.status)}<b>{a.name}</b></span>
              {a.chat_id && <button className="icon-btn" title="Open this agent's chat" onClick={() => go("chat", a.chat_id)}><ExternalLink size={13} /></button>}</div>
            <div className="small muted ellipsis">{a.harness}{a.model ? ` · ${a.model}` : ""}</div>
            <div className="small clamp-2">{a.task}</div>
            {a.status === "running" && a.progress && <div className="small muted ellipsis">{a.progress}</div>}
          </div>
        ))}
      </div>
    </div>
  );
}

const TASK_LABEL: any = { waiting: "waiting", paused: "paused", running: "working", review: "being checked", done: "accepted", failed: "failed", cancelled: "not run" };

const fmtTokens = (n: number) => !n ? "0" : n >= 10000 ? `${Math.round(n / 1000)}k` : n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);
const fmtDuration = (s: number) => s < 90 ? `${Math.max(1, Math.round(s))} s` : s < 5400 ? `${Math.round(s / 60)} min` : `${(s / 3600).toFixed(1)} h`;

// What a step is forecast to need before it runs, and what it really used afterwards.
function taskCost(t: any) {
  if (t.used?.tokens) return `used ${fmtTokens(t.used.tokens)} tokens · ${fmtDuration(t.used.seconds || 0)}`;
  const f = t.forecast;
  if (!f?.tokens) return "";
  return `about ${fmtTokens(f.tokens)} tokens · ${fmtDuration(f.seconds || 0)}${f.learned ? "" : " (first estimate)"}`;
}

function taskIcon(t: any) {
  if (t.paused && t.status === "running") return <Pause size={14} className="warn-c" />;
  switch (t.status) {
    case "done": return <CheckCircle2 size={14} className="ok" />;
    case "failed": return <XCircle size={14} className="bad" />;
    case "cancelled": return <Ban size={14} className="muted" />;
    case "paused": return <Pause size={14} className="warn-c" />;
    case "review": return <Eye size={14} className="accent" />;
    case "running": return <Loader2 size={14} className="spin accent" />;
    default: return <Clock size={14} className="muted" />;
  }
}

function OrchestraTask({ t, live }: any) {
  const [open, setOpen] = useState(false);
  const { toast } = useApp();
  const act = async (what: string) => {
    try { await api.post(`/chats/${t.chat_id}/${what}`, what === "pause" ? { reason: "paused by you" } : {}); }
    catch (e: any) { toast(e.message, "error"); }
  };
  return (
    <div className={`orch-task st-${t.paused && t.status === "running" ? "paused" : t.status}`}>
      <div className="row between gap-s">
        <span className="row gap-s ellipsis">{taskIcon(t)}<b className="ellipsis">{t.name}</b><span className="muted small">{t.id}</span></span>
        <span className="row gap-xs">
          {live && t.status === "running" && t.chat_id && (t.paused
            ? <button className="icon-btn" title="Resume this agent" onClick={() => act("resume")}><Play size={13} /></button>
            : <button className="icon-btn" title="Pause this agent (it stops at its next step and frees its model)" onClick={() => act("pause")}><Pause size={13} /></button>)}
          {t.chat_id && <button className="icon-btn" title="Open this agent's chat" onClick={() => go("chat", t.chat_id)}><ExternalLink size={13} /></button>}
        </span>
      </div>
      <div className="small muted ellipsis">
        {t.partial ? "kept as best attempt" : TASK_LABEL[t.status] || t.status}{t.attempts > 1 || (t.status === "running" && t.rounds > 1) ? ` · round ${Math.max(1, t.attempts)}${t.rounds ? ` of ${t.rounds}` : ""}` : ""} · {t.agent ? `${t.agent} · ` : ""}{t.harness}
        {t.model_name ? <> · {t.online ? <Cloud size={11} /> : null} {t.model_name}</> : null}
      </div>
      {(t.depends_on?.length > 0 || taskCost(t)) && <div className="small muted">
        {t.depends_on?.length > 0 ? `uses ${t.depends_on.join(", ")}` : ""}{t.depends_on?.length > 0 && taskCost(t) ? " · " : ""}{taskCost(t)}
      </div>}
      <div className="small clamp-2" title={t.goal}>{t.goal}</div>
      {t.status === "running" && t.progress && <div className="small accent ellipsis">{t.paused ? "paused" : t.progress}</div>}
      {t.status === "waiting" && t.wait_reason && <div className="small muted">{t.wait_reason}</div>}
      {t.note && <div className="small orch-note">{t.status === "failed" || t.partial || t.status === "running" ? "Check: " : ""}{t.note}</div>}
      {t.output && (
        <div className="orch-output">
          <button className="link small" onClick={() => setOpen(!open)}>{open ? "Hide result" : "Show result"}</button>
          {open && <div className="small"><Markdown>{t.output}</Markdown></div>}
        </div>
      )}
    </div>
  );
}

function OrchestraCard({ step, live }: any) {
  const [showLog, setShowLog] = useState(false);
  const tasks: any[] = step?.tasks || [];
  if (!tasks.length) return null;
  const byId: any = Object.fromEntries(tasks.map((t) => [t.id, t]));
  const depth: any = {};
  const level = (t: any, seen: Set<string> = new Set()): number => {
    if (depth[t.id] !== undefined) return depth[t.id];
    if (seen.has(t.id)) return 0;
    seen.add(t.id);
    const deps = (t.depends_on || []).filter((d: string) => byId[d]);
    return (depth[t.id] = deps.length ? 1 + Math.max(...deps.map((d: string) => level(byId[d], seen))) : 0);
  };
  tasks.forEach((t) => level(t));
  const lanes: any[][] = [];
  tasks.forEach((t) => { (lanes[depth[t.id]] = lanes[depth[t.id]] || []).push(t); });
  const done = tasks.filter((t) => t.status === "done").length;
  const failed = tasks.filter((t) => t.status === "failed").length;
  const skipped = tasks.filter((t) => t.status === "cancelled").length;
  const running = tasks.filter((t) => t.status === "running").length;
  const sequence = step.mode === "sequence";
  return (
    <div className="orch">
      <div className="orch-head">
        <span className="row gap-s"><Workflow size={14} /> <b>{sequence ? "Plan" : "Team"}</b>
          <span className="muted small">{done} of {tasks.length} done{failed ? ` · ${failed} failed` : ""}{skipped ? ` · ${skipped} not run` : ""}{running ? ` · ${running} working` : ""}{sequence ? " · one step at a time" : ""}
            {step.totals?.tokens ? ` · ${fmtTokens(step.totals.tokens)} tokens` : ""}</span></span>
        <span className="row gap-s">
          {step.log?.length > 0 && <button className="link small" onClick={() => setShowLog(!showLog)}>{showLog ? "Hide the planner's log" : `Planner's log (${step.log.length})`}</button>}
          {desktop && step.folder && <button className="icon-btn" title="Open the shared team folder" onClick={() => desktop.openPath(step.folder)}><FolderOpen size={13} /></button>}
        </span>
      </div>
      {sequence ? (
        <div className="orch-seq">
          {tasks.map((t, i) => (
            <div key={t.id} className="orch-seq-row"><span className="orch-seq-n">{i + 1}</span><OrchestraTask t={t} live={live} /></div>
          ))}
        </div>
      ) : <div className="orch-lanes">
        {lanes.map((lane, i) => (
          <div key={i} className="orch-lane">
            <div className="orch-lane-title">{i === 0 ? "Start" : `Then (${i})`}</div>
            {lane.map((t) => <OrchestraTask key={t.id} t={t} live={live} />)}
          </div>
        ))}
      </div>}
      {showLog && <div className="orch-log">{step.log.map((l: any, i: number) => <div key={i}><span className="muted">{Math.round(l.at)}s</span> {l.text}</div>)}</div>}
    </div>
  );
}

function Steps({ steps, approvals, onDecide, log }: any) {
  return (
    <>
      {steps.map((s: any, i: number) => {
        if (s.type === "text") return <Markdown key={i}>{s.text}</Markdown>;
        if (s.type === "tool") return <ToolCard key={s.id || i} step={s} approval={approvals?.find((a: any) => a.call_id === s.id)} onDecide={onDecide} />;
        if (s.type === "plan_step") return <div key={i} className="plan-divider">Step {s.index}: {s.title}</div>;
        if (s.type === "pipeline") return <div key={i} className="note"><Sparkles size={14} /> Running <b>{s.harness}</b>{Object.keys(s.inputs || {}).length ? <> with {Object.entries(s.inputs).filter(([, v]) => v).map(([k, v]) => `${k}: ${v}`).join(", ")}</> : null}</div>;
        if (s.type === "pipeline_end") return <div key={i}>{!log && s.log_tail?.length ? <LogView lines={s.log_tail} /> : null}<PipelineResult step={s} /></div>;
        if (s.type === "error") return <div key={i} className="error-line"><AlertTriangle size={14} /> {s.text}</div>;
        if (s.type === "note") return <div key={i} className="note">{s.text}</div>;
        if (s.type === "steer") return <div key={i} className="note steer"><CornerDownRight size={14} /> You added while it worked: “{s.text}”</div>;
        if (s.type === "team") return <TeamCards key={i} agents={s.agents} />;
        if (s.type === "orchestra") return <OrchestraCard key={i} step={s} />;
        return null;
      })}
    </>
  );
}

function Reasoning({ text, live }: any) {
  const [open, setOpen] = useState(false);
  if (!text) return null;
  return (
    <div className="reasoning">
      <div className="tool-head" onClick={() => setOpen(!open)}>{open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}<Brain size={14} /><span className="muted">{live ? "Thinking…" : "Thoughts"}</span></div>
      {open && <div className="reasoning-body">{text}</div>}
    </div>
  );
}

function AssistantMessage({ msg, isLast, onRetry }: any) {
  const meta = msg.meta || {};
  const steps = meta.steps?.length ? meta.steps : [{ type: "text", text: msg.content }];
  const stats = meta.stats || {};
  return (
    <div className="msg msg-assistant">
      <Reasoning text={meta.reasoning} />
      <PlanView plan={meta.plan} />
      <Steps steps={steps} />
      {meta.stopped && <div className="note">Stopped.</div>}
      <div className="msg-foot">
        <span>{meta.harness_name || meta.harness}</span>
        {meta.model && <span title={meta.auto ? "Chosen by Auto" : undefined}>· {meta.online ? <Cloud size={11} /> : null} {meta.model_name || meta.model}{meta.auto ? " (Auto)" : ""}</span>}
        {stats.cost ? <span title={stats.cost_estimated ? "Estimated: this model's price is not known" : "Online model cost (estimate from tokens and price)"}>· {stats.cost_estimated ? "~" : ""}${stats.cost < 0.01 ? stats.cost.toFixed(4) : stats.cost.toFixed(2)}</span> : null}
        {stats.seconds ? <span>· {stats.seconds}s</span> : null}
        {stats.tokens_per_second ? <span>· {stats.tokens_per_second} tok/s</span> : null}
        {stats.cached_tokens ? <span title={`${stats.cached_tokens.toLocaleString()} prompt tokens came from the model's cache; ${(stats.prompt_tokens || 0).toLocaleString()} were read`}>· {Math.round(100 * stats.cached_tokens / Math.max(1, stats.cached_tokens + (stats.prompt_tokens || 0)))}% from cache</span> : null}
        <button className="icon-btn" title="Copy" onClick={() => navigator.clipboard.writeText(msg.content || "")}><Copy size={13} /></button>
        {isLast && <button className="icon-btn" title="Retry" onClick={onRetry}><RotateCcw size={13} /></button>}
      </div>
    </div>
  );
}

function LiveTurn({ live, onDecide }: any) {
  return (
    <div className="msg msg-assistant live">
      <Reasoning text={live.reasoning} live />
      <PlanView plan={live.plan} current={live.current} />
      <Steps steps={live.steps} approvals={live.approvals} onDecide={onDecide} log={live.log} />
      {live.team && <TeamCards agents={live.team} />}
      {live.orchestra && <OrchestraCard step={live.orchestra} live />}
      <LogView lines={live.log} />
      {live.approvals.filter((a: any) => !a.call_id).map((a: any) => (
        <ToolCard key={a.approval_id} step={{ label: a.agent ? `${a.agent}: ${a.label || a.tool}` : a.label || a.tool, description: a.description, state: "running", arguments: {} }} approval={a} onDecide={onDecide} />
      ))}
      {live.error && <div className="error-line"><AlertTriangle size={14} /> {live.error}</div>}
      <div className="typing"><Spinner size={13} /> {live.status || "Working…"}</div>
    </div>
  );
}

// The plan of the reply being written (or of the last reply): a planned run's steps, or a Planner harness's plan.
function progressOf(live: any, lastAssistant: any) {
  const orch = live ? live.orchestra : [...(lastAssistant?.meta?.steps || [])].reverse().find((s: any) => s.type === "orchestra");
  if (orch?.tasks?.length) {
    const items = orch.tasks.map((t: any) => ({
      id: t.id, name: t.name, status: t.paused && t.status === "running" ? "paused" : t.status, partial: t.partial,
      detail: t.status === "running" ? (t.progress || "working") : t.status === "waiting" ? (t.wait_reason || "") : t.note || "",
      sub: [t.attempts > 1 ? `round ${t.attempts}${t.rounds ? ` of ${t.rounds}` : ""}` : "", taskCost(t)].filter(Boolean).join(" · "),
      chat_id: t.chat_id,
    }));
    const files: string[] = Array.from(new Set(orch.tasks.flatMap((t: any) => [...(t.files || []), ...(t.result_file ? [t.result_file] : [])])));
    return { items, files, models: orch.models || [], folder: orch.folder, totals: orch.totals, log: orch.log || [] };
  }
  const plan = live ? live.plan : lastAssistant?.meta?.plan;
  if (plan?.length) {
    return { items: plan.map((s: any, i: number) => ({ id: String(i + 1), name: s.title, detail: s.detail || "",
      status: s.state === "done" ? "done" : s.state === "doing" || (live && live.current === i) ? "running" : s.state === "skipped" ? "cancelled" : "waiting" })),
      files: [], models: [], log: [] };
  }
  return null;
}

function progressLabel(p: any) {
  const n = p.items.length;
  const settled = p.items.filter((i: any) => ["done", "failed", "cancelled"].includes(i.status)).length;
  const at = p.items.findIndex((i: any) => ["running", "review", "paused"].includes(i.status));
  if (at >= 0) return `Step ${at + 1} of ${n}`;
  if (settled === n) return `${p.items.filter((i: any) => i.status === "done").length} of ${n} done`;
  return `Step ${Math.min(settled + 1, n)} of ${n}`;
}

function ProgressPanel({ progress, onClose }: any) {
  const open = (path: string) => desktop ? desktop.openPath(path) : window.open(fileUrl(path));
  const base = (path: string) => path.split(/[\\/]/).pop();
  const lastLog = progress.log?.length ? progress.log[progress.log.length - 1] : null;
  return (
    <div className="side-panel progress-panel">
      <div className="panel-head"><b>Progress</b><button className="icon-btn" title="Close" onClick={onClose}><X size={16} /></button></div>
      <div className="panel-list">
        <div className="progress-steps">
          {progress.items.map((it: any) => (
            <div key={it.id} className={`progress-step ps-${it.status}`} onClick={() => it.chat_id && go("chat", it.chat_id)}
              title={it.chat_id ? "Open this step's agent chat" : undefined} style={{ cursor: it.chat_id ? "pointer" : "default" }}>
              <span className="progress-icon">{taskIcon(it)}</span>
              <div className="progress-text">
                <div className="progress-name">{it.name}</div>
                {it.sub && <div className="small muted">{it.sub}</div>}
                {it.detail && <div className={`small ${it.status === "running" ? "accent" : "muted"} clamp-2`}>{it.partial ? "Incomplete: " : ""}{it.detail}</div>}
              </div>
            </div>
          ))}
        </div>
        {lastLog && <div className="small muted pad-x progress-last">{lastLog.text}</div>}
        {(progress.models.length > 0 || progress.files.length > 0 || progress.folder) && <div className="progress-used">
          <div className="small progress-used-title">Used in this run</div>
          {progress.totals?.tokens ? <div className="small muted pad-x">{fmtTokens(progress.totals.tokens)} tokens · {fmtDuration(progress.totals.seconds || 0)}</div> : null}
          {progress.models.map((m: any) => (
            <div key={m.id} className="file-row" style={{ cursor: "default" }} title={m.loaded ? "In memory now" : "Not in memory: it is loaded only while a step needs it"}>
              {m.online ? <Cloud size={14} /> : <Cpu size={14} />}<span className="ellipsis">{m.name}</span>
              <span className="muted small">{m.online ? "online" : m.loaded ? `loaded${m.ctx ? ` · ${fmtTokens(m.ctx)} context` : ""}${m.memory_mb ? ` · ${(m.memory_mb / 1024).toFixed(1)} GB` : ""}` : "not loaded"}</span>
            </div>
          ))}
          {progress.folder && desktop && <div className="file-row" onClick={() => desktop.openPath(progress.folder)}><FolderOpen size={14} /><span className="ellipsis">Shared folder</span></div>}
          {progress.files.map((f: string) => (
            <div key={f} className="file-row" onClick={() => open(f)} title={f}><FileText size={14} /><span className="ellipsis">{base(f)}</span></div>
          ))}
        </div>}
      </div>
    </div>
  );
}

function FilesPanel({ chatId, onClose }: any) {
  const [data, setData] = useState<any>(null);
  const load = () => api.get(`/chats/${chatId}/files`).then(setData);
  useEffect(() => { load(); }, [chatId]);
  useEvent((e) => { if (e.kind === "chat" && e.chat_id === chatId && e.type === "file_changed") load(); }, [chatId]);
  return (
    <div className="side-panel">
      <div className="panel-head"><b>Chat files</b><button className="link" onClick={onClose}>Close</button></div>
      {data && <div className="small muted pad-x">{data.root}</div>}
      {desktop && data && <div className="pad-x"><Button small icon={<FolderOpen size={13} />} onClick={() => desktop.openPath(data.root)}>Open folder</Button></div>}
      <div className="panel-list">
        {data?.files.length === 0 && <div className="muted pad">Uploads, downloads and files the agent makes without a folder land here.</div>}
        {data?.files.map((f: any) => (
          <div key={f.path} className="file-row" onClick={() => desktop ? desktop.openPath(f.path) : window.open(fileUrl(f.path))}>
            <FileText size={14} /><span className="ellipsis">{f.name}</span><span className="muted small">{fmtBytes(f.size)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function UserMessage({ m, chatId }: any) {
  const { toast } = useApp();
  const meta = m.meta || {};
  const act = async (fn: () => Promise<any>) => { try { await fn(); } catch (e: any) { toast(e.message, "error"); } };
  return (
    <div className={`msg msg-user${meta.pending ? " pending" : ""}${meta.not_sent ? " not-sent" : ""}`}>
      <div className="bubble">{m.content}
        {(meta.attachments || []).map((a: any) => <div key={a.path} className="attach"><Paperclip size={12} />{a.name}</div>)}
      </div>
      <div className="msg-time">
        {meta.pending === "queue" && <span className="pending-tag"><Clock size={12} /> Queued — sent when the current reply ends</span>}
        {meta.pending === "steer" && <span className="pending-tag"><CornerDownRight size={12} /> Waiting for the agent's next step</span>}
        {meta.steered && <span className="pending-tag"><CornerDownRight size={12} /> Given to the agent while it worked ·&nbsp;</span>}
        {meta.not_sent && <span className="pending-tag"><Ban size={12} /> {meta.not_sent === "restart" ? "Not sent (the app was closed)" : "Not sent (you pressed Stop)"}
          <button className="link" onClick={() => act(() => api.post(`/chats/${chatId}/messages/${m.id}/resend`))}><Send size={11} /> Send now</button></span>}
        {(meta.pending || meta.not_sent) && <button className="icon-btn" title="Remove" onClick={() => act(() => api.del(`/chats/${chatId}/messages/${m.id}`))}><X size={12} /></button>}
        {!meta.pending && !meta.not_sent && fmtTime(m.created)}
      </div>
    </div>
  );
}

export default function ChatView({ chatId }: { chatId: string }) {
  const { toast, projects, refreshChats } = useApp();
  const [chat, setChat] = useState<any>(null);
  const [live, setLive] = useState<any>(null);
  const [learned, setLearned] = useState<any[]>([]);
  const [showFiles, setShowFiles] = useState(false);
  const [showProgress, setShowProgress] = useState(false);
  const [menu, setMenu] = useState(false);
  const [editingTitle, setEditingTitle] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const stick = useRef(true);

  const load = async () => {
    try {
      const c = await api.get(`/chats/${chatId}`);
      setChat(c);
      if (c.running_job && !live) setLive((l: any) => l || { message_id: null, steps: [], approvals: c.pending_approvals.map((a: any) => ({ ...a })), log: [], status: "Working…", reasoning: "", orchestra: c.orchestra });
      if (!c.running_job) setLive(null);
    } catch (e: any) { toast(e.message, "error"); go("home"); }
  };
  useEffect(() => { load(); }, [chatId]);

  useEffect(() => {
    if (stick.current) bottom.current?.scrollIntoView({ block: "end" });
  });

  const update = (fn: (l: any) => any) => setLive((l: any) => fn(l || { steps: [], approvals: [], log: [], reasoning: "" }));

  useEvent((e) => {
    if (e.kind === "memory_learned" && e.chat_id === chatId) setLearned((x) => [...x, ...e.items]);
    if (e.kind === "job" && e.chat_id === chatId && ["done", "failed", "cancelled"].includes(e.status)) { setTimeout(load, 300); }
    if (e.kind !== "chat" || e.chat_id !== chatId) return;
    switch (e.type) {
      case "user_message":
        setChat((c: any) => c && !c.messages.find((m: any) => m.id === e.message_id)
          ? { ...c, messages: [...c.messages, { id: e.message_id, role: "user", content: e.content, meta: e.meta, created: new Date().toISOString() }] } : c);
        if (!e.meta?.pending) update(() => ({ message_id: null, steps: [], approvals: [], log: [], reasoning: "", status: "Starting…" }));
        break;
      case "message_meta": case "message_deleted": load(); break;
      case "steer": update((l) => ({ ...l, steps: [...l.steps.map((s: any) => s.type === "text" ? { ...s, closed: true } : s), { type: "steer", text: e.text }] }));
        setChat((c: any) => c ? { ...c, messages: c.messages.map((m: any) => m.id === e.steer_id ? { ...m, meta: { ...m.meta, pending: undefined, steered: true } } : m) } : c);
        break;
      case "note": update((l) => ({ ...l, steps: [...l.steps, { type: "note", text: e.text }] })); break;
      case "team": update((l) => ({ ...l, team: e.agents })); break;
      case "orchestra": update((l) => ({ ...l, orchestra: e })); break;
      case "paused": setChat((c: any) => c ? { ...c, paused: true } : c); update((l) => ({ ...l, status: `Paused: ${e.reason}` })); break;
      case "resumed": setChat((c: any) => c ? { ...c, paused: false } : c); break;
      case "agent_approval": update((l) => ({ ...l, approvals: [...l.approvals.filter((a: any) => a.approval_id !== e.approval_id), { ...e }], status: `Waiting for your approval (${e.agent})` })); break;
      case "agent_approval_done": update((l) => ({ ...l, approvals: l.approvals.filter((a: any) => a.approval_id !== e.approval_id) })); break;
      case "assistant_start": update((l) => ({ ...l, message_id: e.message_id, status: "Starting…" })); break;
      case "status": update((l) => ({ ...l, status: e.text })); break;
      case "reasoning": update((l) => ({ ...l, reasoning: (l.reasoning || "") + e.text })); break;
      case "token": update((l) => {
        const steps = [...l.steps];
        const last = steps[steps.length - 1];
        if (last && last.type === "text" && !last.closed) steps[steps.length - 1] = { ...last, text: last.text + e.text };
        else steps.push({ type: "text", text: e.text });
        return { ...l, steps, status: "Writing…" };
      }); break;
      case "text_replace": update((l) => {
        const steps = [...l.steps];
        for (let i = steps.length - 1; i >= 0; i--) if (steps[i].type === "text") { steps[i] = { ...steps[i], text: e.text }; break; }
        return { ...l, steps };
      }); break;
      case "tool_call": update((l) => ({ ...l, steps: [...l.steps.map((s: any) => s.type === "text" ? { ...s, closed: true } : s), { ...e.call, type: "tool", state: "running" }], status: e.call.label || "Working…" })); break;
      case "tool_result": update((l) => ({ ...l, steps: l.steps.map((s: any) => s.id === e.call_id ? { ...s, state: "done", ok: e.ok, result: e.result, declined: e.declined, blocked: e.blocked } : s) })); break;
      case "approval": update((l) => {
        const lastTool = [...l.steps].reverse().find((s: any) => s.type === "tool" && s.state === "running");
        return { ...l, approvals: [...l.approvals, { ...e, call_id: lastTool?.id }], status: "Waiting for your approval" };
      }); break;
      case "approval_done": update((l) => ({ ...l, approvals: l.approvals.filter((a: any) => a.approval_id !== e.approval_id) })); break;
      case "plan": update((l) => ({ ...l, plan: e.steps, current: e.current })); break;
      case "log": update((l) => ({ ...l, log: [...(l.log || []), e.text].slice(-400) })); break;
      case "pipeline_start": update((l) => ({ ...l, steps: [...l.steps, { type: "pipeline", harness: e.harness, inputs: e.inputs }] })); break;
      case "error": update((l) => ({ ...l, error: e.text })); break;
      case "assistant_end":
        setChat((c: any) => c ? { ...c, running_job: null, messages: [...c.messages, { id: e.message_id, role: "assistant", content: e.content, meta: e.meta, created: new Date().toISOString() }] } : c);
        setLive(null);
        refreshChats();
        break;
    }
  }, [chatId]);

  const patch = async (values: any) => {
    try { setChat({ ...(await api.put(`/chats/${chatId}`, values)) }); } catch (e: any) { toast(e.message, "error"); }
  };

  const [team, setTeam] = useState<any[]>([]);
  const send = async (text: string, files: File[], mode?: string) => {
    try {
      const attachments = await uploadAll(chatId, files);
      stick.current = true;
      const body: any = { text, attachments, mode: mode || "queue" };
      if (team.length && !running) body.team = team.map((a) => ({ ...a, model: a.model || chat.model || undefined }));
      const r = await api.post(`/chats/${chatId}/messages`, body);
      if (body.team) setTeam([]);
      if (r.state === "queue") toast("Queued: it will be sent when this reply ends", "info");
      if (r.state === "steer") toast("The agent will read this at its next step", "info");
    } catch (e: any) { toast(e.message, "error"); throw e; }
  };

  const decide = async (approvalId: string, allow: boolean, always: boolean) => {
    try { await api.post(`/approvals/${approvalId}`, { allow, always }); } catch (e: any) { toast(e.message, "error"); }
  };

  if (!chat) return <div className="center-screen"><Spinner /></div>;
  const project = projects.find((p: any) => p.id === chat.project_id);
  const running = !!live || !!chat.running_job;
  const lastAssistant = [...chat.messages].reverse().find((m: any) => m.role === "assistant");
  const progress = progressOf(live, lastAssistant);
  const pending = chat.messages.filter((m: any) => m.meta?.pending);
  const settled = chat.messages.filter((m: any) => !m.meta?.pending);

  return (
    <div className="chat-layout">
      <div className="chat-col">
        <div className="chat-head">
          <div className="chat-title">
            {project && <span className="crumb" onClick={() => go("project", project.id)}>{project.name} /</span>}
            {chat.parent && <span className="crumb" onClick={() => go("chat", chat.parent.id)} title="This agent works for a team in another chat">{chat.parent.title || "Team"} / {chat.agent_name} /</span>}
            {editingTitle ? <input className="input title-input" autoFocus defaultValue={chat.title}
              onBlur={(e) => { setEditingTitle(false); patch({ title: e.target.value }); }}
              onKeyDown={(e) => e.key === "Enter" && (e.target as any).blur()} />
              : <h2 onClick={() => setEditingTitle(true)} title="Rename">{chat.title || "New chat"}</h2>}
            {chat.origin === "schedule" && <Badge>scheduled</Badge>}
            {chat.origin === "agent" && <Badge kind="accent">agent</Badge>}
          </div>
          <div className="row gap-s">
            <span className="chip chip-toggle" title="Approve changes (file writes, Python, connector actions) automatically in this chat">
              <ShieldCheck size={14} /><Toggle checked={chat.auto_approve} onChange={(v: boolean) => patch({ auto_approve: v })} label="Auto-approve" />
            </span>
            {running && (chat.paused
              ? <Button small icon={<Play size={13} />} onClick={() => api.post(`/chats/${chatId}/resume`)} title="Continue from where it paused">Resume</Button>
              : <Button small kind="ghost" icon={<Pause size={13} />} onClick={() => api.post(`/chats/${chatId}/pause`, { reason: "paused by you" })} title="Pause at the next step (a team pauses all its agents); the model is freed while paused">Pause</Button>)}
            {progress && <button className={`chip progress-chip${showProgress ? " on" : ""}`} title="Show the plan and its progress" onClick={() => { setShowProgress(!showProgress); setShowFiles(false); }}>
              <ListChecks size={14} /> {progressLabel(progress)}</button>}
            <button className="icon-btn" title="Chat files" onClick={() => { setShowFiles(!showFiles); setShowProgress(false); }}><Paperclip size={16} /></button>
            <div className="popover-wrap">
              <button className="icon-btn" onClick={() => setMenu(!menu)}><MoreHorizontal size={18} /></button>
              {menu && (
                <div className="popover right" onMouseLeave={() => setMenu(false)}>
                  <div className="menu">
                    <div className="menu-item" onClick={() => { patch({ pinned: !chat.pinned }); setMenu(false); }}><Pin size={13} /> {chat.pinned ? "Unpin" : "Pin"}</div>
                    <div className="menu-sub pad">Move to project</div>
                    <div className="menu-item" onClick={() => { patch({ project_id: null }); setMenu(false); }}>— No project —</div>
                    {projects.map((p: any) => <div key={p.id} className="menu-item" onClick={() => { patch({ project_id: p.id }); setMenu(false); }}>{p.name}</div>)}
                    <div className="menu-item danger" onClick={async () => { await api.del(`/chats/${chatId}`); refreshChats(); go("home"); }}><Trash2 size={13} /> Delete chat</div>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
        <div className="messages" ref={scroller}
          onScroll={(e) => { const el = e.currentTarget; stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80; }}>
          <div className="messages-inner">
            {settled.map((m: any) => m.role === "user" ? <UserMessage key={m.id} m={m} chatId={chatId} />
              : <AssistantMessage key={m.id} msg={m} isLast={m.id === lastAssistant?.id && !running}
                  onRetry={async () => { try { await api.post(`/chats/${chatId}/retry`); load(); } catch (e: any) { toast(e.message, "error"); } }} />)}
            {live && <LiveTurn live={live} onDecide={decide} />}
            {pending.map((m: any) => <UserMessage key={m.id} m={m} chatId={chatId} />)}
            {learned.length > 0 && (
              <div className="learned" onClick={() => go("memory")}>
                <Brain size={14} /> Learned: {learned.map((l) => l.text).join(" · ")}
              </div>
            )}
            <div ref={bottom} />
          </div>
        </div>
        <div className="composer-wrap">
          <Composer onSend={send} running={running} status={live?.status} followUps
            onStop={() => api.post(`/chats/${chatId}/stop`)}
            options={<>
              <HarnessPicker value={chat.harness} onChange={(v: string) => patch({ harness: v })} />
              <ModelPicker value={chat.model} chatId={chat.id} onChange={(v: string) => patch({ model: v })} />
              <LimitsPicker value={chat.settings || {}} onChange={(v: any) => patch({ settings: v })} />
              {!chat.parent_chat_id && <TeamPicker team={team} onChange={setTeam} />}
              <ConnectorPicker value={chat.connectors || []} onChange={(v: string[]) => patch({ connectors: v })} />
              <FolderPicker value={chat.folders || []} onChange={(v: string[]) => patch({ folders: v })} inherited={project?.folders || []} />
              <ThinkingToggle value={!!chat.thinking} onChange={(v: boolean) => patch({ thinking: v })} />
            </>} />
        </div>
      </div>
      {showProgress && progress && <ProgressPanel progress={progress} onClose={() => setShowProgress(false)} />}
      {showFiles && <FilesPanel chatId={chatId} onClose={() => setShowFiles(false)} />}
    </div>
  );
}
