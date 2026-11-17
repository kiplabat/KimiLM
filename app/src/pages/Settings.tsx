import React, { useEffect, useState } from "react";
import { FolderOpen } from "lucide-react";
import { api, desktop } from "../api";
import { go, useApp } from "../store";
import { Badge, Button, Field, FolderInput, PageHeader, Toggle } from "../components/ui";

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
  useEffect(() => { load(); }, []);
  useEffect(() => {
    if (!data?.current) return;
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [!!data?.current]);
  const dur = (sec: number) => sec < 90 ? `${Math.round(sec)} s` : sec < 5400 ? `${Math.round(sec / 60)} min` : `${(sec / 3600).toFixed(1)} h`;
  const k = (n: number) => n >= 10000 ? `${Math.round(n / 1000)}k` : n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n || 0);
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
          ? <><span className="small"><b>Running:</b> task {data.current.done + 1} of {data.current.total} · {data.current.task}</span>
              <Button small kind="ghost" onClick={async () => { await api.post("/evals/stop"); load(); }}>Stop</Button></>
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
              <span><b>{r.summary?.passed ?? 0} of {r.summary?.total ?? "?"}</b>{r.status !== "done" ? ` (${r.status})` : ""}</span>
              <span>{dur(r.summary?.seconds || 0)}</span><span>{k(r.summary?.read)}</span><span>{r.summary?.cache_percent ?? 0}%</span><span>{k(r.summary?.written)}</span>
            </div>
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

export default function Settings() {
  const { boot, refreshBoot, toast } = useApp();
  const s = boot.settings;
  const [shell, setShell] = useState<any>(null);
  const [speed, setSpeed] = useState<any>(null);
  useEffect(() => { api.get("/runtime").then(setSpeed).catch(() => {}); }, []);
  const [modelsDir, setModelsDir] = useState(s.models_dir || "");
  const [hf, setHf] = useState("");
  useEffect(() => { desktop?.getShellPrefs().then(setShell); }, []);
  const set = async (values: any) => { try { await api.put("/settings", values); refreshBoot(); } catch (e: any) { toast(e.message, "error"); } };
  const setShellPref = async (p: any) => setShell(await desktop.setShellPrefs(p));
  return (
    <div className="page narrow-page">
      <PageHeader title="Settings" />
      {shell && (
        <div className="card stack-s">
          <h3>Running in the background</h3>
          <Toggle checked={shell.openAtLogin} onChange={(v: boolean) => setShellPref({ openAtLogin: v })} label="Start KimiLM when I sign in to Windows (in the tray) — needed for schedules" />
          <Toggle checked={shell.keepInTray} onChange={(v: boolean) => setShellPref({ keepInTray: v })} label="Closing the window keeps the app running in the tray" />
        </div>
      )}
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
      <EvaluationCard />
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
      <div className="card stack-s">
        <h3>Web and downloads</h3>
        <Toggle checked={s.browser_search ?? true} onChange={(v: boolean) => set({ browser_search: v })} label="When search engines return nothing useful, search again in a real browser window (Edge)" />
        <Field label="Hugging Face token (optional)" help="Only for models that require you to accept a licence on huggingface.co. Stored in Windows Credential Manager.">
          <div className="row gap-s"><input className="input" type="password" value={hf} placeholder={boot.has_hf_token ? "saved ••••" : "hf_…"} onChange={(e) => setHf(e.target.value)} />
            <Button onClick={async () => { await set({ hf_token: hf }); setHf(""); toast("Saved", "ok"); }}>Save</Button></div>
        </Field>
      </div>
      <div className="card stack-s">
        <h3>Safety</h3>
        <Toggle checked={s.allow_python} onChange={(v: boolean) => set({ allow_python: v })} label="Let the agent run code: Python and commands such as tests and git (always asks first unless auto-approve is on)" />
        <Field label="If nobody answers an approval, decline after (minutes)">
          <input className="input narrow" type="number" min={1} value={s.approval_timeout_minutes} onChange={(e) => set({ approval_timeout_minutes: Number(e.target.value) })} />
        </Field>
        <Field label="Most tools offered directly to the model" help="Above this, connector tools are offered through a search tool instead (keeps prompts small and fast on local models).">
          <input className="input narrow" type="number" min={4} max={128} value={s.max_native_tools} onChange={(e) => set({ max_native_tools: Number(e.target.value) })} />
        </Field>
        <div className="small">Keys and tokens are stored in: <Badge kind={boot.secrets_secure ? "ok" : "warn"}>{boot.secrets_secure ? "the system credential store" : "a private file (no credential store found)"}</Badge></div>
      </div>
      <div className="card stack-s">
        <h3>About</h3>
        <div className="small">KimiLM {boot.version} · llama.cpp {boot.runtime.tag}</div>
        <div className="small muted">Data folder: {boot.data_dir}</div>
        {desktop && <div className="row gap-s"><Button small icon={<FolderOpen size={13} />} onClick={() => desktop.openPath(boot.data_dir)}>Open data folder</Button>
          <Button small onClick={() => desktop.openLogs()}>Open logs</Button></div>}
      </div>
    </div>
  );
}
