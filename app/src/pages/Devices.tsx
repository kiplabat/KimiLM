import React, { useEffect, useState } from "react";
import {
  Bluetooth, Camera, Cpu, HardDrive, Keyboard, Monitor, Mouse, Network, Plug, Printer, RefreshCw, Smartphone,
  Speaker, Thermometer, Usb, Download, ShieldCheck, Refrigerator, Heater, DoorOpen, Flame, Target, Square,
} from "lucide-react";
import { api, desktop, enc } from "../api";
import { useApp, useEvent } from "../store";
import { Badge, Button, Empty, Field, PageHeader, Spinner, Toggle } from "../components/ui";
import { SplitPane } from "../components/Resizable";
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
2. If it has no driver: write a manifest {read: [{command, pattern with one (?P<sensor>...) group per datasheet sensor}], write: {output: {command with {value}, ok}}}, then driver(action="test") until every check passes, then driver(action="promote").
3. bench_experiment: a step on each output that matters (start from a steady state; long enough to see the response, e.g. 1–2 hours of device time), then control_identify on its CSV for each sensor that output moves.
4. Choose the controller: on/off with a band for on/off outputs, PI (control_tune) for levels; compare bands or gains with control_simulate (time in band, starts per hour within the datasheet's limit).
5. controller(action="check"), fix what it says, then controller(action="start").
6. Watch with bench(device="${id}") until the goal has held for at least two hours of device time; if the guard holds an output back or a reading drifts, adjust and start again.
Finish with: the controller you started, how well it holds the goal (mean and spread of each reading, starts per hour), and what the guard did.`;
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
        <div className="row gap-s wrap"><Refrigerator size={20} /><h3 style={{ margin: 0 }}>{ds.model}</h3>
          {info.simulated && <Badge>simulated · {ds.speed}× faster</Badge>}
          <Badge kind={modeTone[st.mode] as any} title="custom: a controller you or an agent started · default: the device's own · fallback: its own, after the custom one failed · safe: every output at its safe value">{st.mode}</Badge>
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

export default function Devices() {
  const { boot, toast } = useApp();
  const [data, setData] = useState<any>(null);
  const [sel, setSel] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const load = async () => {
    setBusy(true);
    try { const r = await api.get("/devices"); setData(r); if (!sel && r.devices.length) setSel((r.devices.find((d: any) => !d.internal) || r.devices[0]).id); }
    catch (e: any) { toast(e.message, "error"); }
    finally { setBusy(false); }
  };
  useEffect(() => { load(); }, []);
  useEvent((e) => { if (e.kind === "devices") load(); });
  if (!data) return <div className="page"><PageHeader title="Devices" /><Spinner /></div>;
  const list = [...data.devices].sort((a: any, b: any) => (a.internal ? 1 : 0) - (b.internal ? 1 : 0) || ORDER.indexOf(a.kind) - ORDER.indexOf(b.kind));
  return (
    <div className="page">
      <PageHeader title="Devices" subtitle="What is plugged into this computer, what drives it and what you (and your agents) can do with it."
        actions={<>
          <Toggle checked={boot.settings.devices_watch !== false} label="Tell me when a device is plugged in"
            onChange={async (v: boolean) => { await api.put("/settings", { devices_watch: v }); }} />
          <Button icon={<RefreshCw size={14} />} busy={busy} onClick={load}>Look again</Button></>} />
      {list.length === 0 ? <Empty icon={<Usb />} title="Nothing attached">Plug in a phone, a board, a sensor or a display; it appears here.</Empty> :
        <SplitPane id="devices" initial={0.42}>
          <div className="stack-s">
            {["outside", "inside"].map((part) => {
              const rows = list.filter((d: any) => (part === "inside") === !!d.internal);
              if (!rows.length) return null;
              return <div key={part} className="stack-s"><div className="muted small">{part === "inside" ? "Built in" : "Attached"}</div>
                <div className="dev-grid">{rows.map((d: any) => {
                  const Icon = ICONS[d.kind] || Usb;
                  return <button key={d.id} className={`dev-card${sel === d.id ? " on" : ""}`} onClick={() => setSel(d.id)}>
                    <span className="dev-icon"><Icon size={17} /></span>
                    <span className="stack-xs" style={{ minWidth: 0 }}><b className="ellipsis">{d.name}</b>
                      <span className="muted small">{d.what || d.kind}{d.connection ? ` · ${d.connection}` : ""}</span>
                      {d.state && <span className="small warn">{d.state}</span>}</span></button>;
                })}</div></div>;
            })}
          </div>
          <div>{sel ? (list.find((d: any) => d.id === sel)?.bench ? <BenchPanel device={list.find((d: any) => d.id === sel)} />
            : <Detail id={sel} onChanged={() => {}} />) : null}</div>
        </SplitPane>}
    </div>
  );
}
