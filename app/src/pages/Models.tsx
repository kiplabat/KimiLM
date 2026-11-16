import React, { useEffect, useState } from "react";
import { AlertTriangle, Cpu, Download, Eye, Gauge, HardDrive, Layers, MemoryStick, Play, RotateCcw, Search, Star, Trash2, Wand2, X, Zap } from "lucide-react";
import { api, desktop, enc, fmtBytes } from "../api";
import { go, useApp, useEvent } from "../store";
import { Badge, Button, ConfirmButton, Field, FolderInput, PageHeader, Progress, Spinner, Tabs, Toggle, fitBadge } from "../components/ui";
import Online from "./Online";

const CTX = [2048, 4096, 8192, 16384, 32768, 65536];

function fmtEta(s?: number | null) {
  if (!s) return "";
  if (s < 90) return `${s} s left`;
  if (s < 5400) return `${Math.round(s / 60)} min left`;
  return `${(s / 3600).toFixed(1)} h left`;
}

const DL_ACTIVE = ["queued", "connecting", "downloading", "retrying", "verifying"];

function DownloadsBar() {
  const { downloads, toast } = useApp();
  const shown = Object.values(downloads).filter((d: any) => [...DL_ACTIVE, "paused", "failed"].includes(d.state));
  if (!shown.length) return null;
  const act = async (fn: () => Promise<any>) => { try { await fn(); } catch (e: any) { toast(e.message, "error"); } };
  return (
    <div className="card">
      <h3>Downloads</h3>
      {shown.map((d: any) => {
        const active = DL_ACTIVE.includes(d.state);
        const pct = d.total ? Math.floor((d.done / d.total) * 100) : 0;
        return (
          <div key={d.id} className="download-row">
            <div className="row between wrap"><b>{d.name}{d.vision ? <> <Eye size={13} className="accent" /></> : null}</b>
              <span className="muted small">{fmtBytes(d.done)} / {fmtBytes(d.total)} · {pct}%{d.rate ? ` · ${fmtBytes(d.rate)}/s` : ""}{d.eta ? ` · ${fmtEta(d.eta)}` : ""}</span></div>
            <div className="row gap-s"><Progress value={d.done} total={d.total} />
              {(d.state === "failed" || d.state === "paused") && <Button small icon={<RotateCcw size={13} />} onClick={() => act(() => api.post(`/downloads/${d.id}/resume`))}>{d.state === "paused" ? "Resume" : "Try again"}</Button>}
              <button className="icon-btn" title={active ? "Cancel" : "Remove"} onClick={() => act(() => api.del(`/downloads/${d.id}`))}><X size={14} /></button></div>
            {active && <div className="dl-phase">{d.state === "queued" ? "Waiting to start…" : d.state === "verifying" ? "Checking the file…" : d.phase || (d.state === "connecting" ? "Connecting…" : "Downloading…")}
              {d.state === "downloading" && !d.rate && d.done === 0 ? " — waiting for the first data…" : ""}</div>}
            {!active && d.error && <div className="dl-error">{d.error}</div>}
          </div>
        );
      })}
      <p className="muted small">Downloads continue if you close the window, retry by themselves when the connection drops, and resume where they stopped after a restart.</p>
    </div>
  );
}

const gb = (mb: number) => `${(mb / 1024).toFixed(1)} GB`;

// Where llama-server actually put a loaded model: layers on the GPU and memory per device.
function placementText(l: any) {
  const p = l.placement || {};
  if (l.compute !== "gpu") return "everything on the CPU";
  const parts: string[] = [];
  if (p.layers) parts.push(p.gpu_layers === p.layers ? `all ${p.layers} layers on the GPU` : `${p.gpu_layers} of ${p.layers} layers on the GPU, the rest on the CPU`);
  const devs = Object.entries(p.devices || {}).map(([d, v]: any) => `${d === "CPU" ? "CPU" : d} ${gb((v.model_mb || 0) + (v.kv_mb || 0) + (v.compute_mb || 0))}`);
  if (devs.length) parts.push(devs.join(" · "));
  if (l.kv_on === "cpu") parts.push("context memory on the CPU");
  if (l.moe_cpu === "all") parts.push("MoE experts on the CPU");
  else if (typeof l.moe_cpu === "number" && l.moe_cpu > 0) parts.push(`experts of ${l.moe_cpu} layers on the CPU`);
  return parts.join(" · ");
}

// Saves when released: every change reloads the model.
function LayerSlider({ value, max, onCommit }: any) {
  const [v, setV] = useState(value);
  useEffect(() => setV(value), [value]);
  const commit = () => { if (v !== value) onCommit(v); };
  return (<>
    <input type="range" min={0} max={max} value={v} onChange={(e) => setV(Number(e.target.value))} onPointerUp={commit} onKeyUp={commit} onBlur={commit} />
    <span className="small">{v} of {max}</span>
  </>);
}

// Per-model choices for where each part of the model runs when it uses the GPU.
function Placement({ m, rt, set }: any) {
  const s = m.settings || {};
  const layers = (m.meta?.block_count || 0) + 1;
  const moe = (m.meta?.expert_count || 0) > 1;
  const usable: string[] = rt?.usable || [];
  const backend = usable.includes(s.gpu_backend) ? s.gpu_backend : usable[0];
  const devices = (rt?.devices?.[backend] || []).filter((d: any) => !d.error);
  const lay = s.gpu_layers ?? "auto";
  const layMode = lay === "auto" || lay === "all" ? lay : "some";
  const moeMode = !s.moe_cpu || s.moe_cpu === "none" ? "none" : s.moe_cpu === "all" ? "all" : "some";
  if (!usable.length) return (
    <div className="placement"><div className="warn-line"><AlertTriangle size={14} /> No installed GPU runtime can see a graphics card on this computer, so this model runs on the CPU. CUDA needs an NVIDIA card; Intel and AMD graphics use the Vulkan runtime (Hardware &amp; GPU tab).</div></div>
  );
  return (
    <div className="placement">
      <div className="placement-title"><Layers size={14} /> Where each part runs</div>
      <div className="row gap-m wrap">
        {usable.length > 1 && (
          <Field label="GPU runtime">
            <select className="input" value={backend} onChange={(e) => set(m.id, { gpu_backend: e.target.value, gpu_device: null })}>
              {usable.map((b: string) => <option key={b} value={b}>{(rt.labels?.[b] || b).replace(/ \(.*\)/, "")} — {(rt.devices?.[b] || []).map((d: any) => d.name).join(", ") || "custom"}</option>)}
            </select>
          </Field>
        )}
        {devices.length > 1 && (
          <Field label="Graphics card">
            <select className="input" value={s.gpu_device || ""} onChange={(e) => set(m.id, { gpu_device: e.target.value || null })}>
              <option value="">Automatic</option>
              {devices.map((d: any) => <option key={d.id} value={d.id}>{d.name} ({gb(d.total_mb)}{d.integrated ? ", shared" : ""})</option>)}
            </select>
          </Field>
        )}
        <Field label="Layers on the GPU" help={layMode === "auto" ? "As many as fit in the card's memory; the rest run on the CPU." : layMode === "all" ? "The whole model on the GPU." : `The first ${lay} layers on the GPU, the other ${Math.max(0, layers - Number(lay))} on the CPU.`}>
          <div className="row gap-s">
            <select className="input narrow" value={layMode} onChange={(e) => set(m.id, { gpu_layers: e.target.value === "some" ? Math.max(1, Math.floor(layers / 2)) : e.target.value })}>
              <option value="auto">Auto</option><option value="all">All</option><option value="some">Some…</option>
            </select>
            {layMode === "some" && <LayerSlider value={Number(lay)} max={layers > 1 ? layers : 100} onCommit={(v: number) => set(m.id, { gpu_layers: v })} />}
          </div>
        </Field>
        <Field label="Context memory" help="The conversation's working memory. On the CPU it frees graphics memory for more layers; on the GPU it is faster.">
          <div className="seg">
            {["gpu", "cpu"].map((v) => <button key={v} className={`seg-btn${(s.kv_on || "gpu") === v ? " on" : ""}`} onClick={() => set(m.id, { kv_on: v })}>{v.toUpperCase()}</button>)}
          </div>
        </Field>
        {moe && (
          <Field label="Experts (MoE)" help="Mixture-of-experts models: keeping the large expert weights on the CPU lets the rest run on a smaller card.">
            <div className="row gap-s">
              <select className="input narrow" value={moeMode} onChange={(e) => set(m.id, { moe_cpu: e.target.value === "some" ? Math.max(1, Math.floor(layers / 2)) : e.target.value })}>
                <option value="none">On the GPU</option><option value="some">Some on the CPU…</option><option value="all">All on the CPU</option>
              </select>
              {moeMode === "some" && <><input className="input narrow" type="number" min={1} max={layers} value={s.moe_cpu}
                onChange={(e) => set(m.id, { moe_cpu: Math.max(1, Number(e.target.value) || 1) })} /><span className="small">layers' experts on the CPU</span></>}
            </div>
          </Field>
        )}
        {s.mmproj && s.vision !== false && (
          <Field label="Vision part">
            <div className="seg">
              {["gpu", "cpu"].map((v) => <button key={v} className={`seg-btn${(s.mmproj_on || "gpu") === v ? " on" : ""}`} onClick={() => set(m.id, { mmproj_on: v })}>{v.toUpperCase()}</button>)}
            </div>
          </Field>
        )}
      </div>
      {devices.some((d: any) => d.integrated) && !devices.some((d: any) => !d.integrated) && (
        <p className="muted small">Integrated graphics use the computer's own memory, so moving parts to the GPU does not free memory, and speed depends on the chip. Use “Find the fastest setup” to measure it on this computer.</p>
      )}
    </div>
  );
}

const SPEC_LABEL: any = { none: "Off", ngram: "Repeat-aware (no extra model)", mtp: "Built-in multi-token prediction", draft: "Draft model" };
const KV_LABEL: any = { f16: "Full (16-bit)", q8_0: "Compact (8-bit)", q4_0: "Smallest (4-bit)" };

function speedSummary(st: any) {
  const p: string[] = [];
  p.push(st.compute === "gpu" ? (st.gpu_layers === "all" ? "everything on the GPU" : st.gpu_layers === "auto" || !st.gpu_layers ? "GPU, rest on the CPU" : `${st.gpu_layers} layers on the GPU`) : "CPU");
  if (st.moe_cpu === "all") p.push("experts on the CPU");
  if (st.threads) p.push(`${st.threads} writing threads`);
  if (st.threads_batch) p.push(`${st.threads_batch} reading threads`);
  if (st.kv_type && st.kv_type !== "f16") p.push(`${(KV_LABEL[st.kv_type] || st.kv_type).toLowerCase()} context memory`);
  if (st.spec && st.spec !== "none") p.push(SPEC_LABEL[st.spec].toLowerCase());
  if (st.ctx) p.push(`context ${Number(st.ctx).toLocaleString()}`);
  if (st.priority && st.priority !== "normal") p.push(`${st.priority} priority`);
  return p.join(" · ");
}

// Speed: the suggested configuration for this computer (rules of thumb, then measurements), one button to use
// it, a measured comparison, and every setting still adjustable.
function Speed({ m, models, set, onDone }: any) {
  const { toast } = useApp();
  const [st, setSt] = useState<any>(null);
  const [open, setOpen] = useState(false);
  const [why, setWhy] = useState(false);
  useEffect(() => { api.get(`/models/${m.id}/tune`).then((r) => r.state === "running" && setSt(r)); }, [m.id]);
  useEvent((e) => {
    if (e.kind !== "tune" || e.model_id !== m.id) return;
    setSt(e);
    if (e.state === "done") { toast("Measured. The suggested configuration is updated — press Use suggested configuration to apply it.", "ok"); onDone(); }
    if (e.state === "failed") toast(e.error, "error");
  });
  const s = m.settings || {};
  const sug = m.suggested;
  const saved = m.bench?.tune;
  const rows = st?.results?.length ? st.results : saved?.results || [];
  const running = st?.state === "running";
  const reuse = (st?.state === "done" && st.reuse) || saved?.reuse;
  const drafts = (models || []).filter((x: any) => x.id !== m.id && x.meta?.architecture === m.meta?.architecture && x.size <= m.size * 0.3);
  const apply = async () => { try { await api.post(`/models/${m.id}/suggested`); toast("Using the suggested configuration. You can still change anything below.", "ok"); onDone(); } catch (e: any) { toast(e.message, "error"); } };
  return (
    <div className="speed">
      <div className="row between wrap gap-s">
        <div className="speed-title"><Zap size={14} /> Speed</div>
        <div className="row gap-s wrap">
          {sug && (sug.differs.length === 0
            ? <span className="small ok">Using the suggested configuration{sug.measured ? " (measured)" : ""}</span>
            : <Button small kind="primary" icon={<Wand2 size={13} />} onClick={apply}>Use suggested configuration</Button>)}
          {running
            ? <Button small kind="ghost" icon={<X size={13} />} onClick={() => api.del(`/models/${m.id}/tune`)}>Stop measuring</Button>
            : <Button small icon={<Gauge size={13} />} title="Loads the model several times: CPU vs GPU, thread counts, speed-ups and follow-up reuse. About 5 minutes." onClick={async () => {
                try { await api.post(`/models/${m.id}/tune`); } catch (e: any) { toast(e.message, "error"); } }}>{saved ? "Measure again" : "Find the fastest setup"}</Button>}
        </div>
      </div>
      {sug && (
        <div className="small">
          <span className="muted">Suggested{sug.measured ? ` (measured ${new Date(sug.measured_at).toLocaleDateString()})` : " for this computer"}:</span> {speedSummary(sug.settings)}
          {sug.differs.length > 0 && <span className="muted"> · differs in: {sug.differs.map((k: string) => ({ compute: "where it runs", gpu_layers: "GPU layers", threads: "writing threads", threads_batch: "reading threads", kv_type: "context memory", flash_attn: "flash attention", priority: "priority", spec: "speculative decoding", ctx: "context", parallel: "simultaneous replies", moe_cpu: "experts", ubatch: "batch", draft_model: "draft model", kv_on: "context placement" } as any)[k] || k).join(", ")}</span>}
          {" "}<button className="link small" onClick={() => setWhy(!why)}>{why ? "Hide why" : "Why"}</button>
          {why && <ul className="speed-why">{sug.reasons.map((r: string, i: number) => <li key={i}>{r}</li>)}</ul>}
        </div>
      )}
      {reuse && <div className="small muted">Follow-up messages: {reuse.percent}% of the prompt reused from the cache ({reuse.cached} reused, {reuse.read} read).</div>}
      {running && <div className="row gap-s small"><Spinner size={12} /> {st.stage}: {st.label} ({st.step} of about {st.total}; each step loads the model again)</div>}
      {st?.state === "stopped" && <div className="small muted">Measuring stopped.</div>}
      {rows.length > 0 && (
        <details open={running}>
          <summary className="small">Measurements ({rows.length})</summary>
          <table className="data-table">
            <thead><tr><th>Step</th><th>Setup</th><th>Reads</th><th>Writes</th><th>Writes, repeated text</th><th>Where it ran</th></tr></thead>
            <tbody>{rows.map((r: any, i: number) => (
              <tr key={i}>
                <td className="small muted">{r.stage || ""}</td><td>{r.label}</td>
                <td>{r.error ? "—" : r.reads}</td><td>{r.error ? "—" : r.writes}</td><td>{r.writes_repeat ?? ""}</td>
                <td className="small muted">{r.error ? r.error : r.reuse ? `${r.reuse.percent}% reused` : r.note ? r.note : placementText({ compute: r.settings.compute, placement: r.placement, kv_on: r.settings.kv_on, moe_cpu: r.settings.moe_cpu })}</td>
              </tr>))}</tbody>
          </table>
          <div className="small muted">Tokens per second, with a short context. Long chats favour the GPU and compact context memory more.</div>
        </details>
      )}
      <button className="link small" onClick={() => setOpen(!open)}>{open ? "Hide the speed settings" : "Adjust the speed settings"}</button>
      {open && (
        <div className="row gap-m wrap">
          <Field label="Writing threads" help="Writing is limited by memory speed: usually best at one per physical core.">
            <input className="input narrow" type="number" min={1} max={128} placeholder="auto" value={s.threads || ""} onChange={(e) => set(m.id, { threads: e.target.value ? Number(e.target.value) : null })} />
          </Field>
          <Field label="Reading threads" help="Reading the prompt gains from every thread.">
            <input className="input narrow" type="number" min={1} max={128} placeholder="same" value={s.threads_batch || ""} onChange={(e) => set(m.id, { threads_batch: e.target.value ? Number(e.target.value) : null })} />
          </Field>
          <Field label="Context memory" help="Compact halves the memory and speeds up long chats.">
            <select className="input" value={s.kv_type || "f16"} onChange={(e) => set(m.id, { kv_type: e.target.value })}>
              {Object.keys(KV_LABEL).map((k) => <option key={k} value={k}>{KV_LABEL[k]}</option>)}
            </select>
          </Field>
          <Field label="Flash attention">
            <select className="input narrow" value={s.flash_attn || "auto"} onChange={(e) => set(m.id, { flash_attn: e.target.value })}>
              <option value="auto">Auto</option><option value="on">On</option><option value="off">Off</option>
            </select>
          </Field>
          <Field label="Priority" help="High makes other programs slower while the model works.">
            <select className="input narrow" value={s.priority || "normal"} onChange={(e) => set(m.id, { priority: e.target.value })}>
              <option value="normal">Normal</option><option value="medium">Above normal</option><option value="high">High</option>
            </select>
          </Field>
          <Field label="Speculative decoding" help="Writes several tokens at a time when the guess is right. Measure before relying on it.">
            <select className="input" value={s.spec || "none"} onChange={(e) => set(m.id, { spec: e.target.value })}>
              <option value="none">Off</option><option value="ngram">Repeat-aware (no extra model)</option>
              {(m.meta?.nextn_predict_layers || 0) > 0 && <option value="mtp">Built-in multi-token prediction</option>}
              {drafts.length > 0 && <option value="draft">Draft model</option>}
            </select>
          </Field>
          {s.spec === "draft" && (
            <Field label="Draft model">
              <select className="input" value={s.draft_model || ""} onChange={(e) => set(m.id, { draft_model: e.target.value || null })}>
                <option value="">Choose…</option>{drafts.map((d: any) => <option key={d.id} value={d.id}>{d.name}</option>)}
              </select>
            </Field>
          )}
          {s.compute === "gpu" && (
            <Field label="Batch size" help="Bigger batches read faster on a graphics card.">
              <select className="input narrow" value={s.ubatch || ""} onChange={(e) => set(m.id, { ubatch: e.target.value ? Number(e.target.value) : null })}>
                <option value="">Default (512)</option><option value="1024">1024</option><option value="2048">2048</option>
              </select>
            </Field>
          )}
        </div>
      )}
    </div>
  );
}

function Installed() {
  const { toast, modelStatus, boot } = useApp();
  const [data, setData] = useState<any>(null);
  const [busy, setBusy] = useState<string>("");
  const [rt, setRt] = useState<any>(null);
  const load = () => api.get("/models").then(setData);
  useEffect(() => { load(); api.get("/runtime").then(setRt); }, []);
  useEvent((e) => { if (["models_changed", "model_status"].includes(e.kind)) load(); if (e.kind === "runtime" && e.action !== "install_progress") api.get("/runtime").then(setRt); });
  const set = async (id: string, values: any) => { try { await api.put(`/models/${id}`, values); load(); } catch (e: any) { toast(e.message, "error"); } };
  if (!data) return <Spinner />;
  const gpuInstalled = (boot.runtime.installed || []).some((b: string) => b !== "cpu");
  return (
    <div className="stack">
      <DownloadsBar />
      {(data.loaded?.length > 0 || modelStatus.state === "loading" || modelStatus.state === "failed") && (
        <div className="card">
          <h3>Loaded now</h3>
          {modelStatus.state === "loading" && <div className="row gap-s small"><Spinner size={13} /> Loading {modelStatus.model_id}…</div>}
          {modelStatus.state === "failed" && <div className="error-line">{modelStatus.error}</div>}
          {(data.loaded || []).map((l: any) => (
            <div key={l.model_id} className="loaded-row">
              <div><b>{l.model_id}</b> {l.vision && <Eye size={13} className="accent" />} <span className="muted small">· {l.compute?.toUpperCase()} ({l.backend}) · context {l.ctx?.toLocaleString()}{l.parallel > 1 ? ` · ${l.parallel} replies at once` : ""} · about {(l.memory_mb / 1024).toFixed(1)} GB{l.users ? ` · in use (${l.users})` : ""}</span>
                <div className="small">{placementText(l)}</div>
                {l.note && <div className="warn-line small"><AlertTriangle size={12} /> {l.note}</div>}</div>
              <Button small disabled={l.users > 0} onClick={async () => { try { await api.post("/runtime/unload", { model_id: l.model_id }); load(); } catch (e: any) { toast(e.message, "error"); } }}>Unload</Button>
            </div>
          ))}
          <p className="muted small">Several models can be loaded at once (up to {boot.settings.max_loaded_models || 3}, if memory allows) so agents can work in parallel. Idle models unload after {boot.settings.unload_after_minutes || 20} minutes.</p>
        </div>
      )}
      {data.models.length === 0 && (
        <div className="card center"><p>No models yet.</p><Button kind="primary" onClick={() => go("models", "discover")}>Find a model</Button></div>
      )}
      {data.models.map((m: any) => {
        const s = m.settings || {};
        const b = m.bench || {};
        return (
          <div key={m.id} className="card model-card">
            <div className="row between wrap">
              <div>
                <h3>{m.name} {m.id === data.default && <Badge kind="accent"><Star size={11} /> default</Badge>}
                  {s.mmproj && s.vision !== false && <Badge kind="ok" title={s.mmproj}><Eye size={11} /> sees images</Badge>}</h3>
                <div className="muted small">{m.repo || m.path} · {m.quant} · {fmtBytes(m.size)}{m.meta?.architecture ? ` · ${m.meta.architecture}` : ""}{m.meta?.context_length ? ` · trained for ${m.meta.context_length.toLocaleString()} tokens` : ""}</div>
                {!m.exists && <div className="error-line">The model file is missing ({m.path}).</div>}
              </div>
              <div className="row gap-s">{fitBadge(m.fit, s.compute === "gpu" ? "gpu" : "cpu")}</div>
            </div>
            <div className="row gap-m wrap mt">
              <Field label="Run on">
                <div className="seg">
                  {["auto", "cpu", "gpu"].map((c) => (
                    <button key={c} className={`seg-btn${(s.compute || "auto") === c ? " on" : ""}`} disabled={c === "gpu" && !gpuInstalled}
                      title={c === "gpu" && !gpuInstalled ? "Install a GPU runtime in the Hardware tab first" : ""}
                      onClick={() => set(m.id, { compute: c })}>{c === "auto" ? "Auto" : c.toUpperCase()}</button>
                  ))}
                </div>
              </Field>
              <Field label="Context (memory of the conversation)">
                <select className="input" value={s.ctx || boot.settings.default_ctx || 8192} onChange={(e) => set(m.id, { ctx: Number(e.target.value) })}>
                  {CTX.map((c) => <option key={c} value={c}>{c.toLocaleString()} tokens</option>)}
                </select>
              </Field>
              <Field label="Simultaneous replies" help="For agents in parallel; uses more memory.">
                <select className="input narrow" value={s.parallel || 1} onChange={(e) => set(m.id, { parallel: Number(e.target.value) })}>
                  {[1, 2, 3, 4].map((n) => <option key={n} value={n}>{n}</option>)}
                </select>
              </Field>
              <Field label="Role for Auto" help="Workers do routine parts; a strategist leads hard work (online models usually lead).">
                <select className="input narrow" value={s.role || "worker"} onChange={(e) => set(m.id, { role: e.target.value })}>
                  <option value="worker">Worker</option><option value="strategist">Strategist</option><option value="both">Both</option><option value="off">Not used by Auto</option>
                </select>
              </Field>
              <Field label="Vision (images & video)">
                {s.mmproj ? <><Toggle checked={s.vision !== false} onChange={(v: boolean) => set(m.id, { vision: v })} label={s.vision !== false ? "On" : "Off"} />
                  {s.vision === false && s.vision_error && <div className="field-help">Turned off: {s.vision_error}</div>}</>
                  : <Button small icon={<Eye size={13} />} busy={busy === `vision-${m.id}`} onClick={async () => {
                      setBusy(`vision-${m.id}`);
                      try { const r = await api.post(`/models/${m.id}/vision`); toast(`Downloading the vision part from ${r.repo}`, "ok"); } catch (e: any) { toast(e.message, "error"); }
                      setBusy(""); }}>Add vision</Button>}
              </Field>
            </div>
            {s.compute === "gpu" && <Placement m={m} rt={rt} set={set} />}
            {b.at && (
              <div className="bench">
                <Gauge size={14} /> Last test ({b.compute?.toUpperCase()}): reply in {b.reply_seconds}s · reads {Math.round(b.prompt_per_second || 0)} tok/s · writes {(b.predicted_per_second || 0).toFixed(1)} tok/s · structured answers {b.json_valid ? "OK" : "failed"}
              </div>
            )}
            <div className="row gap-s mt wrap">
              <Button small icon={<Play size={13} />} busy={busy === `load-${m.id}`} onClick={async () => {
                setBusy(`load-${m.id}`); try { await api.post(`/models/${m.id}/load`, {}); } catch (e: any) { toast(e.message, "error"); } setBusy(""); }}>Load now</Button>
              <Button small icon={<Gauge size={13} />} busy={busy === `bench-${m.id}`} onClick={async () => {
                setBusy(`bench-${m.id}`); try { await api.post(`/models/${m.id}/benchmark`); load(); toast("Speed test done", "ok"); } catch (e: any) { toast(e.message, "error"); } setBusy(""); }}>Test speed</Button>
              {m.id !== data.default && <Button small icon={<Star size={13} />} onClick={() => set(m.id, { make_default: true })}>Make default</Button>}
              <ConfirmButton small icon={<Trash2 size={13} />} onConfirm={async () => { try { await api.del(`/models/${m.id}`); load(); } catch (e: any) { toast(e.message, "error"); } }}>Delete</ConfirmButton>
            </div>
            <Speed m={m} models={data.models} set={set} onDone={load} />
          </div>
        );
      })}
      <LocalModels onAdded={load} />
      <div className="card">
        <h3>Add a model file you already have</h3>
        <p className="muted small">Any GGUF file (from LM Studio, Ollama, Hugging Face…). It stays where it is.</p>
        <ImportFile onDone={load} />
      </div>
    </div>
  );
}

// Models other local-AI apps already downloaded (Ollama, LM Studio, Hugging Face cache, GPT4All, Jan, your folders).
export function LocalModels({ onAdded, compact }: any) {
  const { toast, boot, refreshBoot } = useApp();
  const [data, setData] = useState<any>(null);
  const [busy, setBusy] = useState("");
  const [folder, setFolder] = useState("");
  const scan = () => { setData(null); api.get("/models/local").then(setData).catch((e) => toast(e.message, "error")); };
  useEffect(() => { scan(); }, []);
  const add = async (f: any) => {
    setBusy(f.path);
    try { await api.post("/models/import", { path: f.path, name: f.name, mmproj: f.mmproj }); toast(`Added ${f.name}`, "ok"); await refreshBoot(); onAdded?.(); scan(); }
    catch (e: any) { toast(e.message, "error"); }
    setBusy("");
  };
  const fresh = (data?.found || []).filter((f: any) => !f.added_as);
  if (compact && data && !fresh.length) return null;
  return (
    <div className="card">
      <div className="row between"><h3>Already on this computer</h3>
        <button className="link" onClick={scan}>Scan again</button></div>
      {!data ? <div className="row gap-s muted small"><Spinner size={13} /> Looking for models from Ollama, LM Studio, Hugging Face, GPT4All and Jan…</div> : (
        <>
          <p className="muted small">{fresh.length ? `Found ${fresh.length} model${fresh.length > 1 ? "s" : ""} you can use without downloading. ` : "No other models found. "}
            They are used where they are (nothing is copied), and removing one here never deletes the other app's file.</p>
          {fresh.map((f: any) => (
            <div key={f.path} className="list-row" title={f.path}>
              <span className="ellipsis"><b>{f.name}</b> {f.vision && <Eye size={12} className="accent" />} <span className="muted small">{f.source} · {f.quant} · {fmtBytes(f.size)}{f.architecture ? ` · ${f.architecture}` : ""}</span></span>
              <span className="row gap-s">{fitBadge(f.fit)}<Button small kind="primary" busy={busy === f.path} onClick={() => add(f)}>Use</Button></span>
            </div>
          ))}
          {fresh.length > 1 && <div className="mt"><Button small onClick={async () => { for (const f of fresh) await add(f); }}>Use all {fresh.length}</Button></div>}
          {fresh.some((f: any) => f.source === "Ollama") && <p className="muted small">Most Ollama models load fine in llama.cpp; a few Ollama-only formats may not — the app will say so when loading.</p>}
          {!compact && (
            <Field label="Also look in a folder" help={`Searched: ${data.scanned.map((s: any) => s.folder).join(" · ") || "—"}`}>
              <div className="row gap-s"><FolderInput value={folder} onChange={setFolder} />
                <Button disabled={!folder} onClick={async () => {
                  await api.put("/settings", { model_scan_folders: [...(boot.settings.model_scan_folders || []), folder] });
                  setFolder(""); await refreshBoot(); scan();
                }}>Add folder</Button></div>
            </Field>
          )}
        </>
      )}
    </div>
  );
}

function ImportFile({ onDone }: any) {
  const { toast } = useApp();
  const [path, setPath] = useState("");
  const go_ = async (p: string) => { try { await api.post("/models/import", { path: p }); toast("Model added", "ok"); setPath(""); onDone(); } catch (e: any) { toast(e.message, "error"); } };
  return (
    <div className="row gap-s">
      <input className="input" value={path} onChange={(e) => setPath(e.target.value)} placeholder="C:\\models\\model.gguf" />
      {desktop && <Button onClick={async () => { const p = await desktop.pickFile([{ name: "GGUF model", extensions: ["gguf"] }]); if (p) go_(p); }}>Browse…</Button>}
      <Button kind="primary" disabled={!path} onClick={() => go_(path)}>Add</Button>
    </div>
  );
}

const CATEGORIES = [
  { id: "all", label: "All" }, { id: "chat", label: "Chat & agents" }, { id: "vision", label: "Sees images" },
  { id: "coding", label: "Coding" }, { id: "small", label: "Small & fast" },
];
const SORTS = [
  { id: "fit", label: "Best for this computer" }, { id: "downloads", label: "Most downloaded" },
  { id: "trending", label: "Trending" }, { id: "recent", label: "Recently updated" },
];

function RepoFiles({ repo, onBack, hasGpu, onDownload }: any) {
  const [vision, setVision] = useState(true);
  return (
    <div className="mt">
      <div className="row between"><b>{repo.repo}</b><button className="link" onClick={onBack}>Back</button></div>
      {repo.vision && (
        <div className="row gap-s small mt"><Toggle checked={vision} onChange={setVision} />
          <span><Eye size={13} className="accent" /> Also download the vision part ({fmtBytes(repo.mmproj_file?.size)}) so this model can look at images and videos</span></div>
      )}
      {repo.files.length === 0 && <p className="muted">This repository has no GGUF model files.</p>}
      {repo.files.map((f: any) => (
        <div key={f.file} className="list-row">
          <span className="ellipsis">{f.file}{f.parts.length > 1 ? ` (${f.parts.length} parts)` : ""} {f.recommended && <Badge kind="accent" title="Best quality that runs comfortably on this computer">recommended</Badge>}</span>
          <span className="row gap-s"><span className="muted small">{f.quant} · {fmtBytes(f.size)}</span>{fitBadge(f.fit, hasGpu ? "gpu" : "cpu")}
            <Button small kind={f.recommended ? "primary" : "default"} icon={<Download size={13} />} onClick={() => onDownload({ repo: repo.repo, file: f.file, vision })}>Download</Button></span>
        </div>
      ))}
      <p className="muted small">Q4_K_M is usually the best balance; bigger files (Q5, Q6, Q8) are a little better and slower, smaller ones (Q3, IQ2) are faster and noticeably worse.</p>
    </div>
  );
}

function Browse({ hasGpu, onOpen, busy }: any) {
  const { toast } = useApp();
  const [category, setCategory] = useState("all");
  const [sort, setSort] = useState("fit");
  const [trusted, setTrusted] = useState(true);
  const [fitsOnly, setFitsOnly] = useState(true);
  const [data, setData] = useState<any>(null);
  const [shown, setShown] = useState(25);
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    setLoading(true); setShown(25);
    api.get(`/models/browse?category=${category}&sort=${sort}&trusted=${trusted}`).then(setData)
      .catch((e) => toast(`Could not reach Hugging Face: ${e.message}`, "error")).finally(() => setLoading(false));
  }, [category, sort, trusted]);
  const fits = (m: any) => m.fit.cpu !== "too_big" || (hasGpu && ["fits", "partial"].includes(m.fit.gpu));
  const rows = (data?.models || []).filter((m: any) => !fitsOnly || fits(m));
  return (
    <div className="card">
      <h3>Browse models for this computer</h3>
      <p className="muted small">Popular models on Hugging Face, with an estimate of size, whether they fit this computer's memory and how quick they feel. Click one to choose a file.</p>
      <div className="browse-filters">
        <div className="seg">{CATEGORIES.map((c) => <button key={c.id} className={`seg-btn${category === c.id ? " on" : ""}`} onClick={() => setCategory(c.id)}>{c.label}</button>)}</div>
        <select className="input narrow-auto" value={sort} onChange={(e) => setSort(e.target.value)}>{SORTS.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}</select>
        <Toggle checked={fitsOnly} onChange={setFitsOnly} label="Only what fits" />
        <Toggle checked={trusted} onChange={setTrusted} label="Well-known publishers" />
      </div>
      {loading && <div className="row gap-s muted small mt"><Spinner size={13} /> Asking Hugging Face…</div>}
      {!loading && data && (
        <div className="browse-list">
          {rows.length === 0 && <p className="muted">Nothing here fits this computer. Try another category, or turn off "Only what fits".</p>}
          {rows.slice(0, shown).map((m: any) => (
            <div key={m.repo} className={`browse-row${fits(m) ? "" : " dim"}`} onClick={() => onOpen(m.repo)}>
              <div className="stack-xs" style={{ minWidth: 0 }}>
                <div className="ellipsis"><b>{m.name}</b> <span className="muted small">by {m.publisher}</span></div>
                <div className="browse-meta">
                  <span>{m.params_b} B parameters{m.moe ? ` (${m.active_b} B active)` : ""}</span>
                  <span>· about {fmtBytes(m.est_size)} at Q4</span>
                  {m.context ? <span>· {Math.round(m.context / 1024)}K context</span> : null}
                  <span>· {m.downloads.toLocaleString()} downloads</span>
                  {m.vision && <Badge kind="accent"><Eye size={11} /> images</Badge>}
                  {m.coder && <Badge>code</Badge>}
                  {m.gated && <Badge kind="warn" title="Accept the licence on huggingface.co and add a token in Settings">needs access</Badge>}
                </div>
              </div>
              <div className="row gap-s">
                {m.installed ? <Badge kind="ok">installed</Badge> : null}
                <Badge kind={m.speed === "fast" ? "ok" : m.speed === "ok" ? "neutral" : "warn"} title="Rough reply speed on this computer">{({ fast: "fast", ok: "medium speed", slow: "slow", "too big": "too big" } as any)[m.speed] || m.speed}</Badge>
                {fitBadge(m.fit, hasGpu ? "gpu" : "cpu")}
                {busy === m.repo && <Spinner size={13} />}
              </div>
            </div>
          ))}
          {rows.length > shown && <Button small onClick={() => setShown(shown + 25)}>Show more ({rows.length - shown})</Button>}
        </div>
      )}
    </div>
  );
}

function Discover() {
  const { toast } = useApp();
  const [catalog, setCatalog] = useState<any>(null);
  const [q, setQ] = useState("");
  const [results, setResults] = useState<any[] | null>(null);
  const [repo, setRepo] = useState<any>(null);
  const [busy, setBusy] = useState("");
  useEffect(() => { api.get("/models/catalog").then(setCatalog).catch((e) => toast(e.message, "error")); }, []);
  const hasGpu = catalog?.hardware?.gpus?.some((g: any) => !g.integrated && g.vram_mb > 3000);
  const search = async () => {
    if (!q.trim()) return;
    setBusy("search");
    try { setResults((await api.get(`/models/search?q=${enc(q)}`)).results); setRepo(null); } catch (e: any) { toast(`Search failed: ${e.message}`, "error"); }
    setBusy("");
  };
  const openRepo = async (r: string) => {
    setBusy(r);
    try { setRepo(await api.get(`/models/files?repo=${enc(r)}`)); window.scrollTo({ top: 0 }); } catch (e: any) { toast(e.message, "error"); }
    setBusy("");
  };
  const dl = async (body: any) => {
    try { await api.post("/models/download", body); toast("Download started", "ok"); go("models"); } catch (e: any) { toast(e.message, "error"); }
  };
  return (
    <div className="stack">
      <DownloadsBar />
      {repo && <div className="card"><RepoFiles repo={repo} hasGpu={hasGpu} onBack={() => setRepo(null)} onDownload={dl} /></div>}
      <div className="card">
        <h3>Recommended</h3>
        {!catalog ? <Spinner /> : (
          <>
            <p className="muted small">{catalog.hardware.cpu} · {(catalog.hardware.ram_mb / 1024).toFixed(0)} GB memory{catalog.hardware.gpus.map((g: any) => ` · ${g.name}${g.vram_mb ? ` (${(g.vram_mb / 1024).toFixed(0)} GB)` : ""}`).join("")}</p>
            <div className="catalog">
              {catalog.models.map((m: any) => (
                <div key={m.key} className={`catalog-item${m.fit.cpu === "too_big" && !(hasGpu && m.fit.gpu === "fits") ? " dim" : ""}`}>
                  <div className="row between"><b>{m.name}</b>{fitBadge(m.fit, hasGpu ? "gpu" : "cpu")}</div>
                  <div className="small muted">{m.quant} · about {m.approx_gb} GB{m.tools ? " · good with tools" : ""}{m.default ? " · recommended" : ""}</div>
                  <p className="small">{m.about}</p>
                  {m.installed ? <Badge kind="ok">Installed</Badge> :
                    <Button small kind={m.default ? "primary" : "default"} icon={<Download size={13} />} onClick={() => dl({ catalog_key: m.key })}>Download</Button>}
                </div>
              ))}
            </div>
          </>
        )}
      </div>
      <Browse hasGpu={hasGpu} onOpen={openRepo} busy={busy} />
      <div className="card">
        <h3>Search by name</h3>
        <p className="muted small">If you know a model's name. Any GGUF repository on Hugging Face.</p>
        <div className="row gap-s"><input className="input" value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && search()} placeholder="e.g. qwen3.5, gemma, llama, mistral" />
          <Button icon={<Search size={14} />} busy={busy === "search"} onClick={search}>Search</Button></div>
        {results && (
          <div className="mt">
            {results.length === 0 && <p className="muted">No GGUF models found.</p>}
            {results.map((r) => (
              <div key={r.repo} className="list-row" onClick={() => openRepo(r.repo)}>
                <span className="ellipsis">{r.repo}</span>
                <span className="muted small">{busy === r.repo ? <Spinner size={12} /> : `${(r.downloads || 0).toLocaleString()} downloads`}{r.gated ? " · needs access" : ""}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function Hardware() {
  const { toast, boot, refreshBoot } = useApp();
  const [hw, setHw] = useState<any>(null);
  const [rt, setRt] = useState<any>(null);
  const [progress, setProgress] = useState<any>({});
  const load = () => { api.get("/hardware").then(setHw); api.get("/runtime").then(setRt); };
  useEffect(() => { load(); }, []);
  useEvent((e) => {
    if (e.kind === "runtime") {
      if (e.action === "install_progress") setProgress((p: any) => ({ ...p, [e.backend]: e }));
      else { setProgress((p: any) => ({ ...p, [e.backend]: null })); load(); }
    }
  });
  const setDefault = async (v: string) => { await api.put("/settings", { default_compute: v }); refreshBoot(); };
  if (!hw || !rt) return <Spinner />;
  const info: any = {
    cpu: "Works on every computer. Speed depends on the processor and memory.",
    vulkan: "Uses the graphics card through Vulkan (NVIDIA, AMD, Intel Arc). Integrated graphics are often slower than the CPU.",
    cuda: "Fastest on NVIDIA graphics cards (needs a recent NVIDIA driver). Large download (~0.5–1 GB).",
    metal: "Apple Silicon graphics.",
  };
  return (
    <div className="stack">
      <div className="card">
        <h3>This computer</h3>
        <div className="hw-grid">
          <div><Cpu size={16} /> <b>{hw.cpu}</b><div className="muted small">{hw.cores} cores / {hw.threads} threads</div></div>
          <div><MemoryStick size={16} /> <b>{(hw.ram_mb / 1024).toFixed(1)} GB memory</b><div className="muted small">{(hw.ram_free_mb / 1024).toFixed(1)} GB free now</div></div>
          <div><HardDrive size={16} /> <b>{hw.disk_free_mb ? `${(hw.disk_free_mb / 1024).toFixed(0)} GB free` : "—"}</b><div className="muted small">on the home drive</div></div>
          {hw.gpus.map((g: any) => (
            <div key={g.name}><Zap size={16} /> <b>{g.name}</b>
              <div className="muted small">{g.integrated
                ? <>integrated · uses up to {gb(g.shared_mb || hw.ram_mb / 2)} of the computer's memory{g.reserved_mb ? ` (${gb(g.reserved_mb)} reserved)` : ""}</>
                : <>{g.usable_mb || g.vram_mb ? gb(g.usable_mb || g.vram_mb) : "memory unknown"} of its own memory</>}
                {g.runtimes?.length ? ` · works with ${g.runtimes.map((r: string) => r === "cuda" ? "CUDA" : r === "vulkan" ? "Vulkan" : r).join(", ")}` : ""}</div></div>
          ))}
        </div>
        <p className="small">Suggested: <b>{hw.recommended_backend === "cpu" ? "CPU" : hw.recommended_backend.toUpperCase()}</b>{hw.recommended_backend === "cpu" && hw.gpus.some((g: any) => g.integrated) ? " — integrated graphics share the computer's memory and are often no faster than the CPU. To check, set a model to GPU and use “Find the fastest setup” on its card." : ""}</p>
      </div>
      <div className="card">
        <h3>Processing</h3>
        <Field label="Default for models set to Auto" help="Each model can override this on its card. Auto uses the GPU only when a suitable graphics card and GPU runtime are present.">
          <div className="seg">
            {["auto", "cpu", "gpu"].map((c) => <button key={c} className={`seg-btn${(boot.settings.default_compute || "auto") === c ? " on" : ""}`} onClick={() => setDefault(c)}>{c === "auto" ? "Auto" : c.toUpperCase()}</button>)}
          </div>
        </Field>
      </div>
      <div className="card">
        <h3>Runtimes (llama.cpp {rt.tag})</h3>
        {["cpu", ...rt.available.filter((b: string) => b !== "cpu")].map((b: string) => {
          const installed = rt.installed[b];
          const p = progress[b] || rt.installing[b];
          return (
            <div key={b} className="runtime-row">
              <div><b>{rt.labels[b] || b}</b> {installed && <Badge kind="ok">installed</Badge>}
                <div className="muted small">{info[b]}</div>
                {rt.devices[b]?.length > 0 && <div className="small">Devices: {rt.devices[b].map((d: any) => d.error ? d.error : `${d.name} (${(d.total_mb / 1024).toFixed(1)} GB${d.integrated ? ", shared with the computer" : ""})`).join(", ")}</div>}
                {installed && b !== "cpu" && b !== "custom" && b !== "metal" && !(rt.devices[b] || []).some((d: any) => !d.error) && (
                  <div className="small warn-c">{b === "cuda" ? "No NVIDIA card was found on this computer, so this runtime is never used here. You can remove it." : "This runtime found no graphics card it can use."}</div>
                )}
                {p && <div className="row gap-s"><Progress value={p.done || 0} total={p.total || 1} /><span className="small muted">{p.state || "downloading"}</span></div>}
              </div>
              <div>
                {!installed && <Button small icon={<Download size={13} />} busy={!!p} onClick={async () => { try { await api.post("/runtime/install", { backend: b }); } catch (e: any) { toast(e.message, "error"); } }}>Install</Button>}
                {installed && b !== "cpu" && b !== "custom" && <ConfirmButton small onConfirm={async () => { try { await api.del(`/runtime/${b}`); load(); } catch (e: any) { toast(e.message, "error"); } }}>Remove</ConfirmButton>}
              </div>
            </div>
          );
        })}
        {rt.installed.custom && <div className="runtime-row"><div><b>Custom llama-server</b><div className="muted small">{rt.installed.custom}</div></div></div>}
      </div>
    </div>
  );
}

export default function Models({ tab }: { tab?: string }) {
  const t = tab || "installed";
  return (
    <div className="page">
      <PageHeader title="Models" subtitle="Local models run on this computer and cost nothing; online models (GPT, Claude, Gemini and more) join them for hard reasoning. Pick a model, or Auto, per chat, project or scheduled task." />
      <Tabs value={t} onChange={(v: string) => go("models", v)} tabs={[{ id: "installed", label: "On this computer" }, { id: "online", label: "Online" }, { id: "discover", label: "Discover" }, { id: "hardware", label: "Hardware & GPU" }]} />
      {t === "installed" && <Installed />}
      {t === "online" && <Online />}
      {t === "discover" && <Discover />}
      {t === "hardware" && <Hardware />}
    </div>
  );
}
