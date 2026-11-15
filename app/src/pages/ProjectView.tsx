import React, { useEffect, useRef, useState } from "react";
import { AlertTriangle, BookOpen, Brain, FileText, FolderOpen, MessageSquarePlus, NotebookPen, RotateCcw, Trash2, Upload, X } from "lucide-react";
import { api, desktop, fmtBytes, fmtTime } from "../api";
import { go, useApp, useEvent } from "../store";
import { Badge, Button, ConfirmButton, Field, FolderInput, Markdown, PageHeader, Spinner, Toggle } from "../components/ui";

const KNOWLEDGE_TYPES = ".pdf,.docx,.pptx,.xlsx,.xlsm,.xls,.ods,.odt,.rtf,.txt,.md,.markdown,.csv,.tsv,.json,.yaml,.yml,.xml,.html,.htm,.py,.r,.js,.ts,.tsx,.jsx,.sql,.java,.c,.cpp,.h,.cs,.go,.rs,.rb,.php,.sh,.ps1,.ini,.toml,.cfg,.log,.tex,.rst,.ipynb";

function Knowledge({ projectId }: any) {
  const { toast } = useApp();
  const [docs, setDocs] = useState<any[] | null>(null);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const load = () => api.get(`/projects/${projectId}/knowledge`).then((r) => setDocs(r.docs));
  useEffect(() => { load(); }, [projectId]);
  useEvent((e) => { if (e.kind === "knowledge" && e.project_id === projectId) load(); });
  const add = async (files: FileList | null) => {
    if (!files?.length) return;
    setBusy(true);
    for (const f of Array.from(files)) {
      try { await api.upload(`/projects/${projectId}/knowledge`, f); } catch (e: any) { toast(`${f.name}: ${e.message}`, "error"); }
    }
    setBusy(false);
    if (input.current) input.current.value = "";
    load();
  };
  return (
    <div className="card">
      <h3><BookOpen size={16} /> Knowledge files</h3>
      <p className="muted small">Documents this project's chats can draw on: PDF, Word, PowerPoint, Excel, CSV, text and code. They are copied into KimiLM, split into passages and searched on this computer; each chat gets the passages that match its request, with the file name to cite.</p>
      {docs === null ? <Spinner /> : docs.length === 0 ? <p className="muted small">No files yet.</p> : docs.map((d) => (
        <div key={d.id} className="kb-row">
          <FileText size={14} className="muted" />
          <span className="grow ellipsis" title={d.path}>{d.name}</span>
          {d.status === "indexing" && <Badge kind="accent"><Spinner size={11} /> reading</Badge>}
          {d.status === "ready" && <span className="muted small">{d.chunks} passage{d.chunks === 1 ? "" : "s"} · {fmtBytes(d.size)}</span>}
          {d.status === "failed" && <Badge kind="warn" title={d.error}><AlertTriangle size={11} /> {String(d.error || "failed").slice(0, 60)}</Badge>}
          {d.status === "failed" && <button className="icon-btn" title="Try again" onClick={async () => { await api.post(`/knowledge/${d.id}/reindex`); load(); }}><RotateCcw size={13} /></button>}
          <button className="icon-btn" title="Remove from the project" onClick={async () => { await api.del(`/knowledge/${d.id}`); load(); }}><X size={13} /></button>
        </div>
      ))}
      <input ref={input} type="file" multiple accept={KNOWLEDGE_TYPES} hidden onChange={(e) => add(e.target.files)} />
      <div className="row gap-s mt"><Button icon={<Upload size={14} />} busy={busy} onClick={() => input.current?.click()}>Add files</Button>
        <span className="muted small">Up to 200 MB each. Scanned PDFs need a text version.</span></div>
    </div>
  );
}

function Notes({ projectId, notes, onSaved }: any) {
  const { toast } = useApp();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(notes || "");
  useEffect(() => { if (!editing) setDraft(notes || ""); }, [notes]);
  const save = async () => {
    try { await api.put(`/projects/${projectId}`, { notes: draft }); setEditing(false); onSaved(); } catch (e: any) { toast(e.message, "error"); }
  };
  return (
    <div className="card">
      <div className="row between"><h3><NotebookPen size={16} /> Project notes</h3>
        {!editing && <Button small onClick={() => setEditing(true)}>Edit</Button>}</div>
      <p className="muted small">A living document for this project: decisions, status and open questions. Every chat here reads it; the agent and the learning pass add dated lines.</p>
      {editing ? <>
        <textarea className="input mono" rows={12} value={draft} onChange={(e) => setDraft(e.target.value)}
          placeholder={"## Decisions\n- …\n\n## Status\n- …\n\n## Open questions\n- …"} />
        <div className="row gap-s mt"><Button kind="primary" onClick={save}>Save notes</Button><Button kind="ghost" onClick={() => { setEditing(false); setDraft(notes || ""); }}>Cancel</Button></div>
      </> : (notes || "").trim() ? <div className="notes-view"><Markdown>{notes}</Markdown></div>
        : <p className="muted small">Nothing yet. Decisions and status will appear here as you work.</p>}
    </div>
  );
}
import { ConnectorPicker, HarnessPicker, ModelPicker } from "../components/Pickers";

export default function ProjectView({ projectId }: { projectId?: string }) {
  const { toast, refreshProjects } = useApp();
  const isNew = !projectId || projectId === "new";
  const [p, setP] = useState<any>(isNew ? { name: "", instructions: "", folders: [], connectors: [], memory_enabled: 1 } : null);
  const [saving, setSaving] = useState(false);
  const [newFolder, setNewFolder] = useState("");
  const [newMemory, setNewMemory] = useState("");

  const load = async () => { if (!isNew) setP(await api.get(`/projects/${projectId}`)); };
  useEffect(() => { load(); }, [projectId]);
  useEvent((e) => { if (e.kind === "project_notes" && e.project_id === projectId) api.get(`/projects/${projectId}`).then((x) => setP((cur: any) => ({ ...cur, notes: x.notes }))); });

  const save = async () => {
    setSaving(true);
    try {
      const body = { name: p.name || "New project", instructions: p.instructions, folders: p.folders, default_model: p.default_model,
        default_harness: p.default_harness, connectors: p.connectors, memory_enabled: !!p.memory_enabled,
        memory_mode: p.memory_mode || "shared" };
      const out = isNew ? await api.post("/projects", body) : await api.put(`/projects/${projectId}`, body);
      await refreshProjects();
      toast("Project saved", "ok");
      if (isNew) go("project", out.id); else load();
    } catch (e: any) { toast(e.message, "error"); } finally { setSaving(false); }
  };

  if (!p) return <div className="center-screen"><Spinner /></div>;
  return (
    <div className="page">
      <PageHeader title={isNew ? "New project" : p.name}
        subtitle="A project keeps related chats together with shared instructions, folders, defaults and memory."
        actions={!isNew && <Button kind="primary" icon={<MessageSquarePlus size={15} />} onClick={() => go("home", projectId)}>New chat in project</Button>} />
      <div className="grid-2">
        <div className="card">
          <Field label="Name"><input className="input" value={p.name} onChange={(e) => setP({ ...p, name: e.target.value })} placeholder="e.g. Monthly energy report" /></Field>
          <Field label="Instructions" help="Always given to the agent in this project: goals, tone, conventions, what to avoid.">
            <textarea className="input" rows={7} value={p.instructions} onChange={(e) => setP({ ...p, instructions: e.target.value })}
              placeholder="e.g. Reports are for the board: plain English, numbers rounded, always cite the source file." />
          </Field>
          <Field label="Folders" help="Every chat in this project can read and write these folders (changes still ask first).">
            {(p.folders || []).map((f: string) => (
              <div key={f} className="folder-row"><FolderOpen size={14} /><span className="ellipsis">{f}</span>
                <button className="icon-btn" onClick={() => setP({ ...p, folders: p.folders.filter((x: string) => x !== f) })}><X size={13} /></button></div>
            ))}
            <div className="row gap-s"><FolderInput value={newFolder} onChange={setNewFolder} />
              <Button onClick={() => { if (newFolder) { setP({ ...p, folders: [...p.folders, newFolder] }); setNewFolder(""); } }}>Add</Button></div>
          </Field>
          <div className="row gap-m wrap">
            <Field label="Default harness"><HarnessPicker value={p.default_harness} onChange={(v: string) => setP({ ...p, default_harness: v })} /></Field>
            <Field label="Default model"><ModelPicker value={p.default_model} onChange={(v: string) => setP({ ...p, default_model: v })} /></Field>
            <Field label="Connectors"><ConnectorPicker value={p.connectors || []} onChange={(v: string[]) => setP({ ...p, connectors: v })} /></Field>
          </div>
          <Toggle checked={!!p.memory_enabled} onChange={(v: boolean) => setP({ ...p, memory_enabled: v })} label="Use and learn project memory" />
          <Field label="Memory" help={(p.memory_mode || "shared") === "isolated"
            ? "Chats here see only this project's chats, notes, files and memory, and nothing from this project shows up anywhere else (your own sidebar search still finds them)."
            : "Chats here also use what KimiLM remembers about you and can look up your other chats."}>
            <select className="input" value={p.memory_mode || "shared"} onChange={(e) => setP({ ...p, memory_mode: e.target.value })}>
              <option value="shared">Shared with your other chats</option>
              <option value="isolated">Kept to this project</option>
            </select>
          </Field>
          <div className="row gap-s mt">
            <Button kind="primary" busy={saving} onClick={save}>{isNew ? "Create project" : "Save changes"}</Button>
            {!isNew && <ConfirmButton icon={<Trash2 size={14} />} onConfirm={async () => { await api.del(`/projects/${projectId}`); await refreshProjects(); go("home"); }}>Delete project</ConfirmButton>}
          </div>
        </div>
        {!isNew && (
          <div className="stack">
            <Notes projectId={projectId} notes={p.notes} onSaved={load} />
            <Knowledge projectId={projectId} />
            <div className="card">
              <h3>Chats</h3>
              {p.chats.length === 0 && <p className="muted">No chats yet.</p>}
              {p.chats.map((c: any) => (
                <div key={c.id} className="list-row" onClick={() => go("chat", c.id)}>
                  <span className="ellipsis">{c.title || "New chat"}</span><span className="muted small">{fmtTime(c.updated)}</span>
                </div>
              ))}
            </div>
            <div className="card">
              <h3><Brain size={16} /> Project memory</h3>
              <p className="muted small">Learned from chats in this project, or added by you. Used in every chat here.</p>
              {p.memories.map((m: any) => (
                <div key={m.id} className="memory-row"><span>{m.text}</span>
                  <button className="icon-btn" onClick={async () => { await api.del(`/memory/${m.id}`); load(); }}><X size={13} /></button></div>
              ))}
              <div className="row gap-s mt"><input className="input" value={newMemory} placeholder="Add something the agent should know"
                onChange={(e) => setNewMemory(e.target.value)} />
                <Button onClick={async () => { try { await api.post("/memory", { scope: "project", scope_id: projectId, text: newMemory }); setNewMemory(""); load(); } catch (e: any) { toast(e.message, "error"); } }}>Add</Button></div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
