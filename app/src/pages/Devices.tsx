import React, { useEffect, useState } from "react";
import {
  Bluetooth, Camera, Cpu, HardDrive, Keyboard, Monitor, Mouse, Network, Plug, Printer, RefreshCw, Smartphone,
  Speaker, Thermometer, Usb, Download, ShieldCheck, Refrigerator, Heater, DoorOpen, Flame, Target, Square, Search, X,
  AlertTriangle, Copy,
} from "lucide-react";
import { api, desktop, enc } from "../api";
import { useApp, useEvent } from "../store";
import { Badge, Button, Empty, Field, PageHeader, Spinner, Toggle } from "../components/ui";
import { InlineDetail, matches } from "../components/InlineDetail";
import { startAgentChat } from "./Skills";
import { BenchChart, type BenchRow } from "../components/BenchChart";
import { go } from "../store";

// 2.29.0: what is plugged into this computer, what drives it and what can be done with it. Agents see the same
// through the devices and device_control tools; here you can look, act and install what a device needs.

const ICONS: Record<string, any> = {
  appliance: Refrigerator, heater: Heater, phone: Smartphone, board: Cpu, sensor: Thermometer, display: Monitor, keyboard: Keyboard, mouse: Mouse,
  input: Keyboard, audio: Speaker, bluetooth: Bluetooth, camera: Camera, storage: HardDrive, serial: Plug,
  printer: Printer, network: Network, other: Usb,
};
const ORDER = ["appliance", "heater", "phone", "board", "sensor", "serial", "display", "audio", "bluetooth", "keyboard", "mouse", "input",
  "camera", "storage", "printer", "network", "other"];

function ActionForm({ device, action, onDone }: any) {
  const { toast } = useApp();
  const [args, setArgs] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const run = async () => {
    setBusy(true);
    try { const r = await api.post("/devices/control", { id: device.id, action: action.name, arguments: args }); onDone(action.name, r.result, r.folder); }
    catch (e: any) { toast(e.message, "error"); onDone(action.name, `Failed: ${e.message}`); }
    finally { setBusy(false); }
  };
  const keys = Object.keys(action.args || {});
  return (
    <div className="row gap-s wrap" style={{ alignItems: "flex-end" }}>
      {keys.map((k) => (
        <label key={k} className="stack-xs small"><span className="muted">{k}</span>
          <input className="input narrow" style={{ width: 200 }} placeholder={action.args[k]} value={args[k] || ""}
            onChange={(e) => setArgs({ ...args, [k]: e.target.value })} /></label>
      ))}
      <Button small busy={busy} onClick={run} title={action.about}>
        {action.name.replace(/_/g, " ")}</Button>
    </div>
  );
}

// 2.33.0: a display in detail. The monitor's own menu over DDC/CI (levels as sliders, lists such as the input
// as choices; a change is sent when the slider is let go) and how the computer drives it (resolution, refresh,
// scale, rotation, position, main screen, on/off). GNOME asks on the screen to keep a layout change.
function Level({ row, onCommit }: any) {
  const [v, setV] = useState<number>(row.current ?? 0);
  useEffect(() => setV(row.current ?? 0), [row.current]);
  const commit = () => { if (v !== row.current) onCommit(v); };
  return (<>
    <input type="range" min={0} max={row.max || 100} value={v} onChange={(e) => setV(Number(e.target.value))}
      onPointerUp={commit} onKeyUp={commit} onBlur={commit} />
    <span className="small">{v} of {row.max || 100}</span>
  </>);
}

function DisplayPanel({ d }: { d: any }) {
  const { toast } = useApp();
  const [st, setSt] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [lay, setLay] = useState<Record<string, string>>({});
  const load = () => api.get(`/devices/display?id=${enc(d.id)}`).then(setSt).catch((e) => setSt({ error: e.message }));
  useEffect(() => { setSt(null); setLay({}); load(); }, [d.id]);
  if (!st) return <div className="card"><Spinner /></div>;
  if (!st.supported) return null;
  const send = async (body: any) => {
    setBusy(true);
    try { const r = await api.post("/devices/display", { id: d.id, ...body }); toast(r.result, "ok"); }
    catch (e: any) { toast(e.message, "error"); }
    finally { setBusy(false); setTimeout(load, body.layout ? 1500 : 300); }
  };
  const mon = st.monitor, scr = st.layout?.screen;
  const modes: any[] = scr?.modes || [];
  const sizes = Array.from(new Set(modes.map((m) => `${m.width}x${m.height}`)));
  const curMode = modes.find((m) => m.current);
  const size = lay.mode || (curMode ? `${curMode.width}x${curMode.height}` : sizes[0]);
  const rates = modes.filter((m) => `${m.width}x${m.height}` === size).map((m) => m.refresh);
  const scales: number[] = (modes.find((m) => `${m.width}x${m.height}` === size && (!lay.refresh || String(m.refresh) === lay.refresh)) || curMode)?.scales || [];
  const changed = Object.keys(lay).length > 0;
  return (<>
    {!st.internal && <div className="card stack-s">
      <h3>The monitor's own settings</h3>
      {mon?.error ? <div className="small warn">{mon.error}</div> : !mon ? <Spinner /> : <>
        <div className="muted small">{mon.model ? `${mon.model}, ` : ""}over the cable (DDC/CI). These are the settings in the monitor's own menu.</div>
        <div className="dev-kv">{(mon.settings || []).map((row: any) => <React.Fragment key={row.name}>
          <div>{row.label}</div>
          <div className="row gap-s">{row.kind === "C"
            ? <Level row={row} onCommit={(v: number) => send({ setting: row.name, value: v })} />
            : <select className="input narrow-auto" disabled={busy} value={row.value || ""}
                onChange={(e) => send({ setting: row.name, value: e.target.value })}>
                {!row.value && <option value="">?</option>}
                {row.options.map((o: any) => <option key={o.value} value={o.value}>{o.name}</option>)}
              </select>}</div>
        </React.Fragment>)}</div>
        {(mon.other || []).length > 0 && <details className="small"><summary>Other features it lists ({mon.other.length})</summary>
          <div className="muted">{mon.other.map((o: any) => `${o.code} ${o.name}`).join(" · ")}</div></details>}
      </>}
    </div>}
    <div className="card stack-s">
      <h3>How the computer drives it</h3>
      {!st.layout?.how ? <div className="muted small">{st.layout?.note}</div>
        : !scr ? <div className="muted small">This screen is not in the session's layout.</div> : <>
        <div className="dev-kv">
          <div>On</div><div><Toggle checked={lay.on ? lay.on === "true" : scr.enabled} label=""
            onChange={(v: boolean) => setLay({ ...lay, on: String(v) })} /></div>
          {(scr.enabled || lay.on === "true") && <>
            <div>Resolution</div><div><select className="input narrow-auto" value={size}
              onChange={(e) => setLay({ ...lay, mode: e.target.value, refresh: "" })}>
              {sizes.map((x) => <option key={x} value={x}>{x.replace("x", " × ")}</option>)}</select></div>
            <div>Refresh</div><div><select className="input narrow-auto" value={lay.refresh || (curMode && `${curMode.width}x${curMode.height}` === size ? String(curMode.refresh) : "")}
              onChange={(e) => setLay({ ...lay, refresh: e.target.value })}>
              <option value="">best</option>
              {rates.map((r) => <option key={r} value={String(r)}>{r} Hz</option>)}</select></div>
            {scales.length > 0 && <><div>Scale</div><div><select className="input narrow-auto" value={lay.scale || String(scr.scale ?? 1)}
              onChange={(e) => setLay({ ...lay, scale: e.target.value })}>
              {scales.map((x) => <option key={x} value={String(x)}>{Math.round(x * 100)} %</option>)}</select></div></>}
            <div>Rotation</div><div><select className="input narrow-auto" value={lay.rotation || scr.rotation || "0"}
              onChange={(e) => setLay({ ...lay, rotation: e.target.value })}>
              {["0", "90", "180", "270"].map((r) => <option key={r} value={r}>{r}°</option>)}</select></div>
            {st.layout.others.length > 0 && <><div>Where</div><div><select className="input narrow-auto" value={lay.position || ""}
              onChange={(e) => setLay({ ...lay, position: e.target.value })}>
              <option value="">{scr.position ? `as it is (${scr.position.x}, ${scr.position.y})` : "as it is"}</option>
              {st.layout.others.flatMap((o: string) => ["right-of", "left-of", "above", "below"].map((w) =>
                <option key={w + o} value={`${w} ${o}`}>{w.replace("-", " ")} {o}</option>))}</select></div></>}
            {st.layout.how === "gnome" && <><div>Main screen</div><div><Toggle checked={lay.primary ? lay.primary === "true" : scr.primary} label=""
              onChange={(v: boolean) => setLay({ ...lay, primary: String(v) })} /></div></>}
          </>}
        </div>
        <div className="row gap-s">
          <Button small busy={busy} disabled={!changed} onClick={() => {
            const l: Record<string, string> = { ...lay };
            if (l.mode && !l.refresh) delete l.refresh;
            if (l.primary === "false") delete l.primary;
            if (l.on === String(scr.enabled)) delete l.on;
            send({ layout: l }).then(() => setLay({}));
          }}>Apply</Button>
          {changed && <Button small kind="ghost" onClick={() => setLay({})}>Undo</Button>}
          {st.layout.how === "gnome" && <span className="muted small">GNOME asks on the screen to keep it; without an answer it goes back after 20 seconds.</span>}
        </div>
      </>}
    </div>
  </>);
}

// 2.30.0: a device run by KimiOS's guard (simulated or real): live readings, what it is asked to hold, the guard's
// events; set a goal (a long-running task works through driver → experiment → model → controller); stop it;
// and, on a simulated one, make things happen to it.
const EVENTS: Record<string, { event: string; args: any; label: string; icon: any }[]> = {
  appliance: [{ event: "door_open", args: { seconds: 120 }, label: "Open the door (2 min)", icon: DoorOpen },
    { event: "warm_load", args: { compartment: "top", degrees: 5 }, label: "Warm food in the top", icon: Flame },
    { event: "ambient", args: { value: 32 }, label: "Hot day (32 °C)", icon: Thermometer },
    { event: "ambient", args: { value: 25 }, label: "Normal room (25 °C)", icon: Thermometer }],
  heater: [{ event: "ambient", args: { value: 0 }, label: "Cold outside (0 °C)", icon: Thermometer },
    { event: "ambient", args: { value: 12 }, label: "Mild outside (12 °C)", icon: Thermometer },
    { event: "door_open", args: { seconds: 120 }, label: "Open the door", icon: DoorOpen }],
};

function goalPrompt(id: string, ds: any, goal: string) {
  return `Make the device ${id} (${ds.model}) do this: ${goal}

Work through KimiOS's bench tools, in order, and do not stop to ask me:
1. bench(device="${id}"): read its datasheet (sensors, outputs and their hard limits, allowed set points, its own controller); then bench(device="${id}", send="HELP") for its commands.
2. If it has no driver: driver(device="${id}", action="find") first (KimiOS 0.14: drivers others shared for this model); if one fits, driver(action="get", entry=…) tests it here, and promote it only if it passed. Otherwise write a manifest {read: [{command, pattern with one (?P<sensor>...) group per datasheet sensor}], write: {output: {command with {value}, ok}}}, then driver(action="test") until every check passes, then driver(action="promote").
3. bench_experiment: a step on each output that matters (start from a steady state; long enough to see the response, e.g. 1–2 hours of device time), then control_identify on its CSV for each sensor that output moves.
4. Choose the controller: controller(device="${id}", action="find") shows controllers others shared for this model, with their results; get one to check it against this datasheet and compare it with your own. On/off with a band for on/off outputs, PI (control_tune) for levels; compare bands or gains with control_simulate (time in band, starts per hour within the datasheet's limit).
5. controller(action="check"), fix what it says, then controller(action="start").
6. Watch with bench(device="${id}") until the goal has held for at least two hours of device time; if the guard holds an output back or a reading drifts, adjust and start again.
Finish with: the controller you started, how well it holds the goal (mean and spread of each reading, starts per hour), and what the guard did. If a driver or controller you wrote works, prepare it for sharing with action="share" (for the controller, give its results) and give me the link: nothing is sent unless I propose it.`;
}

function BenchPanel({ device }: { device: any }) {
  const { toast } = useApp();
  const id = device.bench_id;
  const [info, setInfo] = useState<any>(null);
  const [rows, setRows] = useState<BenchRow[]>([]);
  const [goal, setGoal] = useState("");
  const [busy, setBusy] = useState(false);
  const since = React.useRef(-1e12);
  const load = () => api.get(`/bench/device?id=${enc(id)}`).then(setInfo).catch((e) => toast(e.message, "error"));
  const poll = async () => {
    try {
      const r = await api.get(`/bench/history?id=${enc(id)}&since=${since.current}`);
      if (r.rows?.length) {
        since.current = r.rows[r.rows.length - 1].t;
        setRows((old) => [...old, ...r.rows].slice(-2400));
      }
    } catch {}
  };
  useEffect(() => { setRows([]); since.current = -1e12; load(); poll(); }, [id]);
  useEffect(() => { const h = setInterval(() => { poll(); load(); }, 2000); return () => clearInterval(h); }, [id]);
  if (!info) return <div className="card"><Spinner /></div>;
  const ds = info.datasheet, st = info.status || {};
  const sensors = Object.entries(ds.sensors || {}).filter(([, v]: any) => v.unit !== "0/1")
    .map(([k, v]: any) => ({ key: k, label: v.about || k, unit: v.unit === "degC" ? "°C" : v.unit }));
  const outputs = Object.entries(ds.outputs || {}).map(([k, v]: any) => ({ key: k, label: k }));
  const ctl = st.controller || (st.mode === "default" || st.mode === "fallback" ? ds.default : null);
  const setpoints: Record<string, number> = {};
  (ctl?.loops || []).forEach((l: any) => { setpoints[l.sensor] = l.setpoint; });
  const event = async (e: any) => {
    try { toast((await api.post("/bench/event", { id, event: e.event, args: e.args })).result, "ok"); } catch (x: any) { toast(x.message, "error"); }
  };
  const start = async () => {
    if (!goal.trim()) return;
    setBusy(true);
    try {
      const r = await api.post("/missions", { text: goalPrompt(id, ds, goal.trim()) });
      toast("A long-running task is working on it", "ok");
      go("chat", r.chat_id);
    } catch (x: any) { toast(x.message, "error"); } finally { setBusy(false); }
  };
  const modeTone: Record<string, string> = { custom: "ok", default: "neutral", fallback: "warn", safe: "bad", experiment: "accent" };
  return (
    <div className="stack-m">
      <div className="card stack-s">
        <div className="row gap-s wrap">{React.createElement(ICONS[device.kind] || Refrigerator, { size: 20 })}<h3 style={{ margin: 0 }}>{ds.model}</h3>
          {info.simulated && <Badge>simulated · {ds.speed}× faster</Badge>}
          {st.mode !== "no driver" && <Badge kind={modeTone[st.mode] as any} title="custom: a controller you or an agent started · default: the device's own · fallback: its own, after the custom one failed · safe: every output at its safe value">{st.mode}</Badge>}
          {info.driver?.current ? <Badge>driver v{info.driver.current}</Badge> : <Badge kind="warn">no driver</Badge>}</div>
        <div className="muted small">{ds.about}</div>
        {rows.length > 0 && <BenchChart rows={rows} sensors={sensors} outputs={outputs} setpoints={setpoints} />}
        {!info.driver?.current && <div className="small warn">No driver yet: set a goal below and an agent writes one from the datasheet and the device's own HELP, tests it and promotes it before anything else.</div>}
        <div className="row gap-m wrap small">
          {Object.entries(st.readings || {}).map(([k, v]: any) => <span key={k}>{k}: <b>{ds.sensors?.[k]?.unit === "0/1" ? (v ? "open" : "closed")
            : typeof v === "number" ? `${v.toFixed(1)}${ds.sensors?.[k]?.unit === "degC" ? " °C" : ""}` : v}</b></span>)}
          {Object.entries(st.outputs || {}).map(([k, v]: any) => <span key={k}>{k}: <b>{v == null ? "—" : v === 1 ? "on" : v === 0 ? "off" : `${Math.round(v * 100)} %`}</b></span>)}
        </div>
      </div>
      <div className="card stack-s">
        <h3>Make it do something</h3>
        <div className="row gap-s">
          <input className="input grow" placeholder={ds.kind === "heater" ? "Keep the room at 21 °C" : "Cool the top compartment to 4 °C and the bottom to −18 °C"}
            value={goal} onChange={(e) => setGoal(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") start(); }} />
          <Button kind="primary" icon={<Target size={14} />} busy={busy} onClick={start} disabled={!goal.trim()}>Set the goal</Button>
        </div>
        <div className="muted small">A long-running task works it out: the driver, step experiments, a model of how the device responds, a controller checked in simulation, then started and watched. Whatever it asks, the guard keeps the device inside its datasheet{info.simulated ? "" : "; on a real device you approve each start"}.</div>
        {st.mode === "custom" && <Button icon={<Square size={13} />} onClick={async () => { await api.post("/bench/stop", { id }); load(); }}>Stop the controller (the device's own takes over)</Button>}
      </div>
      {info.simulated && (EVENTS[ds.kind] || []).length > 0 && <div className="card stack-s">
        <h3>Make something happen</h3>
        <div className="row gap-s wrap">{(EVENTS[ds.kind] || []).map((e) => { const I = e.icon; return <Button key={e.label} small icon={<I size={13} />} onClick={() => event(e)}>{e.label}</Button>; })}</div>
      </div>}
      <div className="card stack-s">
        <h3>What the guard did</h3>
        {(st.events || []).length === 0 ? <div className="muted small">Nothing yet.</div> :
          [...st.events].reverse().slice(0, 12).map((e: any, i: number) => <div key={i} className="small"><span className="muted">{(e.t / 3600).toFixed(1)} h</span> {e.text}</div>)}
        <details className="small"><summary>Datasheet (limits the guard enforces)</summary><pre className="dev-result">{JSON.stringify({ sensors: ds.sensors, outputs: ds.outputs, trips: ds.trips }, null, 1)}</pre></details>
        {info.driver?.manifest && <details className="small"><summary>Driver v{info.driver.current} (written and tested by an agent)</summary><pre className="dev-result">{JSON.stringify(info.driver.manifest, null, 1)}</pre></details>}
      </div>
    </div>
  );
}

function Detail({ id, onChanged }: { id: string; onChanged: () => void }) {
  const { toast } = useApp();
  const [d, setD] = useState<any>(null);
  const [out, setOut] = useState<{ action: string; text: string; folder?: string } | null>(null);
  const load = () => api.get(`/devices/one?id=${enc(id)}`).then(setD).catch((e) => { setD(null); toast(e.message, "error"); });
  useEffect(() => { setD(null); setOut(null); load(); }, [id]);
  if (!d) return <div className="card"><Spinner /></div>;
  const Icon = ICONS[d.kind] || Usb;
  const install = async (name: string) => {
    try { await api.post("/programs", { name, kind: "command" }); toast(`Installing ${name}… you are told when it is done`, "info"); }
    catch (e: any) { toast(e.message, "error"); }
  };
  const kv: [string, any][] = [
    ["What it is", d.what || d.kind], ["Connected by", d.connection], ["State", d.state],
    ["Kernel driver", (d.drivers || []).join(", ") || "none bound"],
    ["Modules that match", (d.modules || []).map((m: any) => `${m.module}${m.loaded ? " (loaded)" : ""}`).join(", ")],
    ["Ids", d.vendor_id ? `${d.vendor_id}:${d.product_id}` : ""], ["Serial ports", (d.ports || []).join(", ")],
    ["Access", Object.entries(d.access || {}).map(([k, v]) => `${k}: ${v}`).join("; ")],
    ["Battery", d.battery != null ? `${d.battery} %` : ""], ["Volume", d.volume || ""],
    ["Screen", d.details?.preferred_mode ? `${d.details.preferred_mode}${d.details.size_inches ? `, ${d.details.size_inches}″` : ""}` : ""],
  ];
  return (
    <div className="stack-m">
      <div className="card stack-s">
        <div className="row gap-s"><Icon size={20} /><h3 style={{ margin: 0 }}>{d.name}</h3><Badge>{d.kind}</Badge>
          {d.kimios_unclaimed && <Badge kind="warn" title="KimiOS found no driver for it">no driver</Badge>}</div>
        <div className="dev-kv">{kv.filter(([, v]) => v).map(([k, v]) => <React.Fragment key={k}><div>{k}</div><div>{String(v)}</div></React.Fragment>)}</div>
        {d.next && <div className="small warn">{d.next}</div>}
      </div>
      {d.kind === "display" && <DisplayPanel d={d} />}
      {(d.programs || []).length > 0 && <div className="card stack-s">
        <h3>Programs that control it</h3>
        {d.programs.map((p: any) => (
          <div key={p.program} className="row between gap-m">
            <div><b>{p.program}</b> <span className="muted small">for {p.for}</span></div>
            {p.installed ? <Badge kind="ok">installed</Badge>
              : <Button small icon={<Download size={13} />} onClick={() => install(p.program)} title={p.install}>Install</Button>}
          </div>))}
        <div className="muted small">A program an action needs is also installed by itself the first time it is needed.</div>
      </div>}
      <div className="card stack-s">
        <h3>What it can do</h3>
        {d.actions.map((a: any) => (
          <div key={a.name} className="stack-xs">
            <div className="small muted">{a.about}{a.physical && !d.trusted ? " — asks you every time" : ""}</div>
            <ActionForm device={d} action={a} onDone={(action: string, text: string, folder?: string) => { setOut({ action, text, folder }); onChanged(); }} />
          </div>))}
        {out && <div className="stack-xs"><div className="small muted">{out.action.replace(/_/g, " ")}</div>
          <pre className="dev-result">{out.text}</pre>
          {/Saved/.test(out.text) && out.folder && desktop && <Button small kind="ghost" onClick={() => desktop.openPath(out.folder)}>Open the folder</Button>}</div>}
        {d.actions.some((a: any) => a.physical) && <Toggle checked={!!d.trusted} label="Agents may control this device without asking each time (it cannot hurt anyone or anything if it moves or switches)"
          onChange={async (v: boolean) => { await api.put("/devices/trust", { id: d.id, trusted: v }); load(); }} />}
      </div>
      <div className="row gap-s">
        <Button icon={<ShieldCheck size={14} />} onClick={() => startAgentChat(`Look at the device ${d.name} (${d.id}) attached to this computer: what is it, what drives it, and what can I do with it? Install what it needs.`)}>
          Ask an agent about it</Button>
      </div>
    </div>
  );
}

// 2.32.0: when KimiOS's device service is not running (or cannot list a device), say so and what to do, instead
// of showing no simulated devices at all.
function BenchProblem({ st }: { st: any }) {
  const { toast } = useApp();
  if (!st?.kimios || (!st.reason && !(st.problems || []).length)) return null;
  const svc = Object.entries(st.services || {}).filter(([, v]) => v);
  return (
    <div className="card stack-s warn-card">
      <div className="row gap-s"><AlertTriangle size={16} className="warn" /><b>{st.reason || "KimiOS's devices have a problem"}</b></div>
      {(st.problems || []).map((p: string, i: number) => <div key={i} className="small">{p}</div>)}
      {svc.length > 0 && <div className="row gap-s wrap small">{svc.map(([k, v]: any) =>
        <Badge key={k} kind={v === "active" ? "ok" : "warn"}>{k}: {v}</Badge>)}</div>}
      {st.fix && <div className="row gap-s"><code className="small">{st.fix}</code>
        <Button small kind="ghost" icon={<Copy size={12} />} onClick={() => { navigator.clipboard?.writeText(st.fix); toast("Copied: run it in a terminal", "ok"); }}>Copy</Button></div>}
      {st.fix && <div className="muted small">Run it in a terminal (it asks for your password), then press Look again. Drivers and long-running tasks you started are kept; continue them from Activity.</div>}
      {(st.journal || []).length > 0 && <details className="small"><summary>What the services last wrote</summary><pre className="dev-result">{st.journal.join("\n")}</pre></details>}
    </div>
  );
}

const PARTS: [string, string, (d: any) => boolean][] = [
  ["bench", "KimiOS devices (run by the guard)", (d) => !!d.bench],
  ["outside", "Attached", (d) => !d.bench && !d.internal],
  ["inside", "Built in", (d) => !d.bench && !!d.internal],
];

export default function Devices() {
  const { boot, toast } = useApp();
  const [data, setData] = useState<any>(null);
  const [sel, setSel] = useState<string>("");
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const load = async () => {
    setBusy(true);
    try { setData(await api.get("/devices")); }
    catch (e: any) { toast(e.message, "error"); }
    finally { setBusy(false); }
  };
  useEffect(() => { load(); }, []);
  useEvent((e) => { if (e.kind === "devices") load(); });
  if (!data) return <div className="page"><PageHeader title="Devices" /><Spinner /></div>;
  const all = [...data.devices].sort((a: any, b: any) => (a.internal ? 1 : 0) - (b.internal ? 1 : 0) || ORDER.indexOf(a.kind) - ORDER.indexOf(b.kind));
  const list = all.filter((d: any) => matches(q, d.name, d.kind, d.what, d.connection, d.id, d.state, d.drivers, d.simulated ? "simulated" : ""));
  const chosen = all.find((d: any) => d.id === sel);
  return (
    <div className="page">
      <PageHeader title="Devices" subtitle="What is plugged into this computer, what drives it and what you (and your agents) can do with it."
        actions={<>
          <Toggle checked={boot.settings.devices_watch !== false} label="Tell me when a device is plugged in"
            onChange={async (v: boolean) => { await api.put("/settings", { devices_watch: v }); }} />
          <Button icon={<RefreshCw size={14} />} busy={busy} onClick={load}>Look again</Button></>} />
      <BenchProblem st={data.bench} />
      {all.length > 0 && <div className="search dev-search"><Search size={14} />
        <input placeholder="Find a device: name, kind, connection, driver…" value={q} onChange={(e) => setQ(e.target.value)} />
        {q && <span className="muted small">{list.length} of {all.length}</span>}
        {q && <button className="icon-btn" onClick={() => setQ("")} title="Clear"><X size={13} /></button>}</div>}
      {all.length === 0 ? <Empty icon={<Usb />} title="Nothing attached">Plug in a phone, a board, a sensor or a display; it appears here.</Empty> :
        list.length === 0 ? <Empty icon={<Search />} title={`No device matches “${q}”`}>Try another word, or clear the search.</Empty> :
        <div className="stack-m">
          {PARTS.map(([part, title, has]) => {
            const rows = list.filter(has);
            if (!rows.length) return null;
            return <div key={part} className="stack-s"><div className="muted small">{title}</div>
              <div className="dev-grid inline-grid">{rows.map((d: any) => {
                const Icon = ICONS[d.kind] || Usb;
                return <React.Fragment key={d.id}>
                  <button className={`dev-card${sel === d.id ? " on" : ""}`} aria-expanded={sel === d.id}
                    onClick={() => setSel(sel === d.id ? "" : d.id)}>
                    <span className="dev-icon"><Icon size={17} /></span>
                    <span className="stack-xs" style={{ minWidth: 0 }}><b className="ellipsis">{d.name}</b>
                      <span className="muted small">{d.what || d.kind}{d.connection ? ` · ${d.connection}` : ""}</span>
                      {d.state && <span className="small warn">{d.state}</span>}</span></button>
                  {sel === d.id && chosen && <InlineDetail id={d.id}>
                    {chosen.bench ? <BenchPanel device={chosen} /> : <Detail id={sel} onChanged={() => {}} />}</InlineDetail>}
                </React.Fragment>;
              })}</div></div>;
          })}
        </div>}
    </div>
  );
}
