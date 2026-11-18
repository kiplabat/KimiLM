import React, { useEffect, useState } from "react";
import { CheckCircle2, CircleHelp, Loader2, Pause, Play, Plus, Rocket, Sparkles, Square, XCircle } from "lucide-react";
import { api, desktop, fmtTime } from "../api";
import { go, useApp, useEvent } from "../store";
import { Badge, Button, ConfirmButton, Empty, Field, Modal, PageHeader, Spinner } from "../components/ui";
import { ModelPicker } from "../components/Pickers";
import { EMPTY_SETUP, SetupCard } from "../components/Setup";

const kTokens = (n: number) => !n ? "0" : n >= 10000 ? `${Math.round(n / 1000)}k` : n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);
const dur = (s: number) => s < 90 ? `${Math.max(0, Math.round(s))} s` : s < 5400 ? `${Math.round(s / 60)} min` : `${(s / 3600).toFixed(1)} h`;

function statusOf(m: any) {
  if (m.status === "running") return { label: m.now ? `working on “${m.now}”` : "working", kind: "accent", icon: <Loader2 size={15} className="spin accent" /> };
  if (m.status === "paused" && m.pause?.kind === "question") return { label: "waiting for your answer", kind: "warn", icon: <CircleHelp size={15} className="warn" /> };
  if (m.status === "paused") return { label: m.pause?.kind === "limit" ? "paused at a limit" : m.pause?.kind === "recovery" ? "paused: it could not get past a failure" : "paused", kind: "warn", icon: <Pause size={15} className="warn" /> };
  if (m.status === "stopped") return { label: "stopped", kind: "neutral", icon: <Square size={13} className="muted" /> };
  if (m.status === "interrupted") return { label: "cut off (the app was closed)", kind: "neutral", icon: <XCircle size={15} className="muted" /> };
  return { label: "finished", kind: "ok", icon: <CheckCircle2 size={15} className="ok" /> };
}

// Describe the task → the planner proposes the set-up → change or confirm → it starts in a chat of its own.
function NewTask({ onClose, allowable, defaults }: any) {
  const { toast, projects } = useApp();
  const [text, setText] = useState("");
  const [model, setModel] = useState("");
  const [project, setProject] = useState("");
  const [setup, setSetup] = useState<any>(null);
  const [busy, setBusy] = useState("");
  const plan = async () => {
    setBusy("plan");
    try { setSetup((await api.post("/missions/plan", { text, model: model || undefined })).setup); }
    catch (e: any) { toast(e.message, "error"); }
    setBusy("");
  };
  const start = async () => {
    setBusy("start");
    try {
      const r = await api.post("/missions", { text, setup: setup || { ...EMPTY_SETUP, limits: defaults }, model: model || undefined, project_id: project || undefined });
      onClose(); go("chat", r.chat_id);
    } catch (e: any) { toast(e.message, "error"); setBusy(""); }
  };
  return (
    <Modal wide title="New long-running task" onClose={onClose}
      footer={<>
        {!setup && <Button disabled={!text.trim() || !!busy} busy={busy === "plan"} icon={<Sparkles size={14} />} onClick={plan}>Let the planner set it up</Button>}
        <Button kind="primary" disabled={!text.trim() || !!busy} busy={busy === "start"} icon={<Rocket size={14} />} onClick={start}>{setup ? "Start the task" : "Start with the default set-up"}</Button>
      </>}>
      <Field label="What should it work on?" help="A problem to solve, not a list of steps: the planner works out the steps one at a time from what the agents find. Say where to work (folders, sites) and what you want at the end.">
        <textarea className="input" rows={5} autoFocus value={text} onChange={(e) => setText(e.target.value)}
          placeholder="e.g. Research how other clean-cooking programmes measured stove usage, compare the methods, and write a report with sources in my Reports folder." />
      </Field>
      <div className="row gap-m wrap">
        <Field label="Planner's model"><ModelPicker value={model} onChange={setModel} allowDefault /></Field>
        <Field label="Project">
          <select className="input" value={project} onChange={(e) => setProject(e.target.value)}>
            <option value="">— none —</option>{projects.map((p: any) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </Field>
      </div>
      {busy === "plan" && <div className="note"><Spinner size={14} /> The planner is reading your request and proposing a team, limits and permissions…</div>}
      {setup && <>
        {setup.note ? <div className="note">{setup.note}</div> : setup.planned_by ? <div className="note"><Sparkles size={14} /> Proposed by {setup.planned_by}. Change anything, then start.</div> : null}
        {setup.goal && setup.goal !== text && <Field label="The task as the planner wrote it out for a run nobody watches">
          <textarea className="input" rows={4} value={setup.goal} onChange={(e) => setSetup({ ...setup, goal: e.target.value })} /></Field>}
        {setup.done_when && <Field label="Finished when"><input className="input" value={setup.done_when} onChange={(e) => setSetup({ ...setup, done_when: e.target.value })} /></Field>}
        <SetupCard setup={setup} onChange={setSetup} allowable={allowable} />
      </>}
    </Modal>
  );
}

function Limits({ m, onClose, onDone }: any) {
  const { toast } = useApp();
  const [l, setL] = useState<any>({ ...m.limits });
  const save = async () => {
    try { await api.put(`/missions/${m.id}/limits`, l); onDone(); onClose(); } catch (e: any) { toast(e.message, "error"); }
  };
  const num = (key: string, label: string, step = 1) => <Field label={label}><input className="input narrow" type="number" min={0} step={step} value={l[key] ?? 0} onChange={(e) => setL({ ...l, [key]: Number(e.target.value) })} /></Field>;
  return (
    <Modal title={`Limits of “${m.title}”`} onClose={onClose} footer={<Button kind="primary" onClick={save}>Save</Button>}>
      <p className="muted small">Used so far: {m.spent?.steps || 0} steps, {dur(m.spent?.seconds || 0)} of work, {kTokens(m.spent?.tokens || 0)} tokens{m.spent?.cost ? `, US$${m.spent.cost.toFixed(2)} online` : ""}.</p>
      <div className="row gap-m wrap">{num("hours", "Hours of work", 0.5)}{num("steps", "Steps")}{num("step_minutes", "Minutes one step may take")}{num("tokens", "Tokens (0 = no limit)", 1000)}{num("cost", "Online spending in US$ (0 = budget only)", 0.5)}</div>
    </Modal>
  );
}

export default function Missions() {
  const { toast } = useApp();
  const [data, setData] = useState<any>(null);
  const [creating, setCreating] = useState(false);
  const [limits, setLimits] = useState<any>(null);
  const load = () => api.get("/missions").then(setData).catch(() => setData({ missions: [], allowable: {}, default_limits: {} }));
  useEffect(() => { load(); const t = setInterval(load, 15000); return () => clearInterval(t); }, []);
  useEvent((e) => { if (e.kind === "job") load(); });
  if (!data) return <div className="page"><Spinner /></div>;
  const resume = async (m: any, more?: boolean) => {
    try {
      if (more) await api.put(`/missions/${m.id}/limits`, { steps: (m.limits?.steps || 0) + Math.max(5, Math.round((m.limits?.steps || 20) / 2)), hours: (m.limits?.hours || 0) + Math.max(1, (m.limits?.hours || 2) / 2) });
      await api.post(`/chats/${m.chat_id}/run/resume`); load(); go("chat", m.chat_id);
    } catch (e: any) { toast(e.message, "error"); }
  };
  const open = data.missions.filter((m: any) => m.status !== "done");
  const finished = data.missions.filter((m: any) => m.status === "done");
  const row = (m: any) => {
    const st = statusOf(m);
    const lim = m.limits || {}, used = m.spent || {};
    const more = Math.max(5, Math.round((lim.steps || 20) / 2));
    return (
      <div key={m.id} className="card mission-row">
        <div className="row between gap-m">
          <div className="row gap-s ellipsis">{st.icon}<h3 className="ellipsis">{m.title}</h3><Badge kind={st.kind}>{st.label}</Badge>{m.schedule_id && <Badge>scheduled</Badge>}</div>
          <div className="row gap-s">
            <Button small onClick={() => go("chat", m.chat_id)}>Open</Button>
            {m.status === "running" && <Button small kind="danger" icon={<Square size={12} />} onClick={async () => { await api.post(`/chats/${m.chat_id}/stop`); load(); }}>Stop</Button>}
            {m.status === "paused" && m.pause?.kind === "limit" && <Button small kind="primary" icon={<Play size={13} />} onClick={() => resume(m, true)}>Continue with {more} more steps</Button>}
            {m.status === "paused" && m.pause?.kind === "question" && <Button small kind="primary" onClick={() => go("chat", m.chat_id)}>Answer</Button>}
            {["stopped", "interrupted"].includes(m.status) || (m.status === "paused" && !["limit", "question"].includes(m.pause?.kind)) ? <Button small kind="primary" icon={<Play size={13} />} onClick={() => resume(m)}>Continue</Button> : null}
            {m.status !== "done" && <Button small kind="ghost" onClick={() => setLimits(m)}>Limits</Button>}
            {m.status !== "done" && m.status !== "running" && <ConfirmButton small kind="ghost" onConfirm={async () => { await api.post(`/chats/${m.chat_id}/run/dismiss`); load(); }}>End task</ConfirmButton>}
            {desktop && m.folder && <Button small kind="ghost" onClick={() => desktop.openPath(m.folder)}>Files</Button>}
          </div>
        </div>
        <div className="small muted">{fmtTime(m.created)} · {m.done} of {m.steps} steps accepted{m.failed ? ` · ${m.failed} failed` : ""} · used {used.steps || 0} of {lim.steps} steps, {dur(used.seconds || 0)} of {lim.hours} h, {kTokens(used.tokens || 0)} tokens{used.cost ? `, US$${used.cost.toFixed(2)}` : ""}</div>
        {lim.steps > 0 && <div className="run-bar"><span style={{ width: `${Math.min(100, Math.max(1, 100 * Math.max((used.steps || 0) / lim.steps, (used.seconds || 0) / ((lim.hours || 1) * 3600))))}%` }} /></div>}
        {m.pause && <div className="small mission-pause">{m.pause.kind === "question" ? "The planner asks: " : "Paused: "}{m.pause.text}</div>}
        {m.understanding && <div className="small clamp-2" title={m.understanding}><b>Understanding:</b> {m.understanding}</div>}
        {m.last && <div className="small muted clamp-2" title={m.last.done}>Latest handoff ({m.last.id} {m.last.name}, {m.last.status}): {m.last.done || m.last.found}</div>}
        <div className="small muted">Team: {(m.team || []).map((a: any) => a.name).join(", ") || "one general agent"}{m.allow?.length ? ` · without asking: ${m.allow.join(", ")}` : " · asks before every change"}</div>
      </div>
    );
  };
  return (
    <div className="page">
      <PageHeader title="Long-running tasks" subtitle="Work that goes on until a problem is solved. A planner gives one step at a time to an agent, reads the handoff it leaves and decides the next step from it, changing the plan when the findings call for it. Tasks keep going when the window is closed, and continue by themselves after a restart."
        actions={<Button kind="primary" icon={<Plus size={14} />} onClick={() => setCreating(true)}>New long-running task</Button>} />
      {data.missions.length === 0 && <Empty icon={<Rocket size={32} />} title="No long-running tasks yet">Start one here, or pick the “Long-running task” harness in any chat. Good for research, building or fixing something, or checking a system you own, when the next step depends on what was just found.</Empty>}
      {open.map(row)}
      {finished.length > 0 && <h3 className="section-title">Finished</h3>}
      {finished.map(row)}
      {creating && <NewTask onClose={() => { setCreating(false); load(); }} allowable={data.allowable} defaults={data.default_limits} />}
      {limits && <Limits m={limits} onClose={() => setLimits(null)} onDone={load} />}
    </div>
  );
}
