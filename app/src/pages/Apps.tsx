// 2.34.0: Apps: every control panel and game built for you, opened like any other app. Install one from a file
// or a web address, add the examples, open one here or in its own window (or from the computer's apps menu).
import React, { useEffect, useRef, useState } from "react";
import { Download, Gamepad2, Globe, LayoutGrid, Plus, SlidersHorizontal, Upload } from "lucide-react";
import { api, enc } from "../api";
import { go, useApp, useEvent } from "../store";
import { Badge, Button, Empty, PageHeader, Spinner, Tabs } from "../components/ui";
import AppRunner, { appIcon, kindLabel } from "../components/AppRunner";

export function useApps(kind = "", device = "") {
  const [data, setData] = useState<any>(null);
  const load = () => api.get(`/apps?kind=${enc(kind)}&device=${enc(device)}`).then(setData).catch(() => setData({ apps: [], examples: [] }));
  useEffect(() => { load(); }, [kind, device]);
  useEvent((e) => { if (e.kind === "apps_changed" || e.kind === "app_built") load(); }, [kind, device]);
  return { apps: data?.apps || null, examples: data?.examples || [], reload: load };
}

export function AppTile({ app, onOpen }: { app: any; onOpen: () => void }) {
  return (
    <button className="app-tile" onClick={onOpen} title={app.about || app.name}>
      {appIcon(app, 52)}
      <span className="stack-xs" style={{ minWidth: 0, alignItems: "flex-start" }}>
        <b className="ellipsis" style={{ maxWidth: "100%" }}>{app.name}</b>
        <span className="muted small ellipsis" style={{ maxWidth: "100%" }}>{kindLabel(app)}{app.device?.name ? ` · ${app.device.name}` : ""}</span>
        <span className="row gap-s small">
          {app.building ? <Badge kind="accent"><Spinner size={10} /> building</Badge> : app.ready ? <span className="muted">v{app.current}</span> : <Badge kind="warn">no version yet</Badge>}
          {app.device?.unbound && <Badge kind="warn">choose its device</Badge>}
          {app.source === "example" && <span className="muted">example</span>}
        </span>
      </span>
    </button>
  );
}

function Bind({ app, onDone }: any) {
  const { toast } = useApp();
  const [devices, setDevices] = useState<any[] | null>(null);
  useEffect(() => { api.get("/devices").then((r) => setDevices(r.devices)).catch(() => setDevices([])); }, []);
  if (!devices) return <Spinner />;
  const fit = devices.filter((d) => !app.device?.kind || d.kind === app.device.kind || (app.device.bench && d.bench));
  return (
    <div className="card stack-s">
      <b>{app.name} was made for {app.device?.model || app.device?.kind || "a device"} on another computer. Which device here is it for?</b>
      <div className="row gap-s wrap">{(fit.length ? fit : devices).slice(0, 24).map((d) => <Button key={d.id} small onClick={async () => {
        try { await api.put(`/apps/${enc(app.id)}`, { device_id: d.id }); toast(`${app.name} now controls ${d.name}`, "ok"); onDone(); } catch (e: any) { toast(e.message, "error"); }
      }}>{d.name}</Button>)}</div>
    </div>
  );
}

export default function Apps({ openId }: { openId?: string }) {
  const { toast } = useApp();
  const [tab, setTab] = useState("all");
  const { apps, examples, reload } = useApps();
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState("");
  const file = useRef<HTMLInputElement>(null);
  const importFile = async (f: File) => {
    setBusy("file");
    try { const a = await api.upload("/apps/import", f); toast(`Installed ${a.name}`, "ok"); go("apps", a.id); }
    catch (e: any) { toast(e.message, "error"); } finally { setBusy(""); }
  };
  const importUrl = async () => {
    setBusy("url");
    try { const a = await api.post("/apps/import", { url: url.trim() }); setUrl(""); toast(`Installed ${a.name}`, "ok"); go("apps", a.id); }
    catch (e: any) { toast(e.message, "error"); } finally { setBusy(""); }
  };
  if (openId) {
    const a = (apps || []).find((x: any) => x.id === openId);
    return <div className="page page-app">
      {a?.device?.unbound && <Bind app={a} onDone={reload} />}
      <AppRunner appId={openId} onBack={() => go("apps")} /></div>;
  }
  const have = new Set((apps || []).map((a: any) => a.example || a.id));
  const missing = examples.filter((e: any) => e.kind === "game" && !have.has(e.example));
  const list = (apps || []).filter((a: any) => tab === "all" || a.kind === tab);
  return (
    <div className="page">
      <PageHeader title="Apps" subtitle="Control panels for your devices and games, built by a model and kept here. Open one, rebuild it when you have a better model, or take it to another computer."
        actions={<>
          <Button icon={<Gamepad2 size={14} />} onClick={() => go("games")}>Make a game</Button>
          <Button icon={<SlidersHorizontal size={14} />} onClick={() => go("devices")} title="A control panel is made from the device's page">Make a control panel</Button>
        </>} />
      <Tabs value={tab} onChange={setTab} tabs={[
        { id: "all", label: "All", count: apps?.length || 0 },
        { id: "game", label: "Games", count: (apps || []).filter((a: any) => a.kind === "game").length },
        { id: "device", label: "Control panels", count: (apps || []).filter((a: any) => a.kind === "device").length }]} />
      {apps === null ? <Spinner /> : list.length === 0 ? <Empty icon={<LayoutGrid />} title={tab === "device" ? "No control panels yet" : "No apps yet"}>
          {tab === "device" ? "Open a device in Devices and press Build a control panel." : "Make a game, or install one from a file."}</Empty> :
        <div className="app-grid">{list.map((a: any) => <AppTile key={a.id} app={a} onOpen={() => go("apps", a.id)} />)}</div>}
      <div className="card stack-s" style={{ marginTop: 18 }}>
        <h3><Download size={15} /> Install an app</h3>
        <div className="muted small">An app from another computer: a .kimiapp file (Export in an app's bar), a zip of a folder with index.html, or a web address that serves one. Apps run sandboxed: no internet, and nothing reaches a device without your yes.</div>
        <div className="row gap-s wrap">
          <input ref={file} type="file" accept=".kimiapp,.zip" hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) importFile(f); e.target.value = ""; }} />
          <Button icon={<Upload size={14} />} busy={busy === "file"} onClick={() => file.current?.click()}>From a file…</Button>
          <Globe size={15} className="muted" />
          <input className="input grow" placeholder="https://… .kimiapp" value={url} onChange={(e) => setUrl(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && url.trim()) importUrl(); }} />
          <Button busy={busy === "url"} disabled={!/^https:\/\/\S+$/.test(url.trim())} onClick={importUrl}>Install</Button>
        </div>
        {missing.length > 0 && <div className="row gap-s wrap small"><span className="muted">Examples you removed:</span>
          {missing.map((e: any) => <Button key={e.example} small icon={<Plus size={12} />} onClick={async () => {
            try { const a = await api.post("/apps", { example: e.example }); go("apps", a.id); } catch (x: any) { toast(x.message, "error"); } }}>{e.name}</Button>)}</div>}
      </div>
    </div>
  );
}
