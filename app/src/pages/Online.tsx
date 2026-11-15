// Online models (2.3.0): providers and their keys, the models KimiLM may use, roles for Auto, prices, the monthly
// budget, what was spent, and the privacy rule for sending things online.
import React, { useEffect, useMemo, useState } from "react";
import { Cloud, Cpu, ExternalLink, Eye, KeyRound, Plus, RefreshCw, Search, Shield, Star, Trash2, Wallet } from "lucide-react";
import { api, desktop, enc } from "../api";
import { useApp, useEvent } from "../store";
import { Badge, Button, ConfirmButton, Field, Modal, Progress, Spinner, Toggle } from "../components/ui";

export const money = (n: any, digits = 2) => (n === null || n === undefined || isNaN(Number(n)) ? "—" : `$${Number(n).toFixed(digits)}`);
const per1m = (p: any) => (p && p.in !== undefined && p.out !== undefined ? `${money(p.in)} in · ${money(p.out)} out per 1M tokens` : "price unknown");
const ROLE_LABEL: any = { strategist: "Strategist", worker: "Worker", both: "Both", off: "Not used by Auto" };
const ROLE_HELP = "Auto gives planning, strategy and hard reasoning to a strategist, and routine parts to workers (models on this computer first).";

function open(url?: string) {
  if (!url) return;
  if (desktop) desktop.openExternal(url); else window.open(url, "_blank");
}

function Budget({ data, reload }: any) {
  const { toast } = useApp();
  const u = data.usage;
  const [cap, setCap] = useState<string>(String(data.settings.cloud_budget_monthly ?? 10));
  useEffect(() => setCap(String(data.settings.cloud_budget_monthly ?? 10)), [data.settings.cloud_budget_monthly]);
  const save = async () => {
    try { await api.put("/online/settings", { cloud_budget_monthly: Number(cap) || 0 }); reload(); toast("Budget saved", "ok"); }
    catch (e: any) { toast(e.message, "error"); }
  };
  const pct = u.budget > 0 ? Math.min(100, (u.spent / u.budget) * 100) : 0;
  return (
    <div className="card">
      <div className="row between wrap">
        <h3><Wallet size={16} /> Spending this month ({u.month})</h3>
        <div className="row gap-s">
          <Field label="Monthly budget (USD)" inline>
            <input className="input narrow" type="number" min={0} step={1} value={cap} onChange={(e) => setCap(e.target.value)} />
          </Field>
          <Button small kind="primary" onClick={save}>Save</Button>
        </div>
      </div>
      <div className="row gap-s mt"><b>{money(u.spent, u.spent < 1 ? 4 : 2)}</b><span className="muted">of {u.budget > 0 ? money(u.budget) : "no limit"}</span>
        {u.budget > 0 && pct >= 100 && <Badge kind="bad">used up: Auto uses local models</Badge>}
        {u.budget > 0 && pct >= 80 && pct < 100 && <Badge kind="warn">{Math.round(pct)}% used</Badge>}</div>
      {u.budget > 0 && <Progress value={Math.min(u.spent, u.budget)} total={u.budget} />}
      <p className="muted small">Warnings at 50% and 80%. When the budget is used up, Auto switches to models on this computer until next month; a chat set to a specific online model stops and says so. Set 0 for no limit. Costs are estimates from the tokens used and each model's price; your provider's bill is the final word.</p>
      {u.by_model?.length > 0 && (
        <table className="data-table">
          <thead><tr><th>Model</th><th>Calls</th><th>Tokens in / out</th><th>Cost</th></tr></thead>
          <tbody>{u.by_model.map((r: any) => (
            <tr key={r.model_id}><td>{r.name}{r.estimated ? <> <Badge title="No price known for this model: estimated with a cautious default. Enter its price below.">estimated</Badge></> : null}</td>
              <td>{r.calls}</td><td>{Number(r.input || 0).toLocaleString()} / {Number(r.output || 0).toLocaleString()}</td><td>{money(r.cost, 4)}</td></tr>
          ))}</tbody>
        </table>
      )}
      {u.by_purpose?.length > 1 && <div className="muted small mt">By use: {u.by_purpose.map((p: any) => `${p.purpose} ${money(p.cost, 4)}`).join(" · ")}</div>}
    </div>
  );
}

function Privacy({ data, reload }: any) {
  const { toast } = useApp();
  const s = data.settings;
  const set = async (values: any) => { try { await api.put("/online/settings", values); reload(); } catch (e: any) { toast(e.message, "error"); } };
  const opts = [
    { id: "ask", title: "Ask before sending my files (recommended)", text: "Online models may be used, but before one first sees your memory notes, attached files or what an agent read from your folders, KimiLM asks: allow once, always in that chat, or continue with a local model." },
    { id: "local_unless_on", title: "Local unless I turn online on", text: "Online models only run in chats where you switch them on (in the chat's model menu). Everything else stays on this computer." },
    { id: "auto", title: "Don't ask", text: "Online models see whatever the task needs. Use only if nothing private is on this computer's folders or in memory." },
  ];
  return (
    <div className="card">
      <div className="row between wrap"><h3><Shield size={16} /> Privacy</h3>
        <Toggle checked={s.cloud_enabled} onChange={(v: boolean) => set({ cloud_enabled: v })} label={s.cloud_enabled ? "Online models on" : "Online models off"} /></div>
      <div className="stack-xs mt">
        {opts.map((o) => (
          <label key={o.id} className="pick-row">
            <input type="radio" name="privacy" checked={s.cloud_privacy === o.id} onChange={() => set({ cloud_privacy: o.id })} />
            <div><div><b>{o.title}</b></div><div className="muted small">{o.text}</div></div>
          </label>
        ))}
      </div>
      <p className="muted small">Pipelines (like the Datavera publisher) and memory learning always run on a model on this computer when one is installed. Requests to OpenAI are sent with storage off.</p>
    </div>
  );
}

function AutoPreview({ data }: any) {
  const r = data.roster || {};
  const strategist = r.preview?.strategist;     // the router's own choice, so this always matches what Auto does
  const worker = r.preview?.worker;
  return (
    <div className="card">
      <h3>How Auto splits the work</h3>
      <div className="row gap-m wrap">
        <div><div className="muted small">Strategist — planning, strategy, hard reasoning, reviewing</div>
          <b>{strategist ? <>{strategist.kind === "cloud" ? <Cloud size={13} /> : <Cpu size={13} />} {strategist.name}</> : "—"}</b></div>
        <div><div className="muted small">Worker — routine and repetitive parts</div>
          <b>{worker ? <>{worker.kind === "cloud" ? <Cloud size={13} /> : <Cpu size={13} />} {worker.name}</> : "—"}</b></div>
      </div>
      {r.online_unavailable && <p className="muted small">Online models are not used right now: {r.online_unavailable}.</p>}
      <p className="muted small">Pick <b>Auto</b> as a chat's model to use this, or the <b>Lead and local workers</b> harness: the strategist plans and splits the task, local workers do the parts in parallel, and the strategist checks the results and writes the answer. Each reply shows which model answered and what it cost.</p>
    </div>
  );
}

function BrowseModels({ provider, onClose, onChanged }: any) {
  const { toast } = useApp();
  const [q, setQ] = useState("");
  const [rows, setRows] = useState<any[] | null>(null);
  const [total, setTotal] = useState(0);
  const [err, setErr] = useState("");
  const load = (query = q) => api.get(`/online/providers/${provider.id}/models?q=${enc(query)}`)
    .then((r) => { setRows(r.models); setTotal(r.total); setErr(""); }).catch((e) => { setErr(e.message); setRows([]); });
  useEffect(() => { load(""); }, [provider.id]);
  const add = async (m: any) => {
    try { await api.post("/online/models", { provider: provider.id, remote_id: m.remote_id }); toast(`${m.name} added`, "ok"); load(); onChanged(); }
    catch (e: any) { toast(e.message, "error"); }
  };
  const remove = async (m: any) => {
    try { await api.del(`/online/models/${enc(`cloud:${provider.id}:${m.remote_id}`)}`); load(); onChanged(); } catch (e: any) { toast(e.message, "error"); }
  };
  return (
    <Modal title={`${provider.name} — models`} onClose={onClose} wide>
      <div className="row gap-s">
        <input className="input" placeholder="Search by name" value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && load()} />
        <Button small icon={<Search size={13} />} onClick={() => load()}>Search</Button>
      </div>
      {err && <div className="error-line mt">{err}</div>}
      {rows === null ? <Spinner /> : (
        <div className="browse-list mt">
          {rows.length === 0 && !err && <div className="muted">No chat models match.</div>}
          {rows.map((m) => (
            <div key={m.remote_id} className="browse-row">
              <div>
                <b>{m.name}</b> {m.caps?.vision && <Eye size={13} className="accent" />} {m.strength >= 4.5 && <Badge kind="accent">strong</Badge>}
                <div className="muted small">{m.remote_id}{m.caps?.context ? ` · context ${Number(m.caps.context).toLocaleString()}` : ""} · {per1m(m.price)}</div>
              </div>
              {m.added ? <Button small kind="ghost" onClick={() => remove(m)}>Remove</Button> : <Button small icon={<Plus size={13} />} onClick={() => add(m)}>Add</Button>}
            </div>
          ))}
          {total > rows.length && <div className="muted small">Showing {rows.length} of {total}. Search to narrow.</div>}
        </div>
      )}
    </Modal>
  );
}

function ProviderCard({ p, reload }: any) {
  const { toast } = useApp();
  const [key, setKey] = useState("");
  const [base, setBase] = useState(p.base || "");
  const [busy, setBusy] = useState("");
  const [browse, setBrowse] = useState(false);
  useEffect(() => setBase(p.base || ""), [p.base]);
  const saveKey = async () => {
    setBusy("key");
    try {
      const r = await api.put(`/online/providers/${p.id}/key`, { key });
      setKey("");
      toast(`Key works: ${r.models} models available${r.added?.length ? `. Added ${r.added.join(" and ")}.` : ""}`, "ok");
    } catch (e: any) { toast(e.message, "error"); }
    setBusy(""); reload();
  };
  const check = async () => {
    setBusy("check");
    try { const r = await api.post(`/online/providers/${p.id}/test`); toast(`Working: ${r.models} models`, "ok"); } catch (e: any) { toast(e.message, "error"); }
    setBusy(""); reload();
  };
  const status = !p.enabled ? <Badge>off</Badge> : p.ready ? (p.error ? <Badge kind="bad">problem</Badge> : <Badge kind="ok">ready</Badge>) : <Badge>no key</Badge>;
  return (
    <div className="card">
      <div className="row between wrap">
        <div><h3>{p.name} {status} {p.models > 0 && <span className="muted small">· {p.models} model{p.models > 1 ? "s" : ""} added</span>}</h3>
          {p.error && <div className="error-line">{p.error}</div>}</div>
        <div className="row gap-s">
          {p.key_url && <Button small kind="ghost" icon={<ExternalLink size={13} />} onClick={() => open(p.key_url)}>Get a key</Button>}
          {(p.has_key || p.custom) && <Toggle checked={p.enabled} onChange={async (v: boolean) => { await api.put(`/online/providers/${p.id}`, { enabled: v }); reload(); }} />}
        </div>
      </div>
      <div className="row gap-s wrap mt">
        <input className="input" type="password" autoComplete="off" placeholder={p.has_key ? "Key saved — paste a new one to replace it" : p.custom ? "API key (optional)" : "Paste your API key"}
          value={key} onChange={(e) => setKey(e.target.value)} />
        <Button small kind="primary" icon={<KeyRound size={13} />} busy={busy === "key"} disabled={!key.trim()} onClick={saveKey}>Save and check</Button>
        {p.ready && <Button small icon={<RefreshCw size={13} />} busy={busy === "check"} onClick={check}>Check</Button>}
        {p.ready && <Button small icon={<Plus size={13} />} onClick={() => setBrowse(true)}>Choose models</Button>}
        {(p.has_key || p.custom) && <ConfirmButton small kind="ghost" icon={<Trash2 size={13} />} onConfirm={async () => { await api.del(`/online/providers/${p.id}`); reload(); }}>Remove</ConfirmButton>}
      </div>
      {(p.custom || p.base) && (
        <details className="mt" open={p.custom}><summary className="small muted">Address</summary>
          <div className="row gap-s mt"><input className="input" value={base} onChange={(e) => setBase(e.target.value)} />
            <Button small onClick={async () => { await api.put(`/online/providers/${p.id}`, { base }); reload(); toast("Saved", "ok"); }}>Save</Button></div>
        </details>
      )}
      <p className="muted small">The key is kept in the Windows Credential Manager and only sent to {p.custom ? "this address" : p.name}. Usage is billed by the provider to your account.</p>
      {browse && <BrowseModels provider={p} onClose={() => setBrowse(false)} onChanged={reload} />}
    </div>
  );
}

function ModelRow({ m, reload, isDefault }: any) {
  const { toast } = useApp();
  const s = m.settings || {};
  const [pin, setPin] = useState(m.price?.in ?? "");
  const [pout, setPout] = useState(m.price?.out ?? "");
  const set = async (values: any) => { try { await api.put(`/online/models/${enc(m.id)}`, values); reload(); } catch (e: any) { toast(e.message, "error"); } };
  return (
    <div className="card model-card">
      <div className="row between wrap">
        <div><h3><Cloud size={15} /> {m.name} {isDefault && <Badge kind="accent"><Star size={11} /> default</Badge>} {m.caps?.vision && <Badge kind="ok"><Eye size={11} /> sees images</Badge>}
          {!m.ready && <Badge kind="warn">provider not ready</Badge>}</h3>
          <div className="muted small">{m.provider_name} · {m.remote_id}{m.caps?.context ? ` · context ${Number(m.caps.context).toLocaleString()}` : ""} · {per1m(m.price)}{m.price?.source ? ` (${m.price.source})` : ""}</div></div>
        <Toggle checked={!!m.enabled} onChange={(v: boolean) => set({ enabled: v })} label={m.enabled ? "On" : "Off"} />
      </div>
      <div className="row gap-m wrap mt">
        <Field label="Role for Auto" help={ROLE_HELP}>
          <div className="seg">{["strategist", "worker", "both", "off"].map((r) => (
            <button key={r} className={`seg-btn${m.role === r ? " on" : ""}`} onClick={() => set({ role: r })}>{ROLE_LABEL[r]}</button>))}</div>
        </Field>
        <Field label="Reasoning effort" help="Higher = deeper thinking, slower, more tokens. Default lets KimiLM choose (high when thinking is on).">
          <select className="input narrow" value={s.effort || ""} onChange={(e) => set({ effort: e.target.value || null })}>
            <option value="">Default</option>{["low", "medium", "high"].map((x) => <option key={x} value={x}>{x}</option>)}
          </select>
        </Field>
        <Field label="Price per 1M tokens (USD)" help="In and out. Used for the budget; filled in automatically when known.">
          <div className="row gap-s"><input className="input narrow" type="number" min={0} step={0.01} placeholder="in" value={pin} onChange={(e) => setPin(e.target.value)} />
            <input className="input narrow" type="number" min={0} step={0.01} placeholder="out" value={pout} onChange={(e) => setPout(e.target.value)} />
            <Button small onClick={() => set({ price_in: pin === "" ? null : Number(pin), price_out: pout === "" ? null : Number(pout) })}>Save</Button></div>
        </Field>
      </div>
      <div className="row gap-s mt">
        {!isDefault && <Button small icon={<Star size={13} />} onClick={async () => { await api.put("/settings", { default_model: m.id }); reload(); }}>Make default</Button>}
        <ConfirmButton small icon={<Trash2 size={13} />} onConfirm={async () => { await api.del(`/online/models/${enc(m.id)}`); reload(); }}>Remove</ConfirmButton>
      </div>
    </div>
  );
}

function CustomEndpoint({ reload }: any) {
  const { toast } = useApp();
  const [f, setF] = useState({ name: "", base: "", key: "" });
  return (
    <div className="card">
      <h3>Add another endpoint</h3>
      <p className="muted small">Any service with an OpenAI-compatible API: Together, Fireworks, Azure-hosted models, or a server on your network (LM Studio, Ollama, vLLM — e.g. http://localhost:1234/v1).</p>
      <div className="row gap-s wrap">
        <input className="input" placeholder="Name" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
        <input className="input" placeholder="Address, e.g. https://api.together.xyz/v1" value={f.base} onChange={(e) => setF({ ...f, base: e.target.value })} />
        <input className="input" type="password" placeholder="API key (if needed)" value={f.key} onChange={(e) => setF({ ...f, key: e.target.value })} />
        <Button small kind="primary" icon={<Plus size={13} />} disabled={!f.base} onClick={async () => {
          try { await api.post("/online/custom", f); setF({ name: "", base: "", key: "" }); reload(); toast("Added. Use Check, then Choose models.", "ok"); } catch (e: any) { toast(e.message, "error"); } }}>Add</Button>
      </div>
    </div>
  );
}

export default function Online() {
  const { boot, refreshBoot } = useApp() as any;
  const [data, setData] = useState<any>(null);
  const load = () => api.get("/online").then(setData);
  useEffect(() => { load(); }, []);
  useEvent((e) => { if (["models_changed", "settings_changed", "budget"].includes(e.kind)) load(); });
  const reload = () => { load(); refreshBoot?.(); };
  const providers = useMemo(() => (data?.providers || []).slice().sort((a: any, b: any) => Number(b.ready) - Number(a.ready)), [data]);
  if (!data) return <Spinner />;
  const defaultModel = boot?.default_model;
  return (
    <div className="stack">
      <div className="card">
        <h3>Online models next to the ones on this computer</h3>
        <p className="muted small">Use frontier models (GPT, Claude, Gemini, Kimi, DeepSeek, Grok…) for strategy and hard reasoning, and your local models for routine and repetitive work. Online models need an API key from each provider (a ChatGPT or Claude subscription can't be used by other apps) and are billed per use by the provider.</p>
        <Field label="Default model for new chats">
          <select className="input" value={defaultModel || ""} onChange={async (e) => { await api.put("/settings", { default_model: e.target.value }); reload(); }}>
            <option value="auto">Auto — KimiLM picks per request</option>
            <optgroup label="On this computer">{(boot?.models || []).map((m: any) => <option key={m.id} value={m.id}>{m.name}</option>)}</optgroup>
            <optgroup label="Online">{(data.models || []).filter((m: any) => m.enabled).map((m: any) => <option key={m.id} value={m.id}>{m.name} ({m.provider_name})</option>)}</optgroup>
          </select>
        </Field>
      </div>
      <div className="grid-2">
        <Budget data={data} reload={reload} />
        <div className="stack"><AutoPreview data={data} /><Privacy data={data} reload={reload} /></div>
      </div>
      {data.models.length > 0 && <h3 className="mt">Online models in use</h3>}
      {data.models.map((m: any) => <ModelRow key={m.id} m={m} reload={reload} isDefault={m.id === defaultModel} />)}
      <h3 className="mt">Providers</h3>
      {providers.map((p: any) => <ProviderCard key={p.id} p={p} reload={reload} />)}
      <CustomEndpoint reload={reload} />
    </div>
  );
}
