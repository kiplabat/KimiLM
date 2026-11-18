// Images (2.11.0): make and change pictures, look through the ones made, and choose what makes them:
// a model on this computer (stable-diffusion.cpp), or an online one (OpenAI, Google).
import React, { useEffect, useRef, useState } from "react";
import { Cloud, Cpu, Download, ExternalLink, Image as ImageIcon, Plus, Square, Star, Trash2, Upload, Wand2, X } from "lucide-react";
import { api, desktop, fileUrl, fmtBytes, fmtTime } from "../api";
import { go, useApp, useEvent } from "../store";
import { Badge, Button, ConfirmButton, Empty, Field, Modal, PageHeader, Progress, Spinner, Tabs } from "../components/ui";
import { EDIT_KEY, PictureGrid } from "../components/Pictures";
import { WorkLine } from "../components/Work";

const SHAPES: Record<string, [string, number]> = {
  square: ["Square", 1], landscape: ["Landscape (3:2)", 1.5], portrait: ["Portrait (2:3)", 2 / 3],
  wide: ["Wide (16:9)", 16 / 9], tall: ["Tall (9:16)", 9 / 16],
};
const ENGINE_NAMES: Record<string, string> = {
  cpu: "Processor (works everywhere)", vulkan: "Graphics card (Vulkan: Intel, AMD, NVIDIA)", cuda: "NVIDIA graphics card (CUDA)",
  metal: "Apple graphics (Metal)", custom: "Your own build",
};
const SLOTS: [string, string, string][] = [
  ["model", "Checkpoint (one file with everything)", "Stable Diffusion 1.5 / SDXL style models: .safetensors, .ckpt or .gguf"],
  ["diffusion_model", "Diffusion model", "For models that come in parts (FLUX, Z-Image, Qwen Image…)"],
  ["llm", "Text encoder (language model)", ""], ["vae", "VAE", ""], ["clip_l", "CLIP-L", ""], ["clip_g", "CLIP-G", ""], ["t5xxl", "T5-XXL", ""],
];

function sizeFor(model: any, shape: string) {
  const s = model?.settings || {};
  const bw = s.width || 512, bh = s.height || 512, r = SHAPES[shape]?.[1] || 1;
  if (r === 1) return [bw, bh];
  const side = Math.sqrt(bw * bh);
  return [Math.round(side * Math.sqrt(r) / 16) * 16, Math.round(side / Math.sqrt(r) / 16) * 16];
}

function external(url: string) { return (e: React.MouseEvent) => { if (desktop) { e.preventDefault(); desktop.openExternal(url); } }; }

// ---------------------------------------------------------------------------------------------- Create
function Create({ state, toModels }: any) {
  const { toast } = useApp();
  const ready = state.models.filter((m: any) => m.ready);
  const [prompt, setPrompt] = useState("");
  const [model, setModel] = useState("");
  const [shape, setShape] = useState("square");
  const [count, setCount] = useState(1);
  const [more, setMore] = useState(false);
  const [negative, setNegative] = useState("");
  const [seed, setSeed] = useState("");
  const [steps, setSteps] = useState("");
  const [strength, setStrength] = useState(0.6);
  const [source, setSource] = useState<any>(null);
  const [redraw, setRedraw] = useState(false);
  const [jobs, setJobs] = useState<any[]>(state.jobs || []);
  const file = useRef<HTMLInputElement>(null);
  useEffect(() => {      // "Change this picture" from a chat or the library
    const path = sessionStorage.getItem(EDIT_KEY);
    if (path) { sessionStorage.removeItem(EDIT_KEY); setSource({ path, name: path.split(/[\\/]/).pop() }); }
  }, []);
  useEvent((e) => {
    if (e.kind !== "image_job") return;
    setJobs((list) => list.some((j) => j.id === e.id) ? list.map((j) => j.id === e.id ? e : j) : [...list, e]);
  });
  const chosen = ready.find((m: any) => m.id === model) || ready.find((m: any) => m.id === state.default) || ready[0];
  const running = jobs.filter((j) => j.state === "running");
  const start = async () => {
    const [width, height] = sizeFor(chosen, shape);
    const body: any = { prompt, model: chosen?.id, count, negative, seed: seed === "" ? undefined : Number(seed), steps: steps === "" ? undefined : Number(steps) };
    if (source) Object.assign(body, { image: source.path, strength, redraw });
    if (!source || shape !== "square") Object.assign(body, { width, height });      // a change keeps the picture's shape
    try { const job = await api.post("/images/generate", body); setJobs((l) => [...l.filter((j) => j.id !== job.id), job]); }
    catch (e: any) { toast(e.message, "error"); }
  };
  const pick = async (f?: File) => {
    if (!f) return;
    try { setSource(await api.upload("/images/upload", f)); } catch (e: any) { toast(e.message, "error"); }
  };
  if (!ready.length) return (
    <Empty icon={<ImageIcon size={34} />} title="Choose what makes the pictures first">
      <p>Download an image model for this computer (it then works without the internet), or switch on an online one.</p>
      <Button kind="primary" onClick={toModels}>Set up an image model</Button>
    </Empty>
  );
  const online = chosen?.kind === "online";
  return (
    <div className="grid-2 image-studio">
      <div className="card stack-s">
        <Field label={source ? "What should change?" : "What should the picture show?"}
          help={source ? "Say what should be different, or describe the picture as it should look afterwards." : "The subject, the setting, the style (photo, drawing, logo…), the light and the colours. Put words that must appear in the picture in quotes."}>
          <textarea className="input" rows={5} autoFocus value={prompt} onChange={(e) => setPrompt(e.target.value)}
            placeholder={source ? "e.g. Make the sky a warm sunset and remove the car." : "e.g. A solar-powered water pump beside a maize field at golden hour, wide photo, soft light."} />
        </Field>
        {source ? (
          <div className="image-source">
            <img src={fileUrl(source.path)} alt="" onError={(e) => ((e.target as HTMLImageElement).style.display = "none")} />
            <div className="grow"><b>Changing this picture</b><div className="small muted ellipsis">{source.name}</div></div>
            <button className="icon-btn" title="Make a new picture instead" onClick={() => setSource(null)}><X size={16} /></button>
          </div>
        ) : (
          <div><Button small icon={<Upload size={13} />} onClick={() => file.current?.click()}>Change a picture you have…</Button>
            <input ref={file} type="file" accept="image/*" hidden onChange={(e) => { pick(e.target.files?.[0]); e.target.value = ""; }} /></div>
        )}
        {source && !online && chosen?.settings?.edit !== "reference" && (
          <div className="small muted stack-s">
            <span>A simple change needs no model and is done in a second, at full size: a glow on the outline, a blur, black and white, brighter or darker, another size or file format, a rotation, a border. Ask for just that.</span>
            <span>For anything else, {chosen?.name} redraws the picture from a description: it cannot follow an instruction such as “remove the car”.</span>
            <label className="row gap-s"><input type="checkbox" checked={redraw} onChange={(e) => setRedraw(e.target.checked)} /> My text describes the finished picture: redraw it</label>
          </div>
        )}
        <div className="row gap-m wrap">
          <Field label="Made by">
            <select className="input" value={chosen?.id || ""} onChange={(e) => setModel(e.target.value)}>
              {ready.map((m: any) => <option key={m.id} value={m.id}>{m.name}{m.kind === "online" ? " (online)" : ""}</option>)}
            </select>
          </Field>
          <Field label="Shape">
            <select className="input" value={shape} onChange={(e) => setShape(e.target.value)}>
              {Object.entries(SHAPES).map(([k, [label]]) => <option key={k} value={k}>{source && k === "square" ? "Same as the picture" : label}</option>)}
            </select>
          </Field>
          <Field label="How many">
            <select className="input narrow" value={count} onChange={(e) => setCount(Number(e.target.value))}>{[1, 2, 3, 4].map((n) => <option key={n}>{n}</option>)}</select>
          </Field>
        </div>
        <div className="small muted">
          {online ? <><Cloud size={12} /> Online: the prompt{source ? " and the picture are" : " is"} sent to {chosen.name}; about US${chosen.settings?.per_image ?? 0.04} per picture from this month's online budget.</>
            : <><Cpu size={12} /> On this computer ({ENGINE_NAMES[state.engines.in_use]?.split(" (")[0] || "image engine"}): nothing leaves it. Without a graphics card a picture can take minutes.</>}
        </div>
        <a className="small" onClick={() => setMore(!more)}>{more ? "Fewer settings" : "More settings"}</a>
        {more && <div className="row gap-m wrap">
          {!online && <Field label="Leave out" help="Things that must not appear"><input className="input" value={negative} onChange={(e) => setNegative(e.target.value)} placeholder="e.g. text, people" /></Field>}
          {!online && <Field label="Steps" help={`Model's own: ${chosen?.settings?.steps ?? 20}`}><input className="input narrow" type="number" min={1} max={150} value={steps} onChange={(e) => setSteps(e.target.value)} /></Field>}
          {!online && <Field label="Seed" help="Same seed, same picture"><input className="input narrow" type="number" value={seed} onChange={(e) => setSeed(e.target.value)} placeholder="random" /></Field>}
          {!online && source && chosen?.settings?.edit !== "reference" && <Field label={`How much may change: ${Math.round(strength * 100)}%`} help="Low keeps the picture, high redraws it">
            <input type="range" min={0.1} max={1} step={0.05} value={strength} onChange={(e) => setStrength(Number(e.target.value))} /></Field>}
          {online && <span className="small muted">Online models choose their own steps and seed.</span>}
        </div>}
        <div className="row gap-s">
          <Button kind="primary" icon={<Wand2 size={14} />} disabled={!prompt.trim()} onClick={start}>{source ? "Change the picture" : count > 1 ? `Make ${count} pictures` : "Make the picture"}</Button>
          {running.length > 0 && <span className="note"><Spinner size={14} /> {running[running.length - 1].text}</span>}
        </div>
      </div>
      <div className="stack-s">
        {jobs.length === 0 && <div className="card muted">The pictures you make appear here, and stay in the Library.</div>}
        {[...jobs].reverse().map((j) => (
          <div key={j.id} className="card stack-s">
            <div className="row between gap-s">
              <div className="ellipsis" title={j.prompt}><b>{j.prompt}</b></div>
              {j.state === "running" ? <Button small icon={<Square size={12} />} onClick={() => api.del(`/images/jobs/${j.id}`)}>Stop</Button>
                : j.state === "done" ? <Badge kind="ok">done</Badge> : <Badge kind={j.state === "failed" ? "bad" : "neutral"}>{j.state}</Badge>}
            </div>
            {j.state === "running" && <div className="note"><Spinner size={14} /> {j.text}</div>}
            {j.error && <div className="small bad">{j.error}</div>}
            <WorkLine jobId={j.job_id} running={j.state === "running"} />
            <PictureGrid large items={j.images} onChange={(p: any) => setSource({ path: p.path, name: p.name })} />
          </div>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------------------------- Library
function Library({ onChange }: any) {
  const [items, setItems] = useState<any[] | null>(null);
  const load = () => api.get("/images/library?limit=200").then((r) => setItems(r.images.filter((i: any) => i.exists)));
  useEffect(() => { load(); }, []);
  useEvent((e) => { if (e.kind === "images_changed") load(); });
  if (!items) return <Spinner />;
  if (!items.length) return <Empty icon={<ImageIcon size={34} />} title="No pictures yet">Pictures made here or by agents in chats are kept in this list, each with its prompt, model and seed.</Empty>;
  return (
    <div className="stack-s">
      <div className="small muted">{items.length} picture{items.length === 1 ? "" : "s"}, newest first. Click one for its prompt, seed and download.</div>
      <PictureGrid large items={items} onChange={onChange} />
      <div className="card">
        {items.slice(0, 40).map((i) => (
          <div key={i.id} className="list-row" style={{ cursor: "default" }}>
            <div className="grow ellipsis" title={i.prompt}>{i.prompt}</div>
            <span className="small muted">{i.model_name} · {fmtTime(i.created)}</span>
            {i.chat_id && <a className="small" onClick={() => go("chat", i.chat_id)}>chat</a>}
            <ConfirmButton small kind="ghost" onConfirm={() => api.del(`/images/library/${i.id}?file=1`).then(load)}><Trash2 size={13} /></ConfirmButton>
          </div>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------------------------- Models
function AddLocal({ onClose }: any) {
  const { toast } = useApp();
  const [name, setName] = useState("");
  const [files, setFiles] = useState<Record<string, string>>({});
  const [parts, setParts] = useState(false);
  const [edit, setEdit] = useState("img2img");
  const [s, setS] = useState<any>({ steps: 20, cfg: 7, width: 512, height: 512, args: "" });
  const shown = SLOTS.filter(([slot]) => parts ? slot !== "model" : slot === "model");
  const save = async () => {
    try { await api.post("/images/models", { name, files, edit, settings: s }); onClose(); } catch (e: any) { toast(e.message, "error"); }
  };
  return (
    <Modal wide title="Add an image model you already have" onClose={onClose}
      footer={<Button kind="primary" disabled={!Object.values(files).some(Boolean)} onClick={save}>Add the model</Button>}>
      <p className="small muted">The files stay where they are and are never changed. Models from ComfyUI, Automatic1111, Forge and similar apps work when stable-diffusion.cpp supports their kind.</p>
      <Field label="Name"><input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. DreamShaper 8" /></Field>
      <div className="row gap-s" style={{ marginBottom: 10 }}>
        <Button small kind={parts ? "default" : "primary"} onClick={() => { setParts(false); setFiles({}); }}>One file</Button>
        <Button small kind={parts ? "primary" : "default"} onClick={() => { setParts(true); setFiles({}); setS({ ...s, cfg: 1, steps: 8, width: 1024, height: 1024 }); }}>Several parts</Button>
      </div>
      {shown.map(([slot, label, help]) => (
        <Field key={slot} label={label} help={help}>
          <div className="row gap-s">
            <input className="input mono" value={files[slot] || ""} onChange={(e) => setFiles({ ...files, [slot]: e.target.value })} placeholder="Full path of the file" />
            {desktop && <Button small onClick={async () => { const p = await desktop.pickFile([{ name: "Model files", extensions: ["safetensors", "sft", "gguf", "ckpt", "pth"] }]); if (p) setFiles({ ...files, [slot]: p }); }}>Browse…</Button>}
          </div>
        </Field>
      ))}
      <div className="row gap-m wrap">
        <Field label="Steps"><input className="input narrow" type="number" value={s.steps} onChange={(e) => setS({ ...s, steps: Number(e.target.value) })} /></Field>
        <Field label="Guidance (CFG)" help="1 for turbo and FLUX-style models"><input className="input narrow" type="number" step={0.5} value={s.cfg} onChange={(e) => setS({ ...s, cfg: Number(e.target.value) })} /></Field>
        <Field label="Width"><input className="input narrow" type="number" step={64} value={s.width} onChange={(e) => setS({ ...s, width: Number(e.target.value) })} /></Field>
        <Field label="Height"><input className="input narrow" type="number" step={64} value={s.height} onChange={(e) => setS({ ...s, height: Number(e.target.value) })} /></Field>
      </div>
      <Field label="When it changes a picture">
        <select className="input" value={edit} onChange={(e) => setEdit(e.target.value)}>
          <option value="img2img">It redraws the picture (most models)</option>
          <option value="reference">It follows an instruction about the picture (edit models: FLUX Kontext, FLUX.2, Qwen Image Edit)</option>
        </select>
      </Field>
      <Field label="Extra options for the image program" help="Passed to sd-cli as written, e.g. --offload-to-cpu --sampling-method euler"><input className="input mono" value={s.args} onChange={(e) => setS({ ...s, args: e.target.value })} /></Field>
    </Modal>
  );
}

function EditModel({ model, onClose }: any) {
  const { toast } = useApp();
  const [name, setName] = useState(model.name);
  const [s, setS] = useState<any>({ ...model.settings });
  const num = (key: string, label: string, step = 1, help = "") => (
    <Field label={label} help={help}><input className="input narrow" type="number" step={step} value={s[key] ?? ""} onChange={(e) => setS({ ...s, [key]: Number(e.target.value) })} /></Field>);
  return (
    <Modal title={model.name} onClose={onClose}
      footer={<Button kind="primary" onClick={async () => { try { await api.put(`/images/models/${model.id}`, { name, settings: s }); onClose(); } catch (e: any) { toast(e.message, "error"); } }}>Save</Button>}>
      <Field label="Name"><input className="input" value={name} onChange={(e) => setName(e.target.value)} /></Field>
      {model.kind === "local" ? <>
        <div className="row gap-m wrap">{num("steps", "Steps")}{num("cfg", "Guidance (CFG)", 0.5)}{num("width", "Width", 64)}{num("height", "Height", 64)}</div>
        <Field label="Extra options for the image program" help="Passed to sd-cli as written"><input className="input mono" value={s.args || ""} onChange={(e) => setS({ ...s, args: e.target.value })} /></Field>
        <div className="small muted">{Object.entries(model.files || {}).map(([slot, path]: any) => <div key={slot} className="ellipsis" title={path}>{slot.replace("_", " ")}: {path}</div>)}</div>
      </> : <div className="row gap-m wrap">{num("per_image", "Cost per picture (US$)", 0.01, "Used for the monthly budget: check the provider's price list")}</div>}
    </Modal>
  );
}

function OnlineCard({ provider, onAdded }: any) {
  const { toast } = useApp();
  const [key, setKey] = useState("");
  const [list, setList] = useState<any>(null);
  const [pick, setPick] = useState("");
  const [busy, setBusy] = useState(false);
  const load = () => api.get(`/images/online/${provider.id}/models`).then((r) => { setList(r); setPick(r.models[0] || ""); }).catch(() => setList({ models: [], from: "" }));
  useEffect(() => { if (provider.ready) load(); }, [provider.ready]);
  const saveKey = async () => {
    setBusy(true);
    try { await api.put(`/online/providers/${provider.id}/key`, { key }); setKey(""); onAdded(); } catch (e: any) { toast(e.message, "error"); }
    setBusy(false);
  };
  return (
    <div className="card stack-s">
      <div className="row between"><b>{provider.name}</b>{provider.ready ? <Badge kind="ok">key saved</Badge> : <Badge>no key yet</Badge>}</div>
      {!provider.ready ? (
        <div className="row gap-s">
          <input className="input" type="password" placeholder={`${provider.name} API key`} value={key} onChange={(e) => setKey(e.target.value)} />
          <Button small kind="primary" busy={busy} disabled={!key.trim()} onClick={saveKey}>Save</Button>
        </div>
      ) : !list ? <Spinner /> : (
        <div className="row gap-s">
          <input className="input" list={`image-models-${provider.id}`} value={pick} onChange={(e) => setPick(e.target.value)} placeholder="Image model" />
          <datalist id={`image-models-${provider.id}`}>{list.models.map((m: string) => <option key={m} value={m} />)}</datalist>
          <Button small icon={<Plus size={13} />} disabled={!pick.trim()} onClick={async () => { try { await api.post("/images/models", { kind: "online", provider: provider.id, remote_id: pick }); onAdded(); } catch (e: any) { toast(e.message, "error"); } }}>Add</Button>
        </div>
      )}
      <div className="small muted">{provider.ready && list?.from === "suggestions" ? "The provider's list could not be read: these are suggestions, type another name if needed. " : ""}
        The same key as for chat (Models → Online). The prompt, and a picture you ask it to change, are sent to {provider.name}.</div>
    </div>
  );
}

function ModelsTab({ state, reload }: any) {
  const { toast } = useApp();
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<any>(null);
  const [progress, setProgress] = useState<any>({});
  const [downloads, setDownloads] = useState<any>({});
  useEvent((e) => {
    if (e.kind === "image_engine" && e.action === "install_progress") setProgress((p: any) => ({ ...p, [e.backend]: e }));
    if (e.kind === "image_engine" && e.action !== "install_progress") setProgress((p: any) => ({ ...p, [e.backend]: undefined }));
    if (e.kind === "image_download") setDownloads((d: any) => ({ ...d, [e.key]: e }));
  });
  const eng = state.engines;
  const local = state.models.filter((m: any) => m.kind === "local"), online = state.models.filter((m: any) => m.kind === "online");
  const row = (m: any) => (
    <div key={m.id} className="list-row" onClick={() => setEditing(m)}>
      <div className="grow">
        <div className="row gap-s"><b>{m.name}</b>{m.default && <Badge kind="accent">default</Badge>}
          {!m.ready && <Badge kind="warn" title={m.missing?.length ? `Missing: ${m.missing.join(", ")}` : ""}>{m.missing?.length ? "files missing" : m.kind === "local" ? "needs the image engine" : "needs a key"}</Badge>}</div>
        <div className="small muted">{m.kind === "local" ? `${fmtBytes(m.size)} · ${m.settings?.steps} steps · ${m.settings?.width}×${m.settings?.height}${m.licence ? ` · ${m.licence}` : ""}` : `online · about US$${m.settings?.per_image} per picture`}</div>
      </div>
      <div className="row gap-s" onClick={(e) => e.stopPropagation()}>
        {!m.default && <Button small kind="ghost" icon={<Star size={13} />} title="Use by default" onClick={() => api.put(`/images/models/${m.id}`, { default: true }).then(reload)} />}
        <ConfirmButton small kind="ghost" onConfirm={() => api.del(`/images/models/${m.id}${m.recipe ? "?files=1" : ""}`).then(reload)}><Trash2 size={13} /></ConfirmButton>
      </div>
    </div>
  );
  return (
    <div className="stack">
      <div className="card stack-s">
        <div className="row between"><h3><Cpu size={16} /> On this computer</h3>
          <span className="small muted">stable-diffusion.cpp {eng.tag}</span></div>
        <p className="small muted">Pictures are made by a small program that KimiLM downloads here and runs only while a picture is being made; afterwards its memory is free again for your text models. No internet is needed once the engine and a model are here.</p>
        {eng.available.map((b: string) => {
          const p = progress[b] || eng.installing[b], installed = eng.installed.includes(b);
          return (
            <div key={b} className="row between gap-s">
              <div><b>{ENGINE_NAMES[b] || b}</b> {installed && <Badge kind="ok">installed</Badge>} {eng.in_use === b && <Badge kind="accent">in use</Badge>}</div>
              {p ? <div className="row gap-s grow" style={{ maxWidth: 320 }}><Progress value={p.done || 0} total={p.total || 1} /><span className="small muted">{p.total ? `${fmtBytes(p.done || 0)} of ${fmtBytes(p.total)}` : "starting…"}</span></div>
                : installed ? <ConfirmButton small kind="ghost" onConfirm={() => api.del(`/images/engine/${b}`).then(reload)}>Remove</ConfirmButton>
                  : <Button small icon={<Download size={13} />} onClick={async () => { try { await api.post("/images/engine", { backend: b }); setProgress((x: any) => ({ ...x, [b]: {} })); } catch (e: any) { toast(e.message, "error"); } }}>Install</Button>}
            </div>
          );
        })}
        {eng.installed.includes("custom") && <div className="small muted">Using your own build of the image program (DVA_SD_CLI).</div>}
        <div className="row gap-m wrap">
          <Field label="Make pictures with" inline>
            <select className="input narrow-auto" value={eng.compute} onChange={(e) => api.put("/images/settings", { image_compute: e.target.value }).then(reload)}>
              <option value="auto">Automatic (a real graphics card when there is one)</option><option value="cpu">The processor only</option>
              {eng.installed.filter((b: string) => !["cpu", "custom"].includes(b)).map((b: string) => <option key={b} value={b}>{ENGINE_NAMES[b]}</option>)}
            </select>
          </Field>
          <Field label="Stop a picture after (minutes)" inline>
            <input className="input narrow" type="number" min={1} max={720} defaultValue={state.timeout_minutes} onBlur={(e) => api.put("/images/settings", { image_timeout_minutes: Number(e.target.value) })} />
          </Field>
        </div>
      </div>

      <div className="card stack-s">
        <div className="row between"><h3>Models on this computer</h3><Button small icon={<Plus size={13} />} onClick={() => setAdding(true)}>Add one you already have</Button></div>
        {local.length === 0 && <div className="small muted">None yet. Download one below, or add a model file you already have.</div>}
        {local.map(row)}
        <div className="field-label" style={{ marginTop: 6 }}>Recommended</div>
        {state.recipes.map((r: any) => {
          const d = downloads[r.key] || r.download, active = d && ["starting", "downloading"].includes(d.state);
          return (
            <div key={r.key} className="recipe">
              <div className="grow">
                <div className="row gap-s wrap"><b>{r.name}</b><span className="small muted">about {r.approx_gb} GB</span>
                  {r.installed ? <Badge kind="ok">here</Badge> : r.fits ? <Badge kind="ok">fits this computer</Badge> : <Badge kind="warn" title={`Comfortable with ${r.min_ram_gb} GB of memory or more`}>may be too big</Badge>}
                  {r.edit === "reference" && <Badge kind="accent">edits by instruction</Badge>}</div>
                <div className="small">{r.about}</div>
                <div className="small muted">Licence: {r.licence} · <a href={r.page} target="_blank" rel="noreferrer" onClick={external(r.page)}>model page <ExternalLink size={11} /></a></div>
                {active && <div className="row gap-s"><Progress value={d.done} total={d.total || 1} /><span className="small muted">{d.file} · {fmtBytes(d.done)} of {fmtBytes(d.total)}</span></div>}
                {d?.state === "failed" && <div className="small bad">{d.error}</div>}
              </div>
              {active ? <Button small onClick={() => api.del(`/images/recipes/${r.key}/download`)}>Cancel</Button>
                : !r.installed && <Button small icon={<Download size={13} />} onClick={async () => { try { setDownloads({ ...downloads, [r.key]: await api.post(`/images/recipes/${r.key}/download`) }); } catch (e: any) { toast(e.message, "error"); } }}>Download</Button>}
            </div>
          );
        })}
        {eng.installed.length === 0 && <div className="note"><Download size={14} /> A model also needs the image engine above: install the processor build first, it works on every computer.</div>}
      </div>

      <div className="stack-s">
        <h3><Cloud size={16} /> Online</h3>
        {online.length > 0 && <div className="card">{online.map(row)}</div>}
        <div className="grid-2 image-online">{state.online.map((p: any) => <OnlineCard key={p.id} provider={p} onAdded={reload} />)}</div>
        <div className="card stack-s">
          <div className="row between"><b>Anthropic (Claude)</b><Badge>reads pictures</Badge></div>
          <div className="small muted">{state.claude} Claude is a strong choice for the chat that plans a picture: it writes the prompt, looks at the result and asks for changes.</div>
        </div>
      </div>
      {adding && <AddLocal onClose={() => { setAdding(false); reload(); }} />}
      {editing && <EditModel model={editing} onClose={() => { setEditing(null); reload(); }} />}
    </div>
  );
}

export default function Images() {
  const [state, setState] = useState<any>(null);
  const [tab, setTab] = useState(sessionStorage.getItem(EDIT_KEY) ? "create" : "");
  const load = () => api.get("/images").then(setState);
  useEffect(() => { load(); }, []);
  useEvent((e) => { if (e.kind === "images_changed" || (e.kind === "image_engine" && e.action !== "install_progress") || (e.kind === "image_download" && e.state !== "downloading")) load(); });
  if (!state) return <div className="page"><Spinner /></div>;
  const current = tab || (state.models.some((m: any) => m.ready) ? "create" : "models");
  return (
    <div className="page">
      <PageHeader title="Images" subtitle="Make pictures from a description and change existing ones, on this computer or with an online model. Agents in chats use the same models." />
      <Tabs value={current} onChange={setTab} tabs={[{ id: "create", label: "Create" }, { id: "library", label: "Library" }, { id: "models", label: "Models", count: state.models.length }]} />
      {current === "create" && <Create state={state} toModels={() => setTab("models")} />}
      {current === "library" && <Library onChange={(p: any) => { sessionStorage.setItem(EDIT_KEY, p.path); setTab("create"); }} />}
      {current === "models" && <ModelsTab state={state} reload={load} />}
    </div>
  );
}
