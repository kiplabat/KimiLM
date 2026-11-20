import React, { useEffect, useRef, useState } from "react";
import { Bot, ChevronDown, Cloud, Cpu, CornerDownRight, Eye, FolderOpen, Gauge, ListPlus, Paperclip, Plug, Plus, Send, Sparkles, Square, Users, X, Lightbulb } from "lucide-react";
import { api, desktop, fmtBytes } from "../api";
import { useApp, useShown } from "../store";
import { Button, Toggle } from "./ui";
import { DictateButton } from "./Audio";

function Popover({ label, icon, children, title }: any) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const close = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as any)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);
  return (
    <div className="popover-wrap" ref={ref}>
      <button className="chip" onClick={() => setOpen(!open)} title={title}>{icon}<span className="ellipsis">{label}</span><ChevronDown size={12} /></button>
      {open && <div className="popover">{typeof children === "function" ? children(() => setOpen(false)) : children}</div>}
    </div>
  );
}

export function HarnessPicker({ value, onChange }: any) {
  const { boot } = useApp();
  const list = (boot?.harnesses || []).filter((h: any) => h.enabled && !h.error);
  const current = list.find((h: any) => h.id === value) || list.find((h: any) => h.id === (boot?.settings?.default_harness || "agent"));
  return (
    <Popover label={current?.name || "Harness"} icon={<Bot size={14} />} title="Harness: how the agent works on your request">
      {(close: any) => (
        <div className="menu">
          {list.map((h: any) => (
            <div key={h.id} className={`menu-item${current?.id === h.id ? " selected" : ""}`} onClick={() => { onChange(h.id); close(); }}>
              <div className="menu-title">{h.name} {h.type === "pipeline" && <span className="badge badge-neutral">pipeline</span>}</div>
              <div className="menu-sub">{h.description}</div>
            </div>
          ))}
        </div>
      )}
    </Popover>
  );
}

export const canSee = (m: any) => !!(m?.settings?.mmproj && m?.settings?.vision !== false);

const cloudSees = (m: any) => !!m?.caps?.vision;
const price = (m: any) => (m?.price?.out !== undefined ? `$${Number(m.price.in).toFixed(2)} / $${Number(m.price.out).toFixed(2)} per 1M` : "price unknown");

export function ModelPicker({ value, onChange, allowDefault, chatId, effective }: any) {
  const { boot, toast } = useApp() as any;
  const models = boot?.models || [];
  const online = (boot?.cloud_models || []).filter((m: any) => m.enabled);
  const privacy = boot?.online?.privacy;
  const [chatOnline, setChatOnline] = useState<boolean | null>(null);
  useEffect(() => { if (chatId && privacy === "local_unless_on") api.get(`/chats/${chatId}/online`).then((r) => setChatOnline(r.allowed)).catch(() => {}); }, [chatId, privacy]);
  const chosen = value || (allowDefault ? "" : boot?.default_model);
  const local = models.find((m: any) => m.id === chosen);
  const cloud = online.find((m: any) => m.id === chosen) || (boot?.cloud_models || []).find((m: any) => m.id === chosen);
  const label = chosen === "auto" ? (effective?.auto && effective?.model_name ? `Auto · ${effective.model_name}` : "Auto") : local ? local.name : cloud ? cloud.name : (allowDefault ? "Same as the chat" : models.length || online.length ? "Auto" : "No model yet");
  const icon = chosen === "auto" ? <Sparkles size={14} /> : cloud ? <Cloud size={14} /> : canSee(local) ? <Eye size={14} /> : <Cpu size={14} />;
  return (
    <Popover label={label} icon={icon} title={effective?.auto && effective?.model_name ? `Auto chose ${effective.model_name}${effective.route_reason ? `: ${effective.route_reason}` : ""}` : "Model: Auto, one on this computer, or an online one. The eye marks models that can see images and videos."}>
      {(close: any) => (
        <div className="menu">
          {allowDefault && <div className={`menu-item${!value ? " selected" : ""}`} onClick={() => { onChange(""); close(); }}><div className="menu-title">Same as the chat</div></div>}
          <div className={`menu-item${chosen === "auto" ? " selected" : ""}`} onClick={() => { onChange("auto"); close(); }}>
            <div className="menu-title"><Sparkles size={13} className="accent" /> Auto</div>
            <div className="menu-sub">KimiLM picks per request: the strongest model for planning and hard reasoning, a local model for routine work.</div>
          </div>
          <div className="menu-group">On this computer · free, private</div>
          {models.length === 0 && <div className="menu-item" onClick={() => (location.hash = "/models/discover")}>Download a model…</div>}
          {models.map((m: any) => (
            <div key={m.id} className={`menu-item${chosen === m.id ? " selected" : ""}`} onClick={() => { onChange(m.id); close(); }}>
              <div className="menu-title">{m.name} {canSee(m) && <Eye size={13} className="accent" />}</div>
              <div className="menu-sub">{m.quant} · {fmtBytes(m.size)} · {(m.settings?.compute || "auto").toUpperCase()}{canSee(m) ? " · sees images" : ""}{m.id === boot.default_model ? " · default" : ""}</div>
            </div>
          ))}
          <div className="menu-group">Online · billed per use</div>
          {online.length === 0 && <div className="menu-item" onClick={() => { location.hash = "/models/online"; close(); }}><div className="menu-sub">Add GPT, Claude, Gemini and more with an API key…</div></div>}
          {online.map((m: any) => (
            <div key={m.id} className={`menu-item${chosen === m.id ? " selected" : ""}${m.ready ? "" : " dim"}`} onClick={() => { onChange(m.id); close(); }}>
              <div className="menu-title"><Cloud size={13} /> {m.name} {cloudSees(m) && <Eye size={13} className="accent" />}</div>
              <div className="menu-sub">{m.provider_name} · {m.role === "strategist" ? "strategist" : m.role} · {price(m)}{m.ready ? "" : " · needs its key"}{m.id === boot.default_model ? " · default" : ""}</div>
            </div>
          ))}
          {privacy === "local_unless_on" && chatId && chatOnline !== null && (
            <div className="menu-item pad">
              <Toggle checked={chatOnline} label="Allow online models in this chat" onChange={async (v: boolean) => {
                try { await api.put(`/chats/${chatId}/online`, { allowed: v }); setChatOnline(v); } catch (e: any) { toast(e.message, "error"); } }} />
            </div>
          )}
        </div>
      )}
    </Popover>
  );
}

// Per-chat limits: reply length, steps, time, temperature, searches. Empty = the harness's own setting.
const LIMITS = [
  { key: "ctx", label: "Context (tokens)", help: "How much the model holds at once: the conversation, the tools and the files it read. Larger needs more memory. Empty: sized for this chat.", ph: "auto", step: 1024 },
  { key: "max_reply_tokens", label: "Reply length (tokens)", help: "Longest single reply. About 750 words per 1,000 tokens.", ph: "2048" },
  { key: "max_steps", label: "Steps", help: "Model calls with tools before it must write up.", ph: "harness" },
  { key: "time_limit_minutes", label: "Time limit (minutes)", help: "The agent stops and reports what it found.", ph: "30" },
  { key: "max_searches", label: "Web searches per reply", help: "Stops search loops.", ph: "6" },
  { key: "max_tool_calls", label: "Tool calls per reply", help: "", ph: "40" },
  { key: "temperature", label: "Creativity (0–1.5)", help: "Low = precise, high = varied.", ph: "harness", step: 0.1 },
];

const BY: any = { you: "set by you", harness: "from the harness", settings: "from Settings", default: "default", model: "the model's setting", sized: "sized for this chat", run: "sized for the planned run" };
const short = (n: number) => n >= 1000 ? `${Math.round(n / 1024)}k` : String(n);

export function LimitsEditor({ value = {}, onChange, compact, effective }: any) {
  return (
    <div className={compact ? "limits-grid compact" : "limits-grid"}>
      {LIMITS.map((l) => (
        <label key={l.key} className="limit" title={l.help}>
          <span>{l.label}{effective?.[l.key] != null && value[l.key] == null && <span className="muted small"> · {l.key === "ctx" ? Number(effective[l.key]).toLocaleString() : effective[l.key]} ({BY[effective[`${l.key}_by`]] || effective[`${l.key}_by`] || "in use"})</span>}</span>
          <input className="input narrow" type="number" min={0} step={l.step || 1} placeholder={effective?.[l.key] != null ? String(effective[l.key]) : l.ph} value={value[l.key] ?? ""}
            onChange={(e) => onChange({ ...value, [l.key]: e.target.value === "" ? undefined : Number(e.target.value) })} />
        </label>
      ))}
    </div>
  );
}

export function LimitsPicker({ value = {}, onChange, effective }: any) {
  const [draft, setDraft] = useState<any>(value || {});
  useEffect(() => setDraft(value || {}), [JSON.stringify(value || {})]);
  const n = Object.keys(value || {}).filter((k) => k !== "tools").length;
  const ctx = (value || {}).ctx || effective?.ctx;
  const steps = (value || {}).max_steps || effective?.max_steps;
  const label = [ctx ? `${short(Number(ctx))} context` : "", steps ? `${steps} steps` : ""].filter(Boolean).join(" · ") || "Limits";
  return (
    <Popover label={n ? `${label} · ${n} set` : label} icon={<Gauge size={14} />} title="Limits for this chat: context, reply length, steps, time… (what is in use now, and who set it)">
      {(close: any) => (
        <div className="menu pad">
          <div className="menu-sub">{effective ? <>In use{effective.model_name ? <> for <b>{effective.model_name}</b></> : null}{effective.harness ? <> with the <b>{effective.harness}</b> harness</> : null}. Leave a box empty to keep that value; type one to set it for this chat.{effective.ctx_max ? <> The model can hold up to {Number(effective.ctx_max).toLocaleString()} tokens.</> : null}</> : "Leave empty to use the harness's own settings."}</div>
          <LimitsEditor value={draft} onChange={setDraft} effective={effective} />
          <div className="row gap-s"><Button small kind="primary" onClick={() => { onChange(draft); close(); }}>Save</Button>
            {n > 0 && <Button small kind="ghost" onClick={() => { onChange({}); close(); }}>Clear</Button>}</div>
        </div>
      )}
    </Popover>
  );
}

// A team for the next message: several agents working at the same time, each with its own model and limits.
export function TeamPicker({ team, onChange }: any) {
  const { boot } = useApp();
  const harnesses = (boot?.harnesses || []).filter((h: any) => h.enabled && !h.error && h.type === "loop" && h.mode !== "team");
  const on = team && team.length > 0;
  const update = (i: number, values: any) => onChange(team.map((a: any, j: number) => (j === i ? { ...a, ...values } : a)));
  const add = () => onChange([...(team || []), { name: `Agent ${(team?.length || 0) + 1}`, harness: "agent", model: "", task: "", limits: {} }]);
  return (
    <Popover label={on ? `Team of ${team.length}` : "Team"} icon={<Users size={14} />} title="Send the next message to a team of agents that work in parallel">
      <div className="menu pad team-builder">
        <div className="menu-sub">Agents work at the same time, each in its own chat, then a lead combines their results. Leave a task empty and the lead splits the work. Agents on different models run truly in parallel when memory allows; online models always can.</div>
        {(team || []).map((a: any, i: number) => (
          <div key={i} className="team-agent">
            <div className="row gap-s">
              <input className="input" value={a.name} onChange={(e) => update(i, { name: e.target.value })} />
              <button className="icon-btn" title="Remove" onClick={() => onChange(team.filter((_: any, j: number) => j !== i))}><X size={13} /></button>
            </div>
            <div className="row gap-s wrap">
              <select className="input" value={a.harness} onChange={(e) => update(i, { harness: e.target.value })}>
                {harnesses.map((h: any) => <option key={h.id} value={h.id}>{h.name}</option>)}
              </select>
              <select className="input" value={a.model || ""} onChange={(e) => update(i, { model: e.target.value })} title="Model">
                <option value="">Same model as the chat</option>
                <optgroup label="By role (picked for you)">
                  <option value="role:worker">Worker — on this computer when possible</option>
                  <option value="role:strategist">Strategist — the strongest model</option>
                  <option value="role:vision">Vision — can see images</option>
                </optgroup>
                <optgroup label="On this computer">{(boot?.models || []).map((m: any) => <option key={m.id} value={m.id}>{m.name}{canSee(m) ? " (sees images)" : ""}</option>)}</optgroup>
                {(boot?.cloud_models || []).some((m: any) => m.enabled) && <optgroup label="Online">
                  {(boot?.cloud_models || []).filter((m: any) => m.enabled).map((m: any) => <option key={m.id} value={m.id}>{m.name} ({m.provider_name})</option>)}</optgroup>}
              </select>
            </div>
            <textarea className="input" rows={2} placeholder="Task (optional — the lead decides)" value={a.task} onChange={(e) => update(i, { task: e.target.value })} />
            <details><summary className="small muted">Limits</summary><LimitsEditor compact value={a.limits || {}} onChange={(v: any) => update(i, { limits: v })} /></details>
          </div>
        ))}
        <div className="row gap-s">
          <Button small icon={<Plus size={13} />} onClick={add}>Add agent</Button>
          {on && <Button small kind="ghost" onClick={() => onChange([])}>No team</Button>}
        </div>
      </div>
    </Popover>
  );
}

export function ConnectorPicker({ value = [], onChange, system = true }: any) {
  const [list, setList] = useState<any[]>([]);
  useEffect(() => { api.get("/connectors").then((r) => setList(r.connectors.filter((c: any) => c.enabled))); }, []);
  // connectors of the operating system (KimiOS) are in every agent chat: shown ticked, not to be unticked here
  const os = system ? list.filter((c: any) => c.system && c.ready !== false) : [];
  const chosen = value.filter((id: string) => !os.some((c: any) => c.id === id));
  const names = [...os.map((c: any) => c.name), ...(chosen.length ? [`${chosen.length} more`] : [])];
  const label = names.length ? (os.length && !chosen.length ? os.map((c: any) => c.name).join(", ") : names.join(" + ")) : "No connectors";
  return (
    <Popover label={label} icon={<Plug size={14} />} title="Connectors (MCP) this chat may use">
      <div className="menu">
        {list.length === 0 && <div className="menu-sub pad">No connectors yet. Add them in Plugins & connectors.</div>}
        {list.map((c: any) => {
          const always = os.some((o: any) => o.id === c.id);
          return (
            <label key={c.id} className="menu-item row gap-s" title={always ? "Part of the operating system: every agent chat has it. Switch it off in Plugins & connectors." : undefined}>
              <input type="checkbox" checked={always || value.includes(c.id)} disabled={always || !c.ready}
                onChange={(e) => onChange(e.target.checked ? [...value, c.id] : value.filter((x: string) => x !== c.id))} />
              <div><div className="menu-title">{c.name}</div><div className="menu-sub">{always ? "Part of the operating system — always on" : c.ready ? (c.description || c.source) : "Needs its key — set it up in Plugins"}</div></div>
            </label>
          );
        })}
      </div>
    </Popover>
  );
}

export function FolderPicker({ value = [], onChange, inherited = [] }: any) {
  const [typed, setTyped] = useState("");
  const add = (p: string) => p && !value.includes(p) && onChange([...value, p]);
  const label = value.length + inherited.length ? `${value.length + inherited.length} folder${value.length + inherited.length > 1 ? "s" : ""}` : "No folders";
  return (
    <Popover label={label} icon={<FolderOpen size={14} />} title="Folders the agent may read and write">
      <div className="menu pad">
        {inherited.map((f: string) => <div key={f} className="folder-row muted" title="From the project">{f}</div>)}
        {value.map((f: string) => (
          <div key={f} className="folder-row"><span className="ellipsis">{f}</span>
            <button className="icon-btn" onClick={() => onChange(value.filter((x: string) => x !== f))}><X size={12} /></button></div>
        ))}
        {desktop ? <Button small onClick={async () => add(await desktop.pickFolder())}>Add a folder…</Button> :
          <div className="row gap-s"><input className="input" value={typed} onChange={(e) => setTyped(e.target.value)} placeholder="Folder path" />
            <Button small onClick={() => { add(typed); setTyped(""); }}>Add</Button></div>}
        <div className="menu-sub">The agent can only touch files inside these folders (and the chat's own workspace). Changes ask you first.</div>
      </div>
    </Popover>
  );
}

export function Composer({ onSend, running, onStop, status, placeholder, autoFocus, options, initialText, followUps }: any) {
  const [text, setText] = useState(initialText || "");
  const [files, setFiles] = useState<File[]>([]);
  const ref = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const shown = useShown();
  useEffect(() => { if (autoFocus && shown) ref.current?.focus(); }, [shown]);   // also when its screen comes back
  useEffect(() => { if (initialText) setText(initialText); }, [initialText]);
  useEffect(() => {
    const el = ref.current;
    if (el) { el.style.height = "auto"; el.style.height = Math.min(el.scrollHeight, 260) + "px"; }
  }, [text]);
  const send = async (mode?: string) => {
    if ((running && !followUps) || (!text.trim() && !files.length)) return;
    const t = text, f = files;
    setText(""); setFiles([]);
    try { await onSend(t, f, mode); } catch (e) { setText(t); setFiles(f); }
  };
  const hasText = !!text.trim() || files.length > 0;
  return (
    <div className="composer" onDragOver={(e) => e.preventDefault()}
      onDrop={(e) => { e.preventDefault(); setFiles([...files, ...Array.from(e.dataTransfer.files)]); }}>
      {files.length > 0 && (
        <div className="attach-row">
          {files.map((f, i) => (
            <span key={i} className="attach"><Paperclip size={12} />{f.name} <span className="muted">{fmtBytes(f.size)}</span>
              <button className="icon-btn" onClick={() => setFiles(files.filter((_, j) => j !== i))}><X size={12} /></button></span>
          ))}
        </div>
      )}
      <textarea ref={ref} value={text} rows={1}
        placeholder={running && followUps ? "Add a follow-up… Enter queues it for when this reply ends; Steer hands it to the agent now"
          : placeholder || "Message the agent… (Shift+Enter for a new line)"}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(running ? "queue" : undefined); } }} />
      <div className="composer-bar">
        <div className="row gap-s wrap">
          <button className="icon-btn" title="Attach files" onClick={() => fileRef.current?.click()}><Paperclip size={16} /></button>
          <DictateButton onText={(said: string) => setText((t: string) => (t ? t.trimEnd() + " " : "") + said)} />
          <input ref={fileRef} type="file" multiple hidden onChange={(e) => { setFiles([...files, ...Array.from(e.target.files || [])]); e.target.value = ""; }} />
          {options}
        </div>
        <div className="row gap-s">
          {status && <span className="status-text">{status}</span>}
          {running && followUps && hasText && <>
            <Button small icon={<CornerDownRight size={13} />} onClick={() => send("steer")} title="Give this to the agent at its next step, without waiting for the reply to end">Steer</Button>
            <Button small kind="primary" icon={<ListPlus size={13} />} onClick={() => send("queue")} title="Send this as soon as the current reply ends">Queue</Button>
          </>}
          {running ? <Button kind="danger" small icon={<Square size={13} />} onClick={onStop}>Stop</Button>
            : <Button kind="primary" small icon={<Send size={14} />} onClick={() => send()} disabled={!hasText}>Send</Button>}
        </div>
      </div>
    </div>
  );
}

export function ThinkingToggle({ value, onChange }: any) {
  return (
    <span className="chip chip-toggle" title="Let the model think before answering (slower, sometimes better). Works with models that support it.">
      <Lightbulb size={14} /><Toggle checked={value} onChange={onChange} label="Think" />
    </span>
  );
}

export async function uploadAll(chatId: string, files: File[]) {
  const out = [];
  for (const f of files) out.push(await api.upload(`/chats/${chatId}/upload`, f));
  return out;
}
