import React, { useEffect, useState } from "react";
import { ClipboardCheck, Cpu, FolderOpen, Gauge, Globe, Info, Monitor, Search, ShieldCheck, Sparkles, Users, X, Zap } from "lucide-react";
import { api, desktop } from "../api";
import { go, useApp, useEvent, useShown } from "../store";
import { Badge, Button, ConfirmButton, Field, FolderInput, PageHeader, Toggle } from "../components/ui";
import { SplitPane } from "../components/Resizable";

// The built-in evaluation: twelve small tasks with answers that code checks. It turns "is this setup good?" into
// numbers that can be compared between models, settings and versions.
function EvaluationCard() {
  const { toast, boot } = useApp();
  const [data, setData] = useState<any>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [model, setModel] = useState("");
  const [picked, setPicked] = useState<string[]>([]);       // up to two runs to compare
  const pick = (id: string) => setPicked((p) => p.includes(id) ? p.filter((x) => x !== id) : [...p.slice(-1), id]);
  const save = (name: string, text: string, type: string) => {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([text], { type }));
    a.download = name;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  };
  const exportRuns = () => {
    const q = (v: any) => `"${String(v ?? "").replace(/"/g, '""')}"`;
    const lines = ["run,when,version,model,task,passed,seconds,read,cached,written,agent_tokens,model_calls,tools,planned,error"];
    for (const r of data.runs) for (const t of r.results || [])
      lines.push([r.id, r.created, r.version, r.model, t.id, t.passed ? 1 : 0, t.seconds ?? "", t.read ?? "", t.cached ?? "", t.written ?? "", t.agent_tokens ?? "", t.calls ?? "", t.tools ?? "", t.planned ? 1 : 0, t.error || ""].map(q).join(","));
    save("kimilm-evaluation.csv", lines.join("\n") + "\n", "text/csv");
  };
  const load = () => api.get("/evals").then(setData).catch(() => {});
  const shown = useShown();          // afresh when you come back to Settings (2.21.0)
  useEffect(() => { if (shown) load(); }, [shown]);
  useEffect(() => {
    if (!data?.current || !shown) return;
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [!!data?.current, shown]);
  const dur = (sec: number) => sec < 90 ? `${Math.round(sec)} s` : sec < 5400 ? `${Math.round(sec / 60)} min` : `${(sec / 3600).toFixed(1)} h`;
  const k = (n: number) => n >= 10000 ? `${Math.round(n / 1000)}k` : n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n || 0);
  const act = async (path: string, method: "post" | "del" = "post") => { try { await (api as any)[method](path); load(); } catch (e: any) { toast(e.message, "error"); } };
  const run = async () => { try { await api.post("/evals/run", model ? { model } : {}); load(); } catch (e: any) { toast(e.message, "error"); } };
  const two = picked.map((id) => data?.runs?.find((r: any) => r.id === id)).filter(Boolean);
  const [a, b] = two.length === 2 ? [...two].sort((x: any, y: any) => String(x.created).localeCompare(String(y.created))) : [null, null];
  const changed = a && b ? Object.keys({ ...(a.settings || {}), ...(b.settings || {}) }).filter((key) => JSON.stringify(a.settings?.[key]) !== JSON.stringify(b.settings?.[key])) : [];
  return (
    <div className="card stack-s">
      <h3>Check this setup</h3>
      <p className="muted small">Runs {data?.tasks?.length || 12} small tasks (reading and computing, writing and editing files, fixing code, finding things, summarising, one planned run) with your default model and checks each answer in code. No internet, nothing personal, approvals off inside the test folder only. Use it to compare models, settings and versions on this computer. On a slow laptop it can take an hour or more.</p>
      <div className="row gap-m wrap">
        {data?.current
          ? <><span className="small"><b>{data.current.paused ? "Paused" : "Running"}:</b> task {data.current.done + 1} of {data.current.total} · {data.current.task}</span>
              {data.current.paused
                ? <Button small kind="primary" onClick={() => act("/evals/resume")} title="Go on with the task where it paused">Resume</Button>
                : <Button small onClick={() => act("/evals/pause")} title="The task stops at its next step and gives its model back; time paused does not count">Pause</Button>}
              <Button small kind="ghost" onClick={() => act("/evals/stop")} title="End it now. Finished tasks are kept and it can be continued later">Stop</Button></>
          : <>
              <select className="input" style={{ maxWidth: 280 }} value={model} onChange={(e) => setModel(e.target.value)} title="The model to test">
                <option value="">Default model</option>
                {(boot?.models || []).map((m: any) => <option key={m.id} value={m.id}>{m.name}</option>)}
              </select>
              <Button small onClick={run}>Run the evaluation</Button>
              {data?.runs?.length > 0 && <Button small kind="ghost" onClick={exportRuns}>Export all results (CSV)</Button>}
            </>}
      </div>
      {data?.runs?.length > 1 && <div className="small muted">Tick two runs to compare them task by task.</div>}
      {data?.runs?.length > 0 && <div className="eval-runs">
        <div className="eval-row eval-head small muted"><span>When</span><span>Version</span><span>Model</span><span>Passed</span><span>Time</span><span>Read</span><span>From cache</span><span>Written</span></div>
        {data.runs.map((r: any) => (
          <div key={r.id}>
            <div className="eval-row small" onClick={() => setOpen(open === r.id ? null : r.id)} title="Show each task">
              <span><input type="checkbox" checked={picked.includes(r.id)} onClick={(e) => e.stopPropagation()} onChange={() => pick(r.id)} title="Compare" />{new Date(r.created).toLocaleString()}</span><span>{r.version}</span><span className="ellipsis">{r.model || "default"}</span>
              <span><b>{r.summary?.passed ?? 0} of {r.summary?.total ?? "?"}</b>{r.status !== "done" ? ` (${r.status === "interrupted" ? "cut off" : r.status})` : ""}{r.can_continue ? " · open to continue" : ""}</span>
              <span>{dur(r.summary?.seconds || 0)}</span><span>{k(r.summary?.read)}</span><span>{r.summary?.cache_percent ?? 0}%</span><span>{k(r.summary?.written)}</span>
            </div>
            {open === r.id && <div className="row gap-s eval-actions">
              {r.can_continue && <Button small kind="primary" onClick={() => act(`/evals/${r.id}/continue`)} title="Run the tasks that were not done, with the same model; finished tasks are kept">Continue ({(r.summary?.total ?? 0) - (r.results?.length ?? 0)} left)</Button>}
              {!(data.current && data.current.id === r.id) && <ConfirmButton small onConfirm={() => act(`/evals/${r.id}`, "del")} title="Take this evaluation off the list, with its task chats">Delete</ConfirmButton>}
            </div>}
            {open === r.id && <div className="eval-tasks">
              {(r.results || []).map((t: any) => (
                <div key={t.id} className="eval-task small" onClick={() => t.chat_id && go("chat", t.chat_id)} title="Open the chat of this task">
                  <span className={t.passed ? "ok" : "bad"}>{t.passed ? "passed" : "failed"}</span>
                  <span>{t.title}{t.planned ? " (planned run)" : ""}</span>
                  <span className="muted">{dur(t.seconds || 0)} · {t.tools || 0} tool{t.tools === 1 ? "" : "s"}{!t.passed && t.expected ? ` · expected ${t.expected}` : ""}{t.error ? ` · ${String(t.error).slice(0, 80)}` : ""}</span>
                </div>
              ))}
            </div>}
          </div>
        ))}
      </div>}
      {a && b && <div className="eval-compare">
        <div className="eval-cmp-row eval-head small muted"><span>Task</span><span>{a.version} · {a.model || "default"}</span><span>{b.version} · {b.model || "default"}</span><span>Change</span></div>
        {(data.tasks || []).map((t: any) => {
          const x = (a.results || []).find((r: any) => r.id === t.id), y = (b.results || []).find((r: any) => r.id === t.id);
          if (!x && !y) return null;
          const cell = (r: any) => r ? <span><span className={r.passed ? "ok" : "bad"}>{r.passed ? "passed" : "failed"}</span> <span className="muted">{dur(r.seconds || 0)}</span></span> : <span className="muted">not run</span>;
          const delta = x && y ? (x.passed !== y.passed ? (y.passed ? "now passes" : "now fails") : x.seconds > 0 && Math.round(100 * (y.seconds - x.seconds) / x.seconds) !== 0 ? `${y.seconds >= x.seconds ? "+" : "−"}${Math.abs(Math.round(100 * (y.seconds - x.seconds) / x.seconds))}% time` : "same") : "";
          return <div key={t.id} className="eval-cmp-row small"><span>{t.title}</span>{cell(x)}{cell(y)}<span className={delta === "now passes" ? "ok" : delta === "now fails" ? "bad" : "muted"}>{delta}</span></div>;
        })}
        <div className="eval-cmp-row small"><b>Total</b><span><b>{a.summary?.passed} of {a.summary?.total}</b> · {dur(a.summary?.seconds || 0)} · {a.summary?.cache_percent}% from cache</span>
          <span><b>{b.summary?.passed} of {b.summary?.total}</b> · {dur(b.summary?.seconds || 0)} · {b.summary?.cache_percent}% from cache</span><span /></div>
        <div className="small muted" style={{ padding: "8px 12px" }}>{changed.length ? <>Settings that differ: {changed.map((key) => `${key} ${JSON.stringify(a.settings?.[key])} → ${JSON.stringify(b.settings?.[key])}`).join("; ")}</> : "Both runs used the same settings."}</div>
      </div>}
    </div>
  );
}

// 2.21.0: the size of everything in the window, like a browser's zoom (text, spacing and layout together)
const SIZES = [0.67, 0.75, 0.8, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2];

function SizeCard() {
  const [zoom, setZoom] = useState<number | null>(null);
  useEffect(() => {
    if (!desktop?.getZoom) return;
    desktop.getZoom().then(setZoom);
    return desktop.onZoom((z: number) => setZoom(z));
  }, []);
  const pct = (z: number) => `${Math.round(z * 100)} %`;
  return (
    <div className="card stack-s">
      <h3>Size of text and everything else</h3>
      {desktop?.getZoom ? <>
        <div className="row gap-s">
          <Button small onClick={() => desktop.stepZoom(-1)} disabled={zoom !== null && zoom <= SIZES[0]} title="Ctrl −">Smaller</Button>
          <select className="input narrow" value={zoom ?? 1} onChange={(e) => desktop.setZoom(Number(e.target.value))}>
            {[...new Set([...SIZES, zoom ?? 1])].sort((a, b) => a - b).map((z) => <option key={z} value={z}>{pct(z)}</option>)}
          </select>
          <Button small onClick={() => desktop.stepZoom(1)} disabled={zoom !== null && zoom >= SIZES[SIZES.length - 1]} title="Ctrl +">Larger</Button>
          {zoom !== null && zoom !== 1 && <Button small kind="ghost" onClick={() => desktop.setZoom(1)} title="Ctrl 0">Back to 100 %</Button>}
        </div>
        <div className="field-help">Like a browser's zoom: text, spacing and layout grow or shrink together, so nothing breaks. Also with Ctrl + and Ctrl − (Ctrl 0 for 100 %), or Ctrl and the mouse wheel. Kept for the next start.</div>
      </> : <div className="field-help">In a browser, use the browser's own zoom (Ctrl + and Ctrl −).</div>}
    </div>
  );
}

const KW = {
 "general": "notifications notify sound bell done finished needs me device plugged in size text zoom larger smaller window panels sidebar width resize reset start sign in tray background keep awake running in the background start kimilm when i sign in to windows (in the tray) — needed for schedules closing the window keeps the app running in the tray keep the computer awake while a task is running (the screen may still turn off; closing a laptop's lid still follows windows' own setting)",
 "models": "models models folder save default context (tokens) longest reply (tokens) free memory after (minutes idle) models loaded at once more lets agents use different models in parallel; each needs memory. time limit per reply (minutes) agents stop and report what they found.",
 "less": "doing less work a model on this computer spends most of its time reading. these settings make it read less: what it has read once is kept, and what it is given to read follows how fast this computer reads. keep what a model has read of its instructions and tools on disk, so a freshly loaded model does not read them again snapshots are used when a model runs with one reply at a time. planned runs always do; for ordinary chats set the model's simultaneous replies to 1 (models), which also saves memory. delete snapshots disk space for snapshots (mb) the oldest are deleted first. memory for prompts already read (mb) reading time per tool result (seconds) how much of a web page, file or command output a model is given follows its measured reading speed: about this many seconds of reading. 0 = always 6,000 characters.",
 "teams": "how agents work think before answering thinking scheduled long-running tasks approve their own actions auto-approve install programs agents install teams of agents a planner (your strongest model) turns a multi-step request into a short sequence of steps and creates an agent for each. a step starts only when the results it needs are accepted. every result is checked; a step that does not pass goes round again with what is missing. before a step starts, its tokens, context and time are forecast, and each model is loaded with only the context the run needs and unloaded when no remaining step uses it. use a team automatically, when a request looks multi-step for every agent or planner request only when i ask (team harness or team builder) order of steps one after another (recommended) side by side when steps are independent load models for a run with the context its steps need (forecast), not each model's full context setting check every step's result against its 'done when' before the next step starts ask me before a plan starts (sche",
 "web": "web and downloads when search engines return nothing useful, search again in a real browser window (edge) cookie banners on websites an agent opens answered without asking, as a person would in passing. agreeing to terms, signing in, sending a file and paying are never done this way. choose what allows least (“reject all”, “only necessary”), else accept accept them leave them to the agent (a click then asks you) hugging face token (optional) only for models that require you to accept a licence on huggingface.co. stored in windows credential manager. save",
 "safety": "programs agents installed remove toolbox flatpak winget safety let the agent run code: python and commands such as tests and git (always asks first unless auto-approve is on) longest a program an agent runs may take (seconds) an agent may ask for less or more time for its code; never more than this. a program that runs longer is ended with every program it started. programs agents may run at once more wait their turn. code runs at low priority, so the model and this window stay responsive. a failure should reach the planner's decision within (seconds) agents of a plan or a long-running task stop as soon as they cannot finish, and the planner decides by rule where the answer is clear. a run's card shows how fast that was against this target (time spent by a model deciding is not counted). if nobody answers an approval, decline after (minutes) most tools offered directly to the model above this, connector tools are offered through a search tool",
 "about": "about open data folder open logs version data folder logs",
 "explore": "idle measure fastest setup suggestions evaluate better ways",
 "check": "evaluation check setup tasks compare models export csv pause continue delete",
 "telemetry": "performance telemetry readings usage statistics tokens clear graphics cpu gpu kimios"
};

// 2.27.0: Settings in sections. A list of sections on the left (drag its edge to widen it), one section at a time
// on the right, and a search that shows every setting whose words match, whatever its section.
const SECTIONS: { id: string; label: string; icon: any; words: string }[] = [
  { id: "general", label: "Window and start", icon: Monitor, words: KW.general },
  { id: "models", label: "Models", icon: Cpu, words: KW.models },
  { id: "speed", label: "Speed and memory", icon: Zap, words: KW.less },
  { id: "teams", label: "Teams and long tasks", icon: Users, words: KW.teams },
  { id: "web", label: "Web and downloads", icon: Globe, words: KW.web },
  { id: "safety", label: "Safety", icon: ShieldCheck, words: KW.safety },
  { id: "explore", label: "Finding better ways", icon: Sparkles, words: KW.explore },
  { id: "check", label: "Check this setup", icon: ClipboardCheck, words: KW.check },
  { id: "telemetry", label: "Performance and usage", icon: Gauge, words: KW.telemetry },
  { id: "about", label: "About", icon: Info, words: KW.about },
];

// 2.29.0: programs agents installed for their work, and how to undo each
function ProgramsCard() {
  const { toast } = useApp();
  const [data, setData] = useState<any>(null);
  const load = () => api.get("/programs").then(setData).catch(() => {});
  useEffect(() => { load(); }, []);
  useEvent((e) => { if (e.kind === "programs_changed") load(); });
  if (!data) return null;
  const pc = data.computer || {};
  const way = pc.os === "windows" ? "winget" : pc.os === "macos" ? "Homebrew" : pc.immutable ? "a toolbox of KimiLM's own (no password)"
    : (pc.managers || [])[0] ? `${pc.managers[0]} when it needs no password` : "Flatpak and KimiLM's own folder";
  return (
    <div className="card stack-s">
      <h3>Programs agents installed</h3>
      <p className="muted small">Command-line programs go in with {way}; apps with {pc.os === "linux" ? "Flatpak (Flathub, for you only)" : pc.os === "windows" ? "winget" : "Homebrew"}; Python and Node packages into KimiLM's own folder.</p>
      {data.installed.length === 0 ? <div className="muted small">None yet.</div> :
        data.installed.map((r: any) => (
          <div key={r.name + r.kind} className="row between gap-m">
            <div className="grow"><b>{r.name}</b> <span className="muted small">{r.kind} · {r.how} · {r.when}{r.by ? ` · for ${r.by}` : ""}</span></div>
            {r.kind !== "browser" && <ConfirmButton small kind="ghost" onConfirm={async () => { try { await api.del(`/programs/${encodeURIComponent(r.name)}`); load(); } catch (e: any) { toast(e.message, "error"); } }}>Remove</ConfirmButton>}
          </div>))}
    </div>
  );
}

export default function Settings({ section }: { section?: string }) {
  const { boot, refreshBoot, toast } = useApp();
  const s = boot.settings;
  const [shell, setShell] = useState<any>(null);
  const [speed, setSpeed] = useState<any>(null);
  useEffect(() => { api.get("/runtime").then(setSpeed).catch(() => {}); }, []);
  const [modelsDir, setModelsDir] = useState(s.models_dir || "");
  const [hf, setHf] = useState("");
  const [q, setQ] = useState("");
  useEffect(() => { desktop?.getShellPrefs().then(setShell); }, []);
  const set = async (values: any) => { try { await api.put("/settings", values); refreshBoot(); } catch (e: any) { toast(e.message, "error"); } };
  const setShellPref = async (p: any) => setShell(await desktop.setShellPrefs(p));
  const last = (() => { try { return localStorage.getItem("kimilm.settings.section") || "general"; } catch { return "general"; } })();
  const current = SECTIONS.some((x) => x.id === section) ? section! : SECTIONS.some((x) => x.id === last) ? last : "general";
  useEffect(() => { try { localStorage.setItem("kimilm.settings.section", current); } catch {} }, [current]);
  const render: Record<string, () => React.ReactNode> = {
    general: () => <>
      <SizeCard />
      <LayoutCard />
      <div className="card stack-s">
        <h3>Notifications</h3>
        <p className="muted small">You are told on screen (and on the desktop when KimiLM is not in front) when something needs you, when long work is done and when a device is plugged in. Everything is kept under the bell next to KimiLM's name.</p>
        <Field label="Tell me a chat is done when it took longer than (seconds)" help="0: only scheduled and long-running tasks, approvals and questions. Not shown when you are looking at that chat.">
          <input className="input narrow" type="number" min={0} step={5} value={s.notify_done_seconds ?? 20} onChange={(e) => set({ notify_done_seconds: Number(e.target.value) })} />
        </Field>
        <Toggle checked={s.notify_sound ?? true} onChange={(v: boolean) => set({ notify_sound: v })} label="A short sound when something needs me" />
        <Toggle checked={s.devices_watch ?? true} onChange={(v: boolean) => set({ devices_watch: v })} label="Tell me when a device is plugged in or taken out (Devices)" />
      </div>
        {shell && (
          <div className="card stack-s">
            <h3>Running in the background</h3>
            <Toggle checked={shell.openAtLogin} onChange={(v: boolean) => setShellPref({ openAtLogin: v })} label="Start KimiLM when I sign in to Windows (in the tray) — needed for schedules" />
            <Toggle checked={shell.keepInTray} onChange={(v: boolean) => setShellPref({ keepInTray: v })} label="Closing the window keeps the app running in the tray" />
            <Toggle checked={shell.keepAwake !== false} onChange={(v: boolean) => setShellPref({ keepAwake: v })} label="Keep the computer awake while a task is running (the screen may still turn off; closing a laptop's lid still follows Windows' own setting)" />
          </div>
        )}
      {!desktop && <div className="card small muted">Starting with the computer and the tray are set in the desktop app.</div>}
    </>,
    models: () => <>
        <div className="card stack-s">
          <h3>Models</h3>
          <Field label="Models folder" help={`Where downloads go (models are large: pick a drive with space, e.g. D:\\models). Currently: ${boot.models_dir}. Existing models stay where they are.`}>
            <div className="row gap-s"><FolderInput value={modelsDir} onChange={setModelsDir} /><Button onClick={() => set({ models_dir: modelsDir })}>Save</Button></div>
          </Field>
          <div className="row gap-m wrap">
            <Field label="Default context (tokens)">
              <select className="input" value={s.default_ctx} onChange={(e) => set({ default_ctx: Number(e.target.value) })}>
                {[4096, 8192, 16384, 32768].map((c) => <option key={c} value={c}>{c.toLocaleString()}</option>)}
              </select>
            </Field>
            <Field label="Longest reply (tokens)">
              <select className="input" value={s.max_reply_tokens} onChange={(e) => set({ max_reply_tokens: Number(e.target.value) })}>
                {[1024, 2048, 4096, 8192].map((c) => <option key={c} value={c}>{c.toLocaleString()}</option>)}
              </select>
            </Field>
            <Field label="Free memory after (minutes idle)">
              <input className="input narrow" type="number" min={0} value={s.unload_after_minutes} onChange={(e) => set({ unload_after_minutes: Number(e.target.value) })} />
            </Field>
            <Field label="Models loaded at once" help="More lets agents use different models in parallel; each needs memory.">
              <input className="input narrow" type="number" min={1} max={6} value={s.max_loaded_models ?? 3} onChange={(e) => set({ max_loaded_models: Number(e.target.value) })} />
            </Field>
            <Field label="Time limit per reply (minutes)" help="Agents stop and report what they found.">
              <input className="input narrow" type="number" min={1} value={s.turn_time_limit_minutes ?? 30} onChange={(e) => set({ turn_time_limit_minutes: Number(e.target.value) })} />
            </Field>
          </div>
        </div>
    </>,
    speed: () => <>
        <div className="card stack-s">
          <h3>Doing less work</h3>
          <p className="muted small">A model on this computer spends most of its time reading. These settings make it read less: what it has read once is kept, and what it is given to read follows how fast this computer reads.</p>
          <Toggle checked={s.prompt_snapshots ?? true} onChange={(v: boolean) => set({ prompt_snapshots: v })} label="Keep what a model has read of its instructions and tools on disk, so a freshly loaded model does not read them again" />
          <p className="muted small">Snapshots are used when a model runs with one reply at a time. Planned runs always do; for ordinary chats set the model's Simultaneous replies to 1 (Models), which also saves memory.</p>
          {speed?.snapshots && <div className="row gap-m wrap small muted">
            <span>{speed.snapshots.files} snapshot{speed.snapshots.files === 1 ? "" : "s"} · {speed.snapshots.size_mb} MB on disk
              {speed.snapshots.restored ? ` · used ${speed.snapshots.restored} time${speed.snapshots.restored === 1 ? "" : "s"} since the app started (${speed.snapshots.tokens_restored.toLocaleString()} tokens not read again)` : ""}</span>
            <Button small disabled={!speed.snapshots.files} onClick={async () => { try { await api.post("/runtime/snapshots/clear"); setSpeed(await api.get("/runtime")); } catch (e: any) { toast(e.message, "error"); } }}>Delete snapshots</Button>
          </div>}
          <div className="row gap-m wrap">
            <Field label="Disk space for snapshots (MB)" help="The oldest are deleted first.">
              <input className="input narrow" type="number" min={0} step={512} value={s.snapshot_disk_mb ?? 4096} onChange={(e) => set({ snapshot_disk_mb: Number(e.target.value) })} />
            </Field>
            <Field label="Memory for prompts already read (MB)" help={`Per loaded model, so the planner's and the agents' prompts all stay ready. Empty = automatic${speed?.cache_ram_mb ? ` (${speed.cache_ram_mb} MB on this computer)` : ""}. Applies the next time a model is loaded.`}>
              <input className="input narrow" type="number" min={0} step={128} placeholder="auto" value={s.prompt_cache_mb ?? ""} onChange={(e) => set({ prompt_cache_mb: e.target.value === "" ? null : Number(e.target.value) })} />
            </Field>
            <Field label="Reading time per tool result (seconds)" help="How much of a web page, file or command output a model is given follows its measured reading speed: about this many seconds of reading. 0 = always 6,000 characters.">
              <input className="input narrow" type="number" min={0} step={10} value={s.reading_budget_seconds ?? 60} onChange={(e) => set({ reading_budget_seconds: Number(e.target.value) })} />
            </Field>
          </div>
        </div>
    </>,
    teams: () => <>
        <div className="card stack-s">
          <h3>How agents work</h3>
          <Toggle checked={s.thinking_default ?? true} onChange={(v: boolean) => set({ thinking_default: v })} label="Think before answering, in new chats and in every agent a planner starts (planners are told so)" />
          <Toggle checked={s.unattended_auto_approve ?? true} onChange={(v: boolean) => set({ unattended_auto_approve: v })} label="Scheduled and long-running tasks approve their own actions (paying, sending your files to a website and sending private material online still ask)" />
          <Toggle checked={s.install_programs ?? true} onChange={(v: boolean) => set({ install_programs: v })} label="Agents install programs they need (browsers, command-line tools, Python and Node packages, apps) instead of stopping" />
          <div className="muted small">Switching Auto-approve on in a chat also answers what its agents are waiting for, at once.</div>
        </div>
        <div className="card stack-s">
          <h3>Teams of agents</h3>
          <p className="muted small">A planner (your strongest model) turns a multi-step request into a short sequence of steps and creates an agent for each. A step starts only when the results it needs are accepted. Every result is checked; a step that does not pass goes round again with what is missing. Before a step starts, its tokens, context and time are forecast, and each model is loaded with only the context the run needs and unloaded when no remaining step uses it.</p>
          <Field label="Use a team" help={s.team_mode === "always" ? "Every request in an Agent or Planner chat goes to the planner first." : s.team_mode === "off" ? "Only with a team harness or the team builder in the composer." : "The planner looks at requests that seem multi-step and decides whether one agent is enough."}>
            <select className="input" value={s.team_mode || "auto"} onChange={(e) => set({ team_mode: e.target.value })}>
              <option value="auto">Automatically, when a request looks multi-step</option>
              <option value="always">For every Agent or Planner request</option>
              <option value="off">Only when I ask (team harness or team builder)</option>
            </select>
          </Field>
          <Field label="Order of steps" help={(s.agent_order || "sequence") === "parallel" ? "Steps that do not need each other run together, as far as each model's Simultaneous replies allow. Uses more memory; worth it with online models or a dedicated graphics card." : "One step at a time, in plan order: the least memory, and no agent works before its input exists. Best on a laptop."}>
            <select className="input" value={s.agent_order || "sequence"} onChange={(e) => set({ agent_order: e.target.value })}>
              <option value="sequence">One after another (recommended)</option>
              <option value="parallel">Side by side when steps are independent</option>
            </select>
          </Field>
          <Toggle checked={s.fit_context ?? true} onChange={(v: boolean) => set({ fit_context: v })} label="Load models for a run with the context its steps need (forecast), not each model's full context setting" />
          <Toggle checked={s.verify_results ?? true} onChange={(v: boolean) => set({ verify_results: v })} label="Check every step's result against its 'done when' before the next step starts" />
          <Toggle checked={s.plan_approval ?? false} onChange={(v: boolean) => set({ plan_approval: v })} label="Ask me before a plan starts (scheduled tasks and auto-approved chats start right away)" />
          <Toggle checked={s.unload_after_run ?? false} onChange={(v: boolean) => set({ unload_after_run: v })} label="Unload the planner's model as well when a run ends (scheduled runs always do)" />
          <Toggle checked={(s.resume_runs || "ask") === "auto"} onChange={(v: boolean) => set({ resume_runs: v ? "auto" : "ask" })} label="Continue runs that were cut off (app closed, restart, crash) by themselves when the app starts; otherwise the chat offers a Continue button" />
          <Toggle checked={s.mission_auto_resume ?? true} onChange={(v: boolean) => set({ mission_auto_resume: v })} label="Continue long-running tasks by themselves after a restart or a crash (a task that cuts the app off three times in a row is left alone)" />
          <Field label="Default limits of a new long-running task" help="Reaching a limit pauses the task between two steps and tells you. Each task's own limits can be changed when it is set up and while it runs (Long-running tasks → Limits).">
            <div className="row gap-m wrap">
              <label className="row gap-s small">hours of work <input className="input narrow" type="number" min={0.5} step={0.5} value={s.mission_hours ?? 8} onChange={(e) => set({ mission_hours: Number(e.target.value) })} /></label>
              <label className="row gap-s small">steps <input className="input narrow" type="number" min={1} value={s.mission_steps ?? 40} onChange={(e) => set({ mission_steps: Number(e.target.value) })} /></label>
              <label className="row gap-s small">minutes a step <input className="input narrow" type="number" min={5} value={s.mission_step_minutes ?? 45} onChange={(e) => set({ mission_step_minutes: Number(e.target.value) })} /></label>
            </div>
          </Field>
          <Toggle checked={s.lean_tools ?? true} onChange={(v: boolean) => set({ lean_tools: v })} label="Give each step only the tools its goal calls for (all of them on a fresh attempt)" />
          <Toggle checked={s.learned_model_choice ?? true} onChange={(v: boolean) => set({ learned_model_choice: v })} label="Choose each step's model from how such steps went on this computer (Activity → Models' record); models you fix yourself are never changed" />
          <Toggle checked={s.escalate_models ?? true} onChange={(v: boolean) => set({ escalate_models: v })} label="Move a step to a stronger model when it fails the same way twice" />
          <Field label="Who checks results" help="A smaller model checks in a fraction of the time, but may judge less well. Run the evaluation below with each choice and compare.">
            <select className="input" value={s.check_model || ""} onChange={(e) => set({ check_model: e.target.value })}>
              <option value="">The planner's model (recommended)</option>
              <option value="smallest">The smallest installed model</option>
            </select>
          </Field>
          <div className="row gap-m wrap">
            <Field label="Rounds per step" help="Tries before the best attempt is kept. Steps the planner marks as loops get two more.">
              <input className="input narrow" type="number" min={1} max={8} value={s.step_rounds ?? 3} onChange={(e) => set({ step_rounds: Number(e.target.value) })} />
            </Field>
            <Field label="Restart a silent model after (seconds)" help="No new token for this long means the model is stuck: it is restarted and the call is made again.">
              <input className="input narrow" type="number" min={30} value={s.stall_timeout_seconds ?? 180} onChange={(e) => set({ stall_timeout_seconds: Number(e.target.value) })} />
            </Field>
            <Field label="Writing time per result (seconds)" help="Steps are asked for a result this long at the computer's writing speed; longer material goes to a file. 0 = no hint.">
              <input className="input narrow" type="number" min={0} step={30} value={s.writing_budget_seconds ?? 180} onChange={(e) => set({ writing_budget_seconds: Number(e.target.value) })} />
            </Field>
            <Field label="Agents at once" help="Only when steps run side by side.">
              <input className="input narrow" type="number" min={1} max={8} value={s.max_parallel_agents ?? 4} onChange={(e) => set({ max_parallel_agents: Number(e.target.value) })} />
            </Field>
            <Field label="Most tasks in a plan">
              <input className="input narrow" type="number" min={2} max={16} value={s.max_team_tasks ?? 8} onChange={(e) => set({ max_team_tasks: Number(e.target.value) })} />
            </Field>
            <Field label="Team time limit (minutes)" help="The planner then writes up what is done.">
              <input className="input narrow" type="number" min={5} value={s.team_time_limit_minutes ?? 120} onChange={(e) => set({ team_time_limit_minutes: Number(e.target.value) })} />
            </Field>
          </div>
        </div>
    </>,
    web: () => <>
        <div className="card stack-s">
          <h3>Web and downloads</h3>
          <Toggle checked={s.browser_search ?? true} onChange={(v: boolean) => set({ browser_search: v })} label="When search engines return nothing useful, search again in a real browser window (Edge)" />
          <Field label="Cookie banners on websites an agent opens" help="Answered without asking, as a person would in passing. Agreeing to terms, signing in, sending a file and paying are never done this way.">
            <select className="input" style={{ maxWidth: 560 }} value={s.browser_consent ?? "necessary"} onChange={(e) => set({ browser_consent: e.target.value })}>
              <option value="necessary">Choose what allows least (“Reject all”, “Only necessary”), else accept</option>
              <option value="accept">Accept them</option>
              <option value="ask">Leave them to the agent (a click then asks you)</option>
            </select>
          </Field>
          <Field label="Hugging Face token (optional)" help="Only for models that require you to accept a licence on huggingface.co. Stored in Windows Credential Manager.">
            <div className="row gap-s"><input className="input" type="password" value={hf} placeholder={boot.has_hf_token ? "saved ••••" : "hf_…"} onChange={(e) => setHf(e.target.value)} />
              <Button onClick={async () => { await set({ hf_token: hf }); setHf(""); toast("Saved", "ok"); }}>Save</Button></div>
          </Field>
        </div>
    </>,
    safety: () => <>
        <ProgramsCard />
        <div className="card stack-s">
          <h3>Safety</h3>
          <Toggle checked={s.allow_python} onChange={(v: boolean) => set({ allow_python: v })} label="Let the agent run code: Python and commands such as tests and git (always asks first unless auto-approve is on)" />
          <Field label="Longest a program an agent runs may take (seconds)" help="An agent may ask for less or more time for its code; never more than this. A program that runs longer is ended with every program it started.">
            <input className="input narrow" type="number" min={10} max={7200} value={s.code_timeout_max_seconds ?? 900} onChange={(e) => set({ code_timeout_max_seconds: Number(e.target.value) })} />
          </Field>
          <Field label="Programs agents may run at once" help="More wait their turn. Code runs at low priority, so the model and this window stay responsive.">
            <input className="input narrow" type="number" min={1} max={8} value={s.code_runs_at_once ?? 2} onChange={(e) => set({ code_runs_at_once: Number(e.target.value) })} />
          </Field>
          <Field label="A failure should reach the planner's decision within (seconds)" help="Agents of a plan or a long-running task stop as soon as they cannot finish, and the planner decides by rule where the answer is clear. A run's card shows how fast that was against this target (time spent by a model deciding is not counted).">
            <input className="input narrow" type="number" min={1} max={600} value={s.fail_fast_target_seconds ?? 10} onChange={(e) => set({ fail_fast_target_seconds: Number(e.target.value) })} />
          </Field>
          <Field label="If nobody answers an approval, decline after (minutes)">
            <input className="input narrow" type="number" min={1} value={s.approval_timeout_minutes} onChange={(e) => set({ approval_timeout_minutes: Number(e.target.value) })} />
          </Field>
          <Field label="Most tools offered directly to the model" help="Above this, connector tools are offered through a search tool instead (keeps prompts small and fast on local models).">
            <input className="input narrow" type="number" min={4} max={128} value={s.max_native_tools} onChange={(e) => set({ max_native_tools: Number(e.target.value) })} />
          </Field>
          <div className="small">Keys and tokens are stored in: <Badge kind={boot.secrets_secure ? "ok" : "warn"}>{boot.secrets_secure ? "the system credential store" : "a private file (no credential store found)"}</Badge></div>
        </div>
    </>,
    explore: () => <ExploreCard s={s} set={set} />,
    check: () => <EvaluationCard />,
    telemetry: () => <TelemetryCard s={s} set={set} />,
    about: () => <>
        <div className="card stack-s">
          <h3>About</h3>
          <div className="small">KimiLM {boot.version} · llama.cpp {boot.runtime.tag}</div>
          <div className="small muted">Data folder: {boot.data_dir}</div>
          {desktop && <div className="row gap-s"><Button small icon={<FolderOpen size={13} />} onClick={() => desktop.openPath(boot.data_dir)}>Open data folder</Button>
            <Button small onClick={() => desktop.openLogs()}>Open logs</Button></div>}
        </div>
    </>,
  };
  const terms = q.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const found = terms.length ? SECTIONS.filter((x) => terms.every((t) => (x.label.toLowerCase() + " " + x.words).includes(t))) : [];
  return (
    <div className="page settings-page">
      <PageHeader title="Settings" />
      <SplitPane id="settings" className="settings-layout" initial={0.24} min={0.14} max={0.45}>
        <nav className="settings-nav">
          <div className="search"><Search size={14} /><input placeholder="Find a setting" value={q} onChange={(e) => setQ(e.target.value)} />
            {q && <button className="icon-btn" onClick={() => setQ("")} title="Clear"><X size={13} /></button>}</div>
          {SECTIONS.map(({ id, label, icon: Icon }) => (
            <div key={id} className={`side-item${!terms.length && current === id ? " active" : ""}${terms.length && !found.some((f) => f.id === id) ? " dim" : ""}`}
              onClick={() => { setQ(""); go("settings", id); }}><Icon size={15} /><span className="ellipsis">{label}</span></div>
          ))}
        </nav>
        <div className="settings-body stack">
          {terms.length > 0 && found.length === 0 && <div className="empty"><h3>No setting matches “{q}”</h3><p>Try another word, or pick a section on the left.</p></div>}
          {terms.length > 0 ? found.map((x) => <section key={x.id} className="stack">
            <div className="section-title">{x.label}</div>{render[x.id]()}</section>)
            : <section className="stack"><h2>{SECTIONS.find((x) => x.id === current)!.label}</h2>{render[current]()}</section>}
        </div>
      </SplitPane>
    </div>
  );
}

// Measuring better setups while idle (2.24.0): KimiOS's "push boundaries a bit", for every installation
function ExploreCard({ s, set }: { s: any; set: (v: any) => void }) {
  const shown = useShown();
  const [st, setSt] = useState<any>(null);
  useEffect(() => { if (shown) api.get("/explore").then(setSt).catch(() => {}); }, [shown, s.explore_when_idle]);
  return (
    <div className="card stack-s">
      <h3>Finding better ways, on its own</h3>
      <p className="muted small">When nothing is running, the computer is plugged in and nobody has used it for a while, KimiLM measures the fastest setup of a model that has not been measured (at most once a day), and evaluates a model that never was (at most once a week). Anything you start, or using the computer again, stops it at once. What it finds is offered on the model's card and in the evaluation list; nothing changes on its own.</p>
      <Toggle checked={s.explore_when_idle ?? true} onChange={(v: boolean) => set({ explore_when_idle: v })} label="Measure better setups while idle" />
      <Toggle checked={s.suggestions_enabled ?? false} onChange={(v: boolean) => set({ suggestions_enabled: v })} label="Suggest better ways of working from my own chats (for example, a request I make every day becomes a scheduled task). Looks only at KimiLM's chats on this computer; nothing is sent anywhere; nothing happens without my yes." />
      <Toggle checked={s.explore_evaluate ?? true} onChange={(v: boolean) => set({ explore_evaluate: v })} label="Also run the evaluation (12 tasks) for a model that was never evaluated, at most once a week" />
      <Field label="Idle time first (minutes)" help="No chat, task or model in use, and no keyboard or mouse input where the desktop can tell, for this long.">
        <input className="input narrow" type="number" min={5} value={s.explore_idle_minutes ?? 30} onChange={(e) => set({ explore_idle_minutes: Number(e.target.value) })} />
      </Field>
      {st && <div className="small muted">
        {st.current ? `Measuring ${st.current.model_id} now.` : `Not measuring now: ${st.reason}.`}
        {st.history?.length > 0 && <ul className="tight">{[...st.history].reverse().slice(0, 5).map((h: any, i: number) =>
          <li key={i}>{new Date(h.at).toLocaleString()} · {h.what || "fastest setup"} · {h.model_id} · {h.result || "?"}{h.best ? ` (fastest: ${h.best})` : ""}{h.of ? ` (${h.passed} of ${h.of})` : ""}{h.stopped ? ` · stopped: ${h.stopped}` : ""}</li>)}</ul>}
      </div>}
    </div>
  );
}

// 2.27.0: the parts of the window can be dragged wider, narrower, taller and shorter; this puts them back
function LayoutCard() {
  const reset = () => {
    try { Object.keys(localStorage).filter((k) => k.startsWith("kimilm.size.")).forEach((k) => localStorage.removeItem(k)); } catch {}
    location.reload();
  };
  return (
    <div className="card stack-s">
      <h3>Sizes of the parts of the window</h3>
      <p className="muted small">Drag the line between two parts (the sidebar, a chat and its Progress panel, the two sides of Images, Sound, Video, Skills, Harnesses, a project and Settings, the height of each chart): the pointer becomes a double-headed arrow. Double-click a line to put that part back; with the keyboard, Tab to it and use the arrow keys. Sizes are kept for the next start.</p>
      <div><Button small onClick={reset}>Put every part back to its original size</Button></div>
    </div>
  );
}

// 2.27.0: readings of the computer (Performance) and statistics of the models' work (Usage)
function TelemetryCard({ s, set }: { s: any; set: (v: any) => void }) {
  const { toast } = useApp();
  return (
    <div className="card stack-s">
      <h3>Performance and usage</h3>
      <p className="muted small">Performance reads the processor, graphics, memory, network, disks, temperatures and power once a second and keeps the last day in memory (nothing is written to disk). On KimiOS the system adds how busy the graphics really are, power from the chips' own meters, fans and slowing down for heat. Usage counts every model call (tokens, time, which chat, what kind of work) in KimiLM's own database. Neither leaves this computer.</p>
      <Toggle checked={s.telemetry_enabled ?? true} onChange={(v: boolean) => set({ telemetry_enabled: v })} label="Take readings of this computer (Performance)" />
      <Toggle checked={s.usage_enabled ?? true} onChange={(v: boolean) => set({ usage_enabled: v })} label="Count every model call (Usage)" />
      <div className="row gap-s">
        <Button small onClick={() => go("performance")}>Open Performance</Button>
        <Button small onClick={() => go("usage")}>Open Usage</Button>
        <ConfirmButton small onConfirm={async () => { try { await api.del("/usage"); toast("Usage statistics cleared", "ok"); } catch (e: any) { toast(e.message, "error"); } }}>Clear usage statistics</ConfirmButton>
      </div>
    </div>
  );
}
