import React, { useEffect, useState } from "react";
import { CalendarClock, Pencil, Play, Plus, Sparkles, Trash2 } from "lucide-react";
import { api, fmtTime } from "../api";
import { go, useApp, useEvent } from "../store";
import { Badge, Button, ConfirmButton, Empty, Field, Modal, PageHeader, Spinner, Toggle } from "../components/ui";
import { ConnectorPicker, HarnessPicker, ModelPicker } from "../components/Pickers";
import { EMPTY_SETUP, SetupCard, limitsText } from "../components/Setup";

function Editor({ s, onClose, onDone }: any) {
  const { toast, projects } = useApp();
  const [f, setF] = useState<any>(s ? { ...s, when: s.when } : { name: "", prompt: "", when: "every day at 09:00", harness: "planner", model: "", project_id: null,
    chat_mode: "new", connectors: [], auto_approve: false, catch_up: true, only_on_ac: false, max_runs_per_day: 0, enabled: true });
  const [preview, setPreview] = useState<any>(null);
  const [ask, setAsk] = useState("");
  const [planning, setPlanning] = useState(false);
  const [allowable, setAllowable] = useState<Record<string, string>>({});
  const [note, setNote] = useState("");
  useEffect(() => { api.get("/missions").then((r) => setAllowable(r.allowable)).catch(() => {}); }, []);
  const long = f.setup?.kind === "mission";
  // One sentence → the planner writes the task out and proposes when it runs, the team, the limits and what it
  // may do without asking. Everything it fills in stays editable; nothing is saved until Save.
  const plan = async () => {
    setPlanning(true);
    try {
      const { setup, allowable: a } = await api.post("/missions/plan", { text: ask, scheduled: true, model: f.model || undefined });
      setAllowable(a);
      setNote(setup.note || (setup.planned_by ? `Set up by ${setup.planned_by}. Change anything, then save.` : ""));
      setF({ ...f, name: f.name || setup.name, prompt: setup.goal, when: setup.when || f.when,
        setup: { kind: setup.kind, done_when: setup.done_when, team: setup.team, limits: setup.limits, allow: setup.allow, outline: setup.outline } });
    } catch (e: any) { toast(e.message, "error"); }
    setPlanning(false);
  };
  useEffect(() => {
    const t = setTimeout(() => api.post("/schedules/parse", { when: f.when }).then(setPreview).catch((e) => setPreview({ error: e.message })), 300);
    return () => clearTimeout(t);
  }, [f.when]);
  const save = async () => {
    try {
      const body = { ...f, cron: preview?.cron };
      if (s) await api.put(`/schedules/${s.id}`, body); else await api.post("/schedules", body);
      toast("Scheduled task saved", "ok"); onDone(); onClose();
    } catch (e: any) { toast(e.message, "error"); }
  };
  return (
    <Modal wide title={s ? "Edit scheduled task" : "New scheduled task"} onClose={onClose} footer={<Button kind="primary" disabled={!f.name || !preview?.cron} onClick={save}>Save</Button>}>
      <Field label="Describe it and let the planner set it up" help={note || "Optional. From one sentence the planner writes the task out for a run nobody watches and proposes the schedule, the team, the limits and what it may do without asking. You can change all of it below."}>
        <div className="row gap-s"><input className="input grow" value={ask} onChange={(e) => setAsk(e.target.value)} onKeyDown={(e) => e.key === "Enter" && ask.trim() && !planning && plan()}
          placeholder="e.g. every weekday morning, check the tariff pages of the three utilities and tell me what changed" />
          <Button icon={<Sparkles size={14} />} disabled={!ask.trim() || planning} busy={planning} onClick={plan}>Set it up</Button></div>
      </Field>
      <Field label="Name"><input className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="Morning data check" /></Field>
      <Field label="What should it do?" help="Write it for a run with nobody watching: the goal, which folders or sources, what to produce, what to do if there is nothing new.">
        <textarea className="input" rows={5} value={f.prompt} onChange={(e) => setF({ ...f, prompt: e.target.value })} />
      </Field>
      <Field label="When" help={preview?.error ? <span className="bad">{preview.error}</span> : preview ? `${preview.when} · next: ${new Date(preview.next).toLocaleString()}` : ""}>
        <input className="input" value={f.when} onChange={(e) => setF({ ...f, when: e.target.value })} placeholder="every day at 08:30 · weekdays at 9 · every 3 hours · every monday at 10:00" />
      </Field>
      <div className="row gap-m wrap">
        <Field label="Harness"><HarnessPicker value={f.harness} onChange={(v: string) => setF({ ...f, harness: v })} /></Field>
        <Field label="Model"><ModelPicker value={f.model} onChange={(v: string) => setF({ ...f, model: v })} /></Field>
        <Field label="Connectors"><ConnectorPicker value={f.connectors || []} onChange={(v: string[]) => setF({ ...f, connectors: v })} /></Field>
        <Field label="Project">
          <select className="input" value={f.project_id || ""} onChange={(e) => setF({ ...f, project_id: e.target.value || null })}>
            <option value="">— none —</option>{projects.map((p: any) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </Field>
        <Field label="Results">
          <select className="input" value={f.chat_mode} onChange={(e) => setF({ ...f, chat_mode: e.target.value })}>
            <option value="new">A new chat each run</option><option value="same">Keep adding to one chat</option>
          </select>
        </Field>
      </div>
      <Field label="How it runs" help={long ? "A planner gives one step at a time to an agent of the team and decides the next step from what it hands back, within the limits below." : "One reply by the chosen harness (a multi-step request is still planned and run as steps)."}>
        <select className="input narrow-auto" value={long ? "mission" : "reply"} onChange={(e) => setF({ ...f, setup: e.target.value === "mission" ? { ...EMPTY_SETUP, ...(f.setup || {}), kind: "mission" } : { ...(f.setup || {}), kind: "reply" } })}>
          <option value="reply">A single run</option><option value="mission">A long-running task (team, limits, step by step)</option>
        </select>
      </Field>
      {long ? <SetupCard setup={f.setup} onChange={(v: any) => setF({ ...f, setup: v })} allowable={allowable} />
        : <Field label="It may do this without asking" help="Anything else waits for you and sends a notification. Leave all off to be asked about every change.">
          <div className="setup-allow">{Object.entries(allowable).map(([key, label]) => (
            <label key={key} className="row gap-s small"><input type="checkbox" checked={(f.setup?.allow || []).includes(key)}
              onChange={(e) => { const allow = f.setup?.allow || []; setF({ ...f, setup: { ...(f.setup || {}), kind: "reply", allow: e.target.checked ? [...allow, key] : allow.filter((a: string) => a !== key) } }); }} /> {label as string}</label>))}</div>
        </Field>}
      <div className="stack-s">
        <Toggle checked={f.auto_approve} onChange={(v: boolean) => setF({ ...f, auto_approve: v })} label="Approve everything automatically (overrides the list above) — use only for tasks you trust completely" />
        <Toggle checked={f.catch_up} onChange={(v: boolean) => setF({ ...f, catch_up: v })} label="If the computer was off at the time, run once when the app starts" />
        <Toggle checked={f.only_on_ac} onChange={(v: boolean) => setF({ ...f, only_on_ac: v })} label="Only when plugged in" />
        <Field label="Maximum runs per day (0 = no limit)"><input className="input narrow" type="number" min={0} value={f.max_runs_per_day} onChange={(e) => setF({ ...f, max_runs_per_day: Number(e.target.value) })} /></Field>
      </div>
    </Modal>
  );
}

export default function Schedules() {
  const { toast } = useApp();
  const [list, setList] = useState<any[] | null>(null);
  const [edit, setEdit] = useState<any>(null);
  const load = () => api.get("/schedules").then((r) => setList(r.schedules));
  useEffect(() => { load(); }, []);
  useEvent((e) => { if (["schedules_changed", "schedules_tick", "job"].includes(e.kind)) load(); });
  if (!list) return <div className="page"><Spinner /></div>;
  return (
    <div className="page">
      <PageHeader title="Schedules" subtitle="Tasks that run on their own, each in its own chat, with the harness and model you choose. They run while KimiLM is open (it can start with Windows and stay in the tray — see Settings)."
        actions={<Button kind="primary" icon={<Plus size={14} />} onClick={() => setEdit({})}>New scheduled task</Button>} />
      {list.length === 0 && <Empty icon={<CalendarClock size={32} />} title="No scheduled tasks yet">Create one here, ask the agent in any chat ("every morning, …"), or add a ready-made one from a plugin.</Empty>}
      {list.map((s) => (
        <div key={s.id} className="card schedule-row">
          <div className="grow">
            <div className="row gap-s"><h3>{s.name}</h3>{s.running && <Badge kind="accent">running</Badge>}{s.setup?.kind === "mission" && <Badge>long-running</Badge>}{s.auto_approve ? <Badge>auto-approve</Badge> : null}</div>
            <div className="muted small">{s.when} · next {s.enabled ? fmtTime(s.next_run) : "—"}{s.last_run ? ` · last ${fmtTime(s.last_run)}` : ""} · {s.harness || "default harness"}{s.model ? ` · ${s.model}` : ""}</div>
            <div className="small clamp">{s.prompt}</div>
            {s.setup?.kind === "mission" && <div className="small muted">Team: {(s.setup.team || []).map((a: any) => a.name).join(", ")} · {limitsText(s.setup.limits)}</div>}
            {s.setup?.allow?.length > 0 && !s.auto_approve && <div className="small muted">Without asking: {s.setup.allow.join(", ")}</div>}
          </div>
          <div className="row gap-s">
            <Toggle checked={!!s.enabled} onChange={async (v: boolean) => { await api.put(`/schedules/${s.id}`, { enabled: v }); load(); }} />
            <Button small icon={<Play size={13} />} disabled={s.running} onClick={async () => { try { const r = await api.post(`/schedules/${s.id}/run`); go("chat", r.chat_id); } catch (e: any) { toast(e.message, "error"); } }}>Run now</Button>
            <Button small icon={<Pencil size={13} />} onClick={() => setEdit(s)}>Edit</Button>
            <ConfirmButton small icon={<Trash2 size={13} />} onConfirm={async () => { await api.del(`/schedules/${s.id}`); load(); }}>Delete</ConfirmButton>
          </div>
        </div>
      ))}
      {edit && <Editor s={edit.id ? edit : null} onClose={() => setEdit(null)} onDone={load} />}
    </div>
  );
}
