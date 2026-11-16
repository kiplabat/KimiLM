import React, { useEffect, useState } from "react";
import { FolderOpen } from "lucide-react";
import { api, desktop } from "../api";
import { useApp } from "../store";
import { Badge, Button, Field, FolderInput, PageHeader, Toggle } from "../components/ui";

export default function Settings() {
  const { boot, refreshBoot, toast } = useApp();
  const s = boot.settings;
  const [shell, setShell] = useState<any>(null);
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
        <div className="row gap-m wrap">
          <Field label="Rounds per step" help="Tries before the best attempt is kept. Steps the planner marks as loops get two more.">
            <input className="input narrow" type="number" min={1} max={8} value={s.step_rounds ?? 3} onChange={(e) => set({ step_rounds: Number(e.target.value) })} />
          </Field>
          <Field label="Restart a silent model after (seconds)" help="No new token for this long means the model is stuck: it is restarted and the call is made again.">
            <input className="input narrow" type="number" min={30} value={s.stall_timeout_seconds ?? 180} onChange={(e) => set({ stall_timeout_seconds: Number(e.target.value) })} />
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
