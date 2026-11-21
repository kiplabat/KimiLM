import React, { useEffect, useState } from "react";
import {
  Bluetooth, Camera, Cpu, HardDrive, Keyboard, Monitor, Mouse, Network, Plug, Printer, RefreshCw, Smartphone,
  Speaker, Thermometer, Usb, Download, ShieldCheck,
} from "lucide-react";
import { api, desktop, enc } from "../api";
import { useApp, useEvent } from "../store";
import { Badge, Button, Empty, Field, PageHeader, Spinner, Toggle } from "../components/ui";
import { SplitPane } from "../components/Resizable";
import { startAgentChat } from "./Skills";

// 2.29.0: what is plugged into this computer, what drives it and what can be done with it. Agents see the same
// through the devices and device_control tools; here you can look, act and install what a device needs.

const ICONS: Record<string, any> = {
  phone: Smartphone, board: Cpu, sensor: Thermometer, display: Monitor, keyboard: Keyboard, mouse: Mouse,
  input: Keyboard, audio: Speaker, bluetooth: Bluetooth, camera: Camera, storage: HardDrive, serial: Plug,
  printer: Printer, network: Network, other: Usb,
};
const ORDER = ["phone", "board", "sensor", "serial", "display", "audio", "bluetooth", "keyboard", "mouse", "input",
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
          <div>{sel ? <Detail id={sel} onChanged={() => {}} /> : null}</div>
        </SplitPane>}
    </div>
  );
}
