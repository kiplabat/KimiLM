import React from "react";
import { Plus, ShieldCheck, Trash2, Users } from "lucide-react";
import { useApp } from "../store";
import { Button, Field } from "./ui";

// The set-up of a long-running or scheduled task: who is on the team, the limits, and what it may do without
// asking. A planner proposes it from one sentence (POST /missions/plan); everything here can be changed before
// the task starts, and nothing runs until the user confirms.
export const EMPTY_SETUP = { kind: "mission", team: [], limits: { hours: 8, steps: 40, step_minutes: 45 }, allow: [], outline: [] };

export function limitsText(l: any) {
  if (!l) return "";
  return `up to ${l.hours} h of work · ${l.steps} step${l.steps === 1 ? "" : "s"} · ${l.step_minutes} min a step`;
}

export function SetupCard({ setup, onChange, allowable, compact }: { setup: any; onChange: (s: any) => void; allowable: Record<string, string>; compact?: boolean }) {
  const { boot } = useApp();
  const harnesses = (boot?.harnesses || []).filter((h: any) => h.enabled && !h.error && h.type === "loop" && !["team", "mission"].includes(h.mode));
  const team: any[] = setup.team || [];
  const limits = setup.limits || {};
  const allow: string[] = setup.allow || [];
  const setTeam = (i: number, patch: any) => onChange({ ...setup, team: team.map((m, j) => (j === i ? { ...m, ...patch } : m)) });
  const setLimit = (key: string, value: string) => onChange({ ...setup, limits: { ...limits, [key]: value === "" ? "" : Number(value) } });
  return (
    <div className="setup-card">
      <div className="setup-title"><Users size={14} /> Team <span className="muted small">the planner gives each step to one of them, and adds an agent when none fits</span></div>
      {team.map((m, i) => (
        <div key={i} className="setup-member">
          <input className="input" style={{ width: 150 }} value={m.name} placeholder="Name" onChange={(e) => setTeam(i, { name: e.target.value })} />
          <select className="input narrow-auto" value={m.harness || "agent"} onChange={(e) => setTeam(i, { harness: e.target.value })} title="How this agent works">
            {harnesses.map((h: any) => <option key={h.id} value={h.id}>{h.name}</option>)}
          </select>
          {!compact && <input className="input grow" value={m.about || ""} placeholder="What this agent is for" onChange={(e) => setTeam(i, { about: e.target.value })} />}
          <button className="icon-btn" title="Remove from the team" onClick={() => onChange({ ...setup, team: team.filter((_, j) => j !== i) })}><Trash2 size={14} /></button>
        </div>
      ))}
      {team.length === 0 && <div className="small muted">No team given: one general agent does every step.</div>}
      <div><Button small kind="ghost" icon={<Plus size={13} />} onClick={() => onChange({ ...setup, team: [...team, { name: "", harness: "agent", about: "" }] })}>Add an agent</Button></div>
      <div className="setup-title">Limits <span className="muted small">reaching one pauses the task between two steps and tells you; nothing is lost</span></div>
      <div className="row gap-m wrap">
        <Field label="Hours of work"><input className="input narrow" type="number" min={0.25} step={0.5} value={limits.hours ?? ""} onChange={(e) => setLimit("hours", e.target.value)} /></Field>
        <Field label="Steps"><input className="input narrow" type="number" min={1} value={limits.steps ?? ""} onChange={(e) => setLimit("steps", e.target.value)} /></Field>
        <Field label="Minutes one step may take"><input className="input narrow" type="number" min={5} value={limits.step_minutes ?? ""} onChange={(e) => setLimit("step_minutes", e.target.value)} /></Field>
        <Field label="Online spending (US$, 0 = the monthly budget only)"><input className="input narrow" type="number" min={0} step={0.5} value={limits.cost ?? 0} onChange={(e) => setLimit("cost", e.target.value)} /></Field>
      </div>
      <div className="setup-title"><ShieldCheck size={14} /> It may do this without asking <span className="muted small">anything else waits for you and sends a notification</span></div>
      <div className="setup-allow">
        {Object.entries(allowable || {}).map(([key, label]) => (
          <label key={key} className="row gap-s small"><input type="checkbox" checked={allow.includes(key)}
            onChange={(e) => onChange({ ...setup, allow: e.target.checked ? [...allow, key] : allow.filter((a) => a !== key) })} /> {label}</label>
        ))}
      </div>
      {setup.outline?.length > 0 && <div className="small muted">Expected phases (the planner changes them as the work goes on): {setup.outline.join(" → ")}</div>}
    </div>
  );
}
