import React, { useEffect, useState } from "react";
import { CalendarPlus, CheckCircle2, Download, Package, Play, Plug, Plus, Trash2, Upload, XCircle } from "lucide-react";
import { api, desktop, enc, TOKEN } from "../api";
import { go, useApp, useEvent } from "../store";
import { Badge, Button, ConfirmButton, Field, FolderInput, Modal, PageHeader, Spinner, Tabs, Toggle } from "../components/ui";

function SettingField({ field, value, options, onChange }: any) {
  if (field.type === "secret") {
    return (
      <Field label={field.label} help={field.help}>
        {value === true && <div className="row gap-s"><Badge kind="ok">Saved securely</Badge><span className="muted small">Paste a new one below to replace it.</span></div>}
        <input className="input" type="password" autoComplete="off" placeholder={value === true ? "••••••••••••" : "Paste here"} onChange={(e) => onChange(e.target.value)} />
      </Field>
    );
  }
  if (field.type === "boolean") return <Field help={field.help}><Toggle checked={value ?? field.default} onChange={onChange} label={field.label} /></Field>;
  if (field.type === "textarea") return <Field label={field.label} help={field.help}><textarea className="input" rows={3} value={value || ""} onChange={(e) => onChange(e.target.value)} /></Field>;
  if (field.type === "folder") return <Field label={field.label} help={field.help}><FolderInput value={value} onChange={onChange} /></Field>;
  if (field.type === "select") {
    const opts = options?.length ? options : field.options || [];
    return (
      <Field label={field.label} help={field.help}>
        <select className="input" value={value || ""} onChange={(e) => onChange(e.target.value)} disabled={!opts.length}>
          {!opts.length && <option value="">(press Check and connect first)</option>}
          {opts.map((o: string) => <option key={o} value={o}>{o}</option>)}
        </select>
      </Field>
    );
  }
  return <Field label={field.label} help={field.help}><input className="input" value={value || ""} onChange={(e) => onChange(e.target.value)} /></Field>;
}

function PluginCard({ p, onChange }: any) {
  const { toast, refreshBoot } = useApp();
  const [open, setOpen] = useState(false);
  const [values, setValues] = useState<any>({});
  const [busy, setBusy] = useState("");
  const [setup, setSetup] = useState<any>(p.setup_state?.message ? p.setup_state : null);
  const save = async () => {
    setBusy("save");
    try {
      await api.put(`/plugins/${p.id}/settings`, values);
      setValues({});
      if (p.setup) {
        setBusy("setup");
        const r = await api.post(`/plugins/${p.id}/setup`);
        setSetup(r);
        toast(r.ok ? "Connected" : r.message, r.ok ? "ok" : "error");
      } else toast("Saved", "ok");
      onChange();
    } catch (e: any) { toast(e.message, "error"); }
    setBusy("");
  };
  const runAction = async (a: any) => {
    try { await api.post(`/plugins/${p.id}/actions/${a.id}`); toast(`${a.label} started — see Activity`, "info"); } catch (e: any) { toast(e.message, "error"); }
  };
  const useTemplate = async (t: any) => {
    try {
      await api.post("/schedules", { name: t.name, when: t.when, prompt: t.prompt, harness: t.harness, connectors: t.connectors || [],
        only_on_ac: t.only_on_ac, max_runs_per_day: t.max_runs_per_day, auto_approve: t.auto_approve, enabled: false });
      toast("Scheduled task added (switched off). Review and switch it on in Schedules.", "ok");
      go("schedules");
    } catch (e: any) { toast(e.message, "error"); }
  };
  if (p.error) return <div className="card"><b>{p.name}</b> <Badge kind="bad">broken</Badge><div className="error-line">{p.error}</div></div>;
  const options = p.setup_state?.options || setup?.options || {};
  return (
    <div className="card plugin-card">
      <div className="row between">
        <div className="row gap-s"><Package size={18} className="accent" /><div><h3>{p.name} <span className="muted small">v{p.version}</span></h3>
          <div className="muted small">{p.source === "builtin" ? "Built in" : "Installed by you"}{p.author ? ` · ${p.author}` : ""}</div></div></div>
        <div className="row gap-s">
          {p.enabled && (p.configured ? <Badge kind="ok">Ready</Badge> : <Badge kind="warn">Needs setup</Badge>)}
          <Toggle checked={p.enabled} onChange={async (v: boolean) => { await api.put(`/plugins/${p.id}/enabled`, { enabled: v }); onChange(); refreshBoot(); }} />
        </div>
      </div>
      <p>{p.description}</p>
      <div className="row gap-s wrap small">
        {p.connectors?.length > 0 && <Badge>{p.connectors.length} connector{p.connectors.length > 1 ? "s" : ""}</Badge>}
        {p.skills?.length > 0 && <Badge>{p.skills.length} skills</Badge>}
        {p.harness_list?.length > 0 && <Badge>{p.harness_list.length} harness{p.harness_list.length > 1 ? "es" : ""}</Badge>}
        <button className="link" onClick={() => setOpen(!open)}>{open ? "Hide details" : "Settings & details"}</button>
      </div>
      {open && p.enabled && (
        <div className="plugin-body">
          {p.settings?.length > 0 && (
            <div className="stack-s">
              {p.settings.map((f: any) => (
                <SettingField key={f.key} field={f} options={options[f.key]} value={f.key in values ? values[f.key] : p.values[f.key]}
                  onChange={(v: any) => setValues({ ...values, [f.key]: v })} />
              ))}
              <div className="row gap-s">
                <Button kind="primary" busy={!!busy} onClick={save}>{p.setup ? "Save, check and connect" : "Save"}</Button>
                {busy === "setup" && <span className="muted small">Checking with the service… the first time it also builds your catalogue (about a minute).</span>}
              </div>
              {setup?.message && <div className={setup.ok ? "ok-line" : "error-line"}>{setup.ok ? <CheckCircle2 size={14} /> : <XCircle size={14} />} {setup.message}</div>}
            </div>
          )}
          {p.actions?.length > 0 && (
            <div className="mt"><div className="small muted">Actions</div>
              <div className="row gap-s wrap">{p.actions.map((a: any) => <Button key={a.id} small icon={<Play size={13} />} title={a.help} onClick={() => runAction(a)}>{a.label}</Button>)}</div></div>
          )}
          {p.harness_list?.length > 0 && (
            <div className="mt"><div className="small muted">Harnesses</div>
              {p.harness_list.map((h: any) => <div key={h.id} className="list-row" onClick={() => go("harnesses", h.id)}><b>{h.name}</b><span className="muted small ellipsis">{h.description}</span></div>)}</div>
          )}
          {p.skills?.length > 0 && (
            <div className="mt"><div className="small muted">Skills</div>
              {p.skills.map((s: any) => <div key={s.id} className="list-row" onClick={() => go("skills", s.id)}><b>{s.name}</b><span className="muted small ellipsis">{s.description}</span></div>)}</div>
          )}
          {p.schedule_templates?.length > 0 && (
            <div className="mt"><div className="small muted">Ready-made scheduled tasks</div>
              {p.schedule_templates.map((t: any) => (
                <div key={t.name} className="list-row"><span><b>{t.name}</b> <span className="muted small">{t.when}</span></span>
                  <Button small icon={<CalendarPlus size={13} />} onClick={() => useTemplate(t)}>Add</Button></div>
              ))}</div>
          )}
          <div className="row gap-s mt">
            <Button small icon={<Download size={13} />} onClick={() => window.open(`/api/plugins/${p.id}/export?token=${enc(TOKEN)}`)}>Export as zip</Button>
            {p.source === "user" && <ConfirmButton small icon={<Trash2 size={13} />} onConfirm={async () => { await api.del(`/plugins/${p.id}`); onChange(); }}>Remove</ConfirmButton>}
          </div>
        </div>
      )}
    </div>
  );
}

function CreatePlugin({ onClose, onDone }: any) {
  const { toast } = useApp();
  const [form, setForm] = useState<any>({ name: "", description: "", skills: [], harnesses: [], connectors: [] });
  const [items, setItems] = useState<any>(null);
  useEffect(() => {
    Promise.all([api.get("/skills"), api.get("/harnesses"), api.get("/connectors")]).then(([s, h, c]) =>
      setItems({ skills: s.skills.filter((x: any) => x.source === "user"), harnesses: h.harnesses.filter((x: any) => x.source === "user"), connectors: c.connectors.filter((x: any) => x.source === "user") }));
  }, []);
  const toggle = (key: string, id: string) => setForm({ ...form, [key]: form[key].includes(id) ? form[key].filter((x: string) => x !== id) : [...form[key], id] });
  return (
    <Modal title="Create a plugin" onClose={onClose} footer={<Button kind="primary" disabled={!form.name} onClick={async () => {
      try { await api.post("/plugins-create", form); toast("Plugin created", "ok"); onDone(); onClose(); } catch (e: any) { toast(e.message, "error"); }
    }}>Create</Button>}>
      <p className="muted small">Bundle your own skills, harnesses and connectors into one plugin you can export and share. Keys are not included; whoever installs it enters their own.</p>
      <Field label="Name"><input className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field>
      <Field label="Description"><input className="input" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></Field>
      {!items ? <Spinner /> : ["skills", "harnesses", "connectors"].map((k) => (
        <Field key={k} label={k[0].toUpperCase() + k.slice(1)}>
          {items[k].length === 0 && <div className="muted small">None of your own yet.</div>}
          {items[k].map((x: any) => <label key={x.id} className="check"><input type="checkbox" checked={form[k].includes(x.id)} onChange={() => toggle(k, x.id)} /> {x.name}</label>)}
        </Field>
      ))}
    </Modal>
  );
}

function Plugins_({ }: any) {
  const { toast } = useApp();
  const [data, setData] = useState<any>(null);
  const [creating, setCreating] = useState(false);
  const load = () => api.get("/plugins").then(setData);
  useEffect(() => { load(); }, []);
  useEvent((e) => { if (e.kind === "plugins_changed") load(); });
  return (
    <div className="stack">
      <div className="row gap-s">
        <label className="btn btn-default"><Upload size={14} /><span>Install a plugin or MCP bundle (.zip, .mcpb)</span>
          <input type="file" accept=".zip,.mcpb,.dxt" hidden onChange={async (e) => {
            const f = e.target.files?.[0]; e.target.value = "";
            if (f) try { await api.upload("/plugins-upload", f); toast("Plugin installed", "ok"); load(); } catch (err: any) { toast(err.message, "error"); }
          }} /></label>
        <Button icon={<Plus size={14} />} onClick={() => setCreating(true)}>Create a plugin</Button>
      </div>
      {!data ? <Spinner /> : data.plugins.map((p: any) => <PluginCard key={p.id} p={p} onChange={load} />)}
      {creating && <CreatePlugin onClose={() => setCreating(false)} onDone={load} />}
    </div>
  );
}

// "NAME=value" lines ⇄ an object. Values are never sent back by the engine, so an existing name shows as
// "NAME=" and an empty value means "keep what is saved".
const toLines = (o: any, sep: string) => Object.entries(o || {}).map(([k, v]) => `${k}${sep}${v || ""}`).join("\n");
const fromLines = (text: string, sep: string) => Object.fromEntries(text.split("\n").map((l) => l.trim()).filter(Boolean)
  .map((l) => { const i = l.indexOf(sep.trim()); return i < 0 ? [l, ""] : [l.slice(0, i).trim(), l.slice(i + sep.trim().length).trim()]; }).filter(([k]) => k));
const splitArgs = (text: string) => (text ? (text.match(/(?:[^\s"]+|"[^"]*")+/g) || []).map((a: string) => a.replace(/^"|"$/g, "")) : []);

function ConnectorForm({ preset, edit, onClose, onDone }: any) {
  const { toast } = useApp();
  const src = edit || preset;
  const [f, setF] = useState<any>({ name: edit ? edit.name : preset.name.replace(/ \(.*\)/, ""), transport: src.transport, type: src.type || "http", url: src.url || "",
    command: src.command || "", args: (src.args || []).map((a: string) => (/\s/.test(a) ? `"${a}"` : a)).join(" "), auth: src.auth || "none", token: "",
    env: toLines(src.env, "="), headers: toLines(src.headers, ": "), cwd: src.cwd || "", description: src.description || "" });
  const [busy, setBusy] = useState(false);
  const save = async () => {
    setBusy(true);
    try {
      const body: any = { ...f, args: splitArgs(f.args), env: fromLines(f.env, "="), headers: fromLines(f.headers, ":") };
      if (!body.token) delete body.token;
      const c = edit ? await api.put(`/connectors/${enc(edit.id)}/config`, body) : await api.post("/connectors", body);
      const t = await api.post(`/connectors/${enc(c.id)}/test`);
      toast(t.ok ? `Connected: ${t.tools.length} tools` : `Saved, but the test failed: ${t.error}`, t.ok ? "ok" : "error");
      onDone(); onClose();
    } catch (e: any) { toast(e.message, "error"); }
    setBusy(false);
  };
  return (
    <Modal wide title={edit ? `Edit connector: ${edit.name}` : `Add connector: ${preset.name}`} onClose={onClose}
      footer={<Button kind="primary" busy={busy} onClick={save}>{f.transport === "http" && f.auth === "oauth" ? "Save and sign in" : "Save and test"}</Button>}>
      {!edit && <p className="muted small">{preset.description}</p>}
      <Field label="Name"><input className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
      {f.transport === "http" ? <>
        <Field label="Server URL" help="Must start with https://"><input className="input" value={f.url} onChange={(e) => setF({ ...f, url: e.target.value })} placeholder="https://example.com/mcp" /></Field>
        <div className="row gap-m wrap">
          <Field label="How it is reached" help="Streamable HTTP is the current way; choose SSE only for an older server whose address ends in /sse.">
            <select className="input" value={f.type} onChange={(e) => setF({ ...f, type: e.target.value })}><option value="http">Streamable HTTP</option><option value="sse">SSE (older servers)</option></select>
          </Field>
          <Field label="Signing in" help={f.auth === "oauth" ? "Your browser opens on the service's own sign-in page; KimiLM keeps the sign-in on this computer and renews it." : undefined}>
            <select className="input" value={f.auth} onChange={(e) => setF({ ...f, auth: e.target.value })}>
              <option value="none">None (or only the headers below)</option><option value="bearer">API key / bearer token</option><option value="oauth">Sign in with the browser (OAuth)</option></select>
          </Field>
        </div>
        {f.auth === "bearer" && <Field label="Key or token" help={edit?.has_token ? "A key is saved. Paste a new one to replace it." : "Stored in Windows Credential Manager."}><input className="input" type="password" value={f.token} onChange={(e) => setF({ ...f, token: e.target.value })} /></Field>}
        <Field label="Extra headers" help="One per line as Name: value. Values are stored in Windows Credential Manager; ${NAME} takes the value of an environment variable. An empty value keeps what is saved.">
          <textarea className="input mono" rows={2} value={f.headers} onChange={(e) => setF({ ...f, headers: e.target.value })} placeholder="X-Api-Key: …" /></Field>
      </> : <>
        <Field label="Command" help="As in other apps' configurations: npx, uvx, node, python, docker or a full path. If Node.js is not installed, “node” runs on the app's own Node and “python” on the app's Python; npx and uvx need Node.js / uv."><input className="input mono" value={f.command} onChange={(e) => setF({ ...f, command: e.target.value })} placeholder="npx" /></Field>
        <Field label="Arguments"><input className="input mono" value={f.args} onChange={(e) => setF({ ...f, args: e.target.value })} placeholder="-y @some/mcp-server" /></Field>
        <Field label="Environment" help="One per line as NAME=value (keys, tokens, settings the server reads). Values are stored in Windows Credential Manager; an empty value keeps what is saved.">
          <textarea className="input mono" rows={2} value={f.env} onChange={(e) => setF({ ...f, env: e.target.value })} placeholder="API_KEY=…" /></Field>
        <Field label="Working folder (optional)"><input className="input mono" value={f.cwd} onChange={(e) => setF({ ...f, cwd: e.target.value })} /></Field>
      </>}
    </Modal>
  );
}

const serverLine = (s: any) => s.transport === "http" ? `${s.type === "sse" ? "SSE" : "HTTP"} · ${s.url}${s.auth === "oauth" ? " · signs in with the browser" : s.auth === "bearer" ? " · key" : ""}` : `${s.command} ${(s.args || []).join(" ")}`;

// Take servers over: from a pasted configuration (the "mcpServers" JSON other apps use), or from the apps on
// this computer that already have some. Secrets are read by the engine from the file itself, never shown here.
function ImportServers({ mode, onClose, onDone }: any) {
  const { toast } = useApp();
  const [text, setText] = useState("");
  const [groups, setGroups] = useState<any[] | null>(mode === "found" ? null : []);
  const [error, setError] = useState("");
  const [picked, setPicked] = useState<Record<string, boolean>>({});
  useEffect(() => { if (mode === "found") api.get("/connectors-found").then((r) => { setGroups(r.found); setPicked(Object.fromEntries(r.found.flatMap((g: any, i: number) => g.servers.map((s: any) => [`${i}:${s.name}`, true])))); }).catch((e) => setError(e.message)); }, []);
  useEffect(() => {
    if (mode !== "paste") return;
    if (!text.trim()) { setGroups([]); setError(""); return; }
    const t = setTimeout(() => api.post("/connectors-preview", { text }).then((r) => { setGroups([{ app: "Pasted configuration", servers: r.servers }]); setPicked(Object.fromEntries(r.servers.map((s: any) => [`0:${s.name}`, true]))); setError(""); })
      .catch((e) => { setGroups([]); setError(e.message); }), 350);
    return () => clearTimeout(t);
  }, [text]);
  const count = Object.values(picked).filter(Boolean).length;
  const add = async () => {
    try {
      let added: any[] = [], skipped: any[] = [];
      for (const [i, g] of (groups || []).entries()) {
        const names = g.servers.filter((s: any) => picked[`${i}:${s.name}`]).map((s: any) => s.name);
        if (!names.length) continue;
        const r = await api.post("/connectors-import", mode === "paste" ? { text, names } : { path: g.path, app: g.app, names });
        added = added.concat(r.added); skipped = skipped.concat(r.skipped);
      }
      const signIn = added.filter((a) => a.auth === "oauth").length;
      toast(`Added ${added.length} connector${added.length === 1 ? "" : "s"}${skipped.length ? `, ${skipped.length} already here` : ""}${signIn ? `. ${signIn} need${signIn === 1 ? "s" : ""} you to sign in: press Sign in on ${signIn === 1 ? "it" : "them"}.` : ". Press Test on each to connect."}`, added.length ? "ok" : "info");
      onDone(); onClose();
    } catch (e: any) { toast(e.message, "error"); }
  };
  return (
    <Modal wide title={mode === "paste" ? "Add connectors from a configuration" : "MCP servers found on this computer"} onClose={onClose}
      footer={<Button kind="primary" disabled={!count} onClick={add}>Add {count || ""} connector{count === 1 ? "" : "s"}</Button>}>
      {mode === "paste" && <Field label="Paste the configuration" help='The JSON that Claude Desktop, Claude Code, Cursor, VS Code, Windsurf and most server instructions use: an "mcpServers" (or "servers") section, or one server with "command" or "url".'>
        <textarea className="input mono" rows={8} autoFocus value={text} onChange={(e) => setText(e.target.value)}
          placeholder={'{\n  "mcpServers": {\n    "example": { "command": "npx", "args": ["-y", "@scope/server"], "env": { "API_KEY": "…" } }\n  }\n}'} /></Field>}
      {error && <div className="error-line">{error}</div>}
      {groups === null && <Spinner />}
      {groups !== null && mode === "found" && groups.length === 0 && !error && <p className="muted">No other app on this computer has MCP servers set up (looked for Claude Desktop, Claude Code, Cursor, VS Code, Windsurf, LM Studio, Gemini CLI and Zed).</p>}
      {(groups || []).map((g: any, i: number) => (
        <div key={i} className="found-group">
          <div className="small"><b>{g.app}</b>{g.path ? <span className="muted"> · {g.path}</span> : null}</div>
          {g.servers.map((s: any) => (
            <label key={s.name} className="found-server">
              <input type="checkbox" checked={!!picked[`${i}:${s.name}`]} onChange={(e) => setPicked({ ...picked, [`${i}:${s.name}`]: e.target.checked })} />
              <div className="grow"><div><b>{s.name}</b>{!s.enabled && <Badge>switched off there</Badge>}</div>
                <div className="small muted mono ellipsis">{serverLine(s)}</div>
                {(Object.keys(s.env || {}).length > 0 || Object.keys(s.headers || {}).length > 0) && <div className="small muted">comes with values for {[...Object.keys(s.env || {}), ...Object.keys(s.headers || {})].join(", ")} (kept in Windows Credential Manager)</div>}
                {s.note && <div className="small muted">{s.note}</div>}</div>
            </label>
          ))}
        </div>
      ))}
    </Modal>
  );
}

function ExportServers({ onClose }: any) {
  const { toast } = useApp();
  const [text, setText] = useState("");
  useEffect(() => { api.get("/connectors-export").then((r) => setText(JSON.stringify(r, null, 2))).catch((e) => setText(e.message)); }, []);
  return (
    <Modal wide title="Your connectors as a configuration" onClose={onClose}
      footer={<Button kind="primary" onClick={() => { navigator.clipboard.writeText(text); toast("Copied", "ok"); }}>Copy</Button>}>
      <p className="muted small">The same JSON other apps read (Claude Desktop, Cursor, VS Code with “servers” instead of “mcpServers”). Keys and tokens are not included: they appear as {"${NAME}"} references to fill in where you use it.</p>
      <pre className="log">{text}</pre>
    </Modal>
  );
}

function PromptUse({ c, p, onClose }: any) {
  const { toast } = useApp();
  const [args, setArgs] = useState<Record<string, string>>({});
  const [text, setText] = useState("");
  const get = async () => { try { setText((await api.post(`/connectors/${enc(c.id)}/prompt`, { name: p.name, arguments: args })).text); } catch (e: any) { toast(e.message, "error"); } };
  return (
    <Modal title={`${c.name}: ${p.title || p.name}`} onClose={onClose}
      footer={<>{text && <Button onClick={() => { navigator.clipboard.writeText(text); toast("Copied: paste it into a chat", "ok"); }}>Copy</Button>}<Button kind="primary" onClick={get}>Fill it in</Button></>}>
      {p.description && <p className="muted small">{p.description}</p>}
      {p.arguments.map((a: any) => <Field key={a.name} label={`${a.name}${a.required ? "" : " (optional)"}`} help={a.description}><input className="input" value={args[a.name] || ""} onChange={(e) => setArgs({ ...args, [a.name]: e.target.value })} /></Field>)}
      {text && <pre className="log">{text}</pre>}
    </Modal>
  );
}

function Connectors() {
  const { toast } = useApp();
  const [data, setData] = useState<any>(null);
  const [preset, setPreset] = useState<any>(null);
  const [edit, setEdit] = useState<any>(null);
  const [dialog, setDialog] = useState("");
  const [usePrompt, setUsePrompt] = useState<any>(null);
  const [tools, setTools] = useState<Record<string, any>>({});
  const load = () => api.get("/connectors").then(setData);
  useEffect(() => { load(); }, []);
  useEvent((e) => { if (["connectors_changed", "plugins_changed"].includes(e.kind)) load(); });
  const test = async (c: any) => {
    setTools({ ...tools, [c.id]: "loading" });
    const r = await api.post(`/connectors/${enc(c.id)}/test`);
    setTools((t) => ({ ...t, [c.id]: r }));
  };
  const update = async (c: any, values: any) => { try { await api.put(`/connectors/${enc(c.id)}/config`, values); load(); } catch (e: any) { toast(e.message, "error"); } };
  if (!data) return <Spinner />;
  return (
    <div className="stack">
      <div className="card">
        <h3>Add a connector</h3>
        <p className="muted small">Connectors are MCP servers: they give the agent tools, documents and prompts from other apps and services. They are set up the same way as in Claude Desktop, Cursor or VS Code, so a server's own instructions work here: paste its configuration, or take over the servers those apps already have. Choose connectors per chat (the plug menu) or per project.</p>
        <div className="row gap-s wrap">
          <Button kind="primary" icon={<Plus size={13} />} onClick={() => setDialog("paste")}>Paste a configuration</Button>
          <Button icon={<Download size={13} />} onClick={() => setDialog("found")}>Found on this computer</Button>
          {data.presets.map((p: any) => <Button key={p.key} icon={<Plug size={13} />} onClick={() => setPreset(p)}>{p.name}</Button>)}
          {data.connectors.some((c: any) => c.source === "user") && <Button kind="ghost" icon={<Upload size={13} />} onClick={() => setDialog("export")}>Export</Button>}
        </div>
      </div>
      {data.connectors.map((c: any) => {
        const t = tools[c.id];
        const info = (t && t !== "loading" && t.ok ? t.info : c.info) || {};
        const oauth = c.auth === "oauth";
        return (
          <div key={c.id} className="card">
            <div className="row between">
              <div className="grow" style={{ minWidth: 0 }}><h3>{c.name}</h3><div className="muted small ellipsis">{serverLine(c)} · {c.source === "user" ? "yours" : `from plugin ${c.plugin}`}</div>
                {info.name && <div className="muted small">{info.title || info.name}{info.version ? ` ${info.version}` : ""}{info.protocol ? ` · MCP ${info.protocol}` : ""}{c.tool_count ? ` · ${c.tool_count} tool${c.tool_count === 1 ? "" : "s"}` : ""}{info.resources_count ? ` · ${info.resources_count} resource${info.resources_count === 1 ? "" : "s"}` : ""}{info.prompts_count ? ` · ${info.prompts_count} prompt${info.prompts_count === 1 ? "" : "s"}` : ""}</div>}</div>
              <div className="row gap-s">{!c.ready && <Badge kind="warn">{oauth ? "sign in needed" : "needs key"}</Badge>}{oauth && c.signed_in && <Badge kind="ok">signed in</Badge>}<Toggle checked={c.enabled} onChange={(v: boolean) => update(c, { enabled: v })} /></div>
            </div>
            <div className="row gap-m wrap mt">
              <Field label="Ask before">
                <select className="input" value={c.approval} onChange={(e) => update(c, { approval: e.target.value })}>
                  <option value="changes">Actions that change things</option><option value="always">Every action</option><option value="never">Never ask</option>
                </select>
              </Field>
            </div>
            <div className="row gap-s wrap">
              <Button small busy={t === "loading"} onClick={() => test(c)}>{oauth && !c.signed_in ? "Sign in" : "Test & list tools"}</Button>
              {oauth && c.signed_in && <Button small kind="ghost" onClick={async () => { await api.post(`/connectors/${enc(c.id)}/signout`); setTools((x) => ({ ...x, [c.id]: undefined })); load(); }}>Sign out</Button>}
              {c.source === "user" && <Button small onClick={() => setEdit(c)}>Edit</Button>}
              {c.source === "user" && <ConfirmButton small icon={<Trash2 size={13} />} onConfirm={async () => { await api.del(`/connectors/${enc(c.id)}/delete`); load(); }}>Remove</ConfirmButton>}
              {c.source !== "user" && <button className="link" onClick={() => go("plugins")}>Settings are in its plugin</button>}
            </div>
            {t === "loading" && oauth && !c.signed_in && <div className="note">Your browser is opening the service's sign-in page. Come back here when you have signed in.</div>}
            {t && t !== "loading" && (t.ok ? (<>
              <div className="tool-list">{t.tools.map((x: any) => <div key={x.name} className="small"><b>{x.title || x.name}</b> {x.readonly ? <Badge kind="ok">read-only</Badge> : x.destructive ? <Badge kind="bad">deletes or overwrites</Badge> : <Badge kind="warn">changes</Badge>} <span className="muted">{x.description.slice(0, 140)}</span></div>)}</div>
              {t.resources?.length > 0 && <div className="tool-list"><div className="small"><b>Resources</b> <span className="muted">what the agent can read from it</span></div>
                {t.resources.slice(0, 30).map((r: any) => <div key={r.uri} className="small"><span className="mono">{r.uri}</span> <span className="muted">{r.name}{r.description ? ` — ${r.description.slice(0, 100)}` : ""}</span></div>)}</div>}
              {t.prompts?.length > 0 && <div className="tool-list"><div className="small"><b>Prompts</b> <span className="muted">ready-made requests the server offers</span></div>
                {t.prompts.map((p: any) => <div key={p.name} className="small"><button className="link" onClick={() => setUsePrompt({ c, p })}>{p.title || p.name}</button> <span className="muted">{p.description.slice(0, 120)}</span></div>)}</div>}
            </>) : <div className="error-line">{t.error}</div>)}
          </div>
        );
      })}
      {preset && <ConnectorForm preset={preset} onClose={() => setPreset(null)} onDone={load} />}
      {edit && <ConnectorForm edit={edit} onClose={() => setEdit(null)} onDone={load} />}
      {(dialog === "paste" || dialog === "found") && <ImportServers mode={dialog} onClose={() => setDialog("")} onDone={load} />}
      {dialog === "export" && <ExportServers onClose={() => setDialog("")} />}
      {usePrompt && <PromptUse c={usePrompt.c} p={usePrompt.p} onClose={() => setUsePrompt(null)} />}
    </div>
  );
}

export default function Plugins({ tab }: { tab?: string }) {
  const t = tab || "plugins";
  return (
    <div className="page">
      <PageHeader title="Plugins & connectors" subtitle="Plugins bundle connectors, skills, harnesses and ready-made tasks. Turn on the ones you need." />
      <Tabs value={t} onChange={(v: string) => go("plugins", v)} tabs={[{ id: "plugins", label: "Plugins" }, { id: "connectors", label: "Connectors (MCP)" }]} />
      {t === "plugins" ? <Plugins_ /> : <Connectors />}
    </div>
  );
}
