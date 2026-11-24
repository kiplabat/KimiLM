// 2.34.0: an app (a device's control panel or a game) running in a sandboxed frame. The frame has no origin of its
// own: it cannot reach KimiLM's API, other apps or the internet. Everything it does arrives here as a message (the
// `kimi` object, engine/builtin/apps/kimi.js), is checked against the app's device and permissions, asks the person
// where needed (the bar above the frame) and is answered. The bar also holds versions, rebuild, export, the
// computer's apps menu and uninstall.
import React, { useEffect, useRef, useState } from "react";
import {
  ArrowLeft, Bug, CheckCircle2, Cpu, ExternalLink, FlaskConical, Hammer, Maximize2, MessageSquare, Monitor, RotateCcw,
  Share2, Trash2, Upload, X,
} from "lucide-react";
import { api, desktop, enc, TOKEN } from "../api";
import { go, useApp, useEvent, useShown } from "../store";
import { Badge, Button, ConfirmButton, Spinner } from "./ui";
import { ModelPicker } from "./Pickers";

type Ask = { id: number; text: string; choices: [string, string][]; resolve: (choice: string) => void };

export function appIcon(app: any, size = 44) {
  const ic = app?.icon || {};
  return <span className="app-icon" style={{ width: size, height: size, background: ic.color || "#7E8A97", fontSize: size * 0.5 }}>{ic.emoji || "▢"}</span>;
}

// An SVG of the app's icon, for the computer's own apps menu
export function iconSvg(app: any) {
  const ic = app?.icon || {};
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128"><rect width="128" height="128" rx="28" fill="${ic.color || "#7E8A97"}"/>` +
    `<text x="64" y="86" font-size="64" text-anchor="middle" font-family="Noto Color Emoji, Segoe UI Emoji, Apple Color Emoji, sans-serif">${ic.emoji || ""}</text></svg>`;
}

export function kindLabel(app: any) {
  return app?.kind === "game" ? `${app.dims ? app.dims + " " : ""}game` : "control panel";
}

export default function AppRunner({ appId, solo, embedded, onBack }: { appId: string; solo?: boolean; embedded?: boolean; onBack?: () => void }) {
  const { toast } = useApp();
  const shown = useShown();
  const [app, setApp] = useState<any>(null);
  const [error, setError] = useState("");
  const [asks, setAsks] = useState<Ask[]>([]);
  const [errors, setErrors] = useState<string[]>([]);
  const [rebuild, setRebuild] = useState(false);
  const [prompt, setPrompt] = useState("");
  const [model, setModel] = useState("");
  const [fresh, setFresh] = useState(false);
  const [busy, setBusy] = useState("");
  const [frameKey, setFrameKey] = useState(0);
  const [shared, setShared] = useState<any>(null);       // 2.35.0: the registry entry written for sharing
  const frame = useRef<HTMLIFrameElement>(null);
  const appRef = useRef<any>(null);
  const once = useRef<Record<string, boolean>>({});
  const seq = useRef(0);
  appRef.current = app;

  const load = async () => {
    try { const a = await api.get(`/apps/${enc(appId)}`); setApp(a); setError(""); return a; }
    catch (e: any) { setError(e.message); return null; }
  };
  useEffect(() => { setApp(null); setErrors([]); setAsks([]); load(); }, [appId]);
  useEvent((e) => {
    if ((e.kind === "apps_changed" || e.kind === "app_built") && e.app_id === appId) {
      const before = appRef.current?.current;
      load().then((a) => { if (a && a.current !== before) { setFrameKey((k) => k + 1); setErrors([]); if (e.kind === "app_built" && a.current) toast(`${a.name}: version ${a.current} is ready`, "ok"); } });
    }
  }, [appId]);
  useEffect(() => {       // a hidden (kept) screen tells its app, so a game can pause
    frame.current?.contentWindow?.postMessage({ kimi: 1, event: "visible", data: shown }, "*");
  }, [shown]);

  // ---------------------------------------------------------------- asking the person
  const ask = (text: string, choices: [string, string][]) => new Promise<string>((resolve) => {
    const id = ++seq.current;
    setAsks((list) => [...list, { id, text, choices, resolve: (c: string) => { setAsks((l) => l.filter((x) => x.id !== id)); resolve(c); } }]);
  });
  const allow = async (perm: "control" | "ai", text: string) => {
    const a = appRef.current;
    if (a?.permissions?.[perm] || once.current[perm]) return;
    const c = await ask(text, [["always", "Allow for this app"], ["once", "Only now"], ["no", "No"]]);
    if (c === "no") throw new Error("The person said no");
    if (c === "once") { once.current[perm] = true; return; }
    const upd = await api.put(`/apps/${enc(a.id)}`, { permissions: { [perm]: true } });
    setApp(upd);
  };
  const confirmPhysical = async (text: string) => {
    const c = await ask(text, [["yes", "Yes, do it"], ["no", "No"]]);
    if (c !== "yes") throw new Error("The person said no");
  };

  // ---------------------------------------------------------------- what the app asks for
  const deviceDesc = useRef<any>(null);
  const describe = async () => {
    const dev = appRef.current?.device;
    if (!dev?.id) throw new Error("This panel has no device yet: choose one in its bar");
    deviceDesc.current = await api.get(`/devices/one?id=${enc(dev.id)}`);
    return deviceDesc.current;
  };
  const handle = async (method: string, args: any) => {
    const a = appRef.current;
    if (!a) throw new Error("The app is not loaded");
    const dev = a.device || {};
    const bench = !!dev.bench, bid = dev.bench_id || String(dev.id || "").replace(/^bench:/, "");
    const needDevice = () => { if (a.kind !== "device" || !dev.id) throw new Error("This app has no device"); };
    switch (method) {
      case "hello": return { app: { id: a.id, name: a.name, kind: a.kind, version: a.current }, device: dev.id ? { id: dev.id, name: dev.name, kind: dev.kind, bench, simulated: !!dev.simulated } : null };
      case "notify": toast(`${a.name}: ${String(args?.text || "").slice(0, 200)}`, "info"); return true;
      case "storage.get": return (await api.get(`/apps/${enc(a.id)}/storage`)).storage[args?.key] ?? null;
      case "storage.all": return (await api.get(`/apps/${enc(a.id)}/storage`)).storage;
      case "storage.set": return api.put(`/apps/${enc(a.id)}/storage`, { key: args?.key, value: args?.value });
      case "ai.ask":
        await allow("ai", `${a.name} wants to ask your model questions (for example the computer's moves). It sees only what the app sends.`);
        return api.post(`/apps/${enc(a.id)}/ask`, { prompt: args?.prompt, schema: args?.schema, max_tokens: args?.max_tokens, once: !!once.current.ai });
      case "device.info":
        needDevice();
        return bench ? api.get(`/bench/device?id=${enc(bid)}`) : describe();
      case "device.state":
        needDevice();
        return bench ? api.get(`/bench/device?id=${enc(bid)}`) : describe();
      case "device.history": {
        needDevice();
        if (!bench) throw new Error("History is kept for devices KimiOS's guard runs");
        return (await api.get(`/bench/history?id=${enc(bid)}&since=${Number(args?.since ?? -1e12)}`)).rows || [];
      }
      case "device.check":
        needDevice();
        if (!bench) throw new Error("Controllers are for devices KimiOS's guard runs");
        return api.post("/bench/controller", { id: bid, spec: args?.spec, start: false });
      case "device.control": {
        needDevice();
        if (!bench) throw new Error("Controllers are for devices KimiOS's guard runs; use kimi.device.act");
        const checked = await api.post("/bench/controller", { id: bid, spec: args?.spec, start: false });
        if (!checked.valid) return checked;
        await allow("control", `${a.name} wants to control ${dev.name}. KimiOS's guard still keeps the device inside its datasheet.`);
        if (!dev.simulated) await confirmPhysical(`${a.name} is about to run a new controller on the real ${dev.name}. Go ahead?`);
        return api.post("/bench/controller", { id: bid, spec: args?.spec, start: true, by: a.name });
      }
      case "device.release":
        needDevice();
        if (!bench) throw new Error("Only devices KimiOS's guard runs have a controller to give back");
        await allow("control", `${a.name} wants to control ${dev.name}.`);
        return api.post("/bench/stop", { id: bid });
      case "device.event":
        needDevice();
        if (!bench || !dev.simulated) throw new Error("Only simulated devices take events");
        return (await api.post("/bench/event", { id: bid, event: args?.name, args: args?.args || {} })).result;
      case "device.act": {
        needDevice();
        const d = deviceDesc.current || await describe();
        const act = (d.actions || []).find((x: any) => x.name === args?.action);
        if (!act) throw new Error(`${d.name} cannot do ${args?.action}. It can: ${(d.actions || []).map((x: any) => x.name).join(", ")}`);
        const strArgs: Record<string, string> = {};
        Object.entries(args?.args || {}).forEach(([k, v]) => { strArgs[k] = typeof v === "string" ? v : JSON.stringify(v); });
        if (act.changes) await allow("control", `${a.name} wants to change things on ${d.name} (${act.name.replace(/_/g, " ")} and other actions).`);
        if (act.physical && !d.trusted) await confirmPhysical(`${a.name}: ${act.name.replace(/_/g, " ")} on ${d.name} ${JSON.stringify(strArgs)}. This may make it move or switch something. Go ahead?`);
        return (await api.post("/devices/control", { id: dev.id, action: act.name, arguments: strArgs })).result;
      }
      case "device.install": {
        needDevice();
        await confirmPhysical(`${a.name} wants to install its device part on ${dev.name}. Go ahead?`);
        return api.post(`/apps/${enc(a.id)}/install`);
      }
      case "error": {
        const where = args?.source ? ` (${args.source}${args.line ? `:${args.line}` : ""})` : "";
        setErrors((list) => list.includes(String(args?.message) + where) ? list : [...list.slice(-4), String(args?.message) + where]);
        return null;
      }
      default: throw new Error(`kimi has no ${method}`);
    }
  };
  useEffect(() => {
    const on = async (e: MessageEvent) => {
      if (!frame.current || e.source !== frame.current.contentWindow) return;
      const m = e.data || {};
      if (!m.kimi || !m.method) return;
      const reply = (out: any) => { if (m.id) frame.current?.contentWindow?.postMessage({ kimi: 1, id: m.id, ...out }, "*"); };
      try { reply({ result: await handle(String(m.method), m.args) }); }
      catch (err: any) { reply({ error: err?.message || String(err) }); }
    };
    window.addEventListener("message", on);
    return () => window.removeEventListener("message", on);
  }, []);

  // ---------------------------------------------------------------- the bar's actions
  const doRebuild = async (text = prompt) => {
    if (!text.trim()) return;
    setBusy("build");
    try {
      await api.post(`/apps/${enc(appId)}/build`, { prompt: text.trim(), model: model || undefined, fresh });
      toast("The builder is at work in a chat (the chat button above): the new version opens here when it is saved, and this one stays", "ok");
      setRebuild(false); setPrompt(""); load();
    } catch (e: any) { toast(e.message, "error"); } finally { setBusy(""); }
  };
  const fixErrors = () => doRebuild(`The app reports these errors while it runs; fix them and keep everything else as it is:\n${errors.join("\n")}`);
  const exportApp = async () => {
    try {
      const r = await fetch(`/api/apps/${enc(appId)}/export`, { headers: { Authorization: `Bearer ${TOKEN}` } });
      if (!r.ok) throw new Error((await r.json()).error || "Export failed");
      const blob = await r.blob(), url = URL.createObjectURL(blob), link = document.createElement("a");
      link.href = url; link.download = `${appId}-v${app.current}.kimiapp`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 5000);
    } catch (e: any) { toast(e.message, "error"); }
  };
  const shortcut = async (add: boolean) => {
    try {
      const r = await desktop.appShortcut({ id: app.id, name: app.name, about: app.about, kind: app.kind, icon: iconSvg(app), add });
      if (!r?.ok) throw new Error(r?.reason || "The computer's apps menu could not be changed");
      setApp(await api.put(`/apps/${enc(app.id)}`, { shortcut: add }));
      toast(add ? `${app.name} is in the computer's apps menu` : `Removed from the computer's apps menu`, "ok");
    } catch (e: any) { toast(e.message, "error"); }
  };
  const uninstall = async () => {
    try {
      if (app.shortcut && desktop?.appShortcut) await desktop.appShortcut({ id: app.id, name: app.name, add: false });
      await api.del(`/apps/${enc(app.id)}`);
      toast(`${app.name} was uninstalled`, "ok");
      if (solo) window.close(); else if (onBack) onBack(); else go("apps");
    } catch (e: any) { toast(e.message, "error"); }
  };
  const tryIt = async () => {                 // 2.35.0: a few seconds in a hidden browser, with stand-ins
    setBusy("try");
    try { const r = await api.post(`/apps/${enc(app.id)}/versions/${app.current}/preview`); load();
      toast(r.skipped ? `Not tried: ${r.skipped}` : r.ok ? "Tried it: no problems seen" : `Tried it: ${r.problems.length} problem(s)`, r.ok ? "ok" : "info"); }
    catch (e: any) { toast(e.message, "error"); } finally { setBusy(""); }
  };
  const share = async () => {
    setBusy("share");
    try { setShared(await api.post(`/apps/${enc(app.id)}/share`, {})); }
    catch (e: any) { toast(e.message, "error"); } finally { setBusy(""); }
  };
  const openLink = (url: string) => { if (desktop?.openExternal) desktop.openExternal(url); else window.open(url, "_blank", "noopener"); };
  const install = async () => {
    setBusy("install");
    try { const r = await api.post(`/apps/${enc(app.id)}/install`); toast(r.result, "ok"); load(); }
    catch (e: any) { toast(e.message, "error"); } finally { setBusy(""); }
  };

  if (error) return <div className="app-runner"><div className="card"><b>{error}</b>{onBack && <Button onClick={onBack}>Back</Button>}</div></div>;
  if (!app) return <div className="app-runner"><Spinner /></div>;
  const cur = (app.versions || []).find((v: any) => v.n === app.current);
  return (
    <div className={`app-runner${solo ? " solo" : ""}${embedded ? " embedded" : ""}`}>
      <div className="app-bar">
        {onBack && !solo && <button className="icon-btn" onClick={onBack} title="Back to the apps"><ArrowLeft size={16} /></button>}
        {appIcon(app, 28)}
        <div className="stack-xs" style={{ minWidth: 0 }}>
          <b className="ellipsis">{app.name}</b>
          <span className="muted small ellipsis">{kindLabel(app)}{app.device?.name ? ` · ${app.device.name}` : ""}{cur?.model ? ` · built by ${cur.model}` : ""}</span>
        </div>
        {app.building && <Badge kind="accent" title="A builder is making a new version; this one stays until it is saved"><Spinner size={11} /> building</Badge>}
        {cur?.preview && !cur.preview.skipped && (cur.preview.ok
          ? <span className="app-tried ok" title={`Tried for a few seconds (${cur.preview.how}): ${(cur.preview.notes || []).join("; ")}`}><CheckCircle2 size={13} /> tried</span>
          : <span className="app-tried bad" title={(cur.preview.problems || []).join("\n")}><Bug size={13} /> {cur.preview.problems.length} problem{cur.preview.problems.length === 1 ? "" : "s"} when tried</span>)}
        <div className="grow" />
        {(app.versions || []).length > 0 && <select className="input narrow-auto" value={app.current || ""} title="Every build is a version; choose which one runs"
          onChange={async (e) => { setApp(await api.post(`/apps/${enc(app.id)}/versions/${e.target.value}`)); setFrameKey((k) => k + 1); }}>
          {[...app.versions].reverse().map((v: any) => <option key={v.n} value={v.n}>v{v.n}{v.notes ? ` · ${v.notes.slice(0, 40)}` : ""}</option>)}
        </select>}
        <button className="icon-btn" title="Start it again" onClick={() => { setErrors([]); setFrameKey((k) => k + 1); }}><RotateCcw size={15} /></button>
        <button className="icon-btn" title="Full screen" onClick={() => frame.current?.requestFullscreen?.()}><Maximize2 size={15} /></button>
        <Button small icon={<Hammer size={13} />} onClick={() => setRebuild(!rebuild)} title="Ask a model for a new version (keeps this one)">Rebuild</Button>
        {app.chat_id && !solo && <button className="icon-btn" title="The builder's chat" onClick={() => go("chat", app.chat_id)}><MessageSquare size={15} /></button>}
        {!solo && desktop && !embedded && <button className="icon-btn" title="Open in its own window" onClick={() => desktop.openApp ? desktop.openApp(app.id) : null}><ExternalLink size={15} /></button>}
      </div>
      {rebuild && <div className="app-rebuild card stack-s">
        <textarea className="input" rows={3} autoFocus value={prompt} onChange={(e) => setPrompt(e.target.value)}
          placeholder={app.kind === "game" ? "What to change: harder enemies, a second level, touch controls… or leave as it was asked and use a better model" : "What to change: another chart, bigger buttons, a schedule…"} />
        <div className="row gap-s wrap">
          <ModelPicker value={model} onChange={setModel} />
          <label className="row gap-s small"><input type="checkbox" checked={fresh} onChange={(e) => setFresh(e.target.checked)} /> start from scratch (the versions so far stay)</label>
          <div className="grow" />
          <Button small kind="ghost" onClick={() => doRebuild(cur?.prompt || app.about || "the same app, better")} title="The original request again, with the model chosen here">Same request, this model</Button>
          <Button small kind="primary" busy={busy === "build"} disabled={!prompt.trim()} onClick={() => doRebuild()}>Build the new version</Button>
        </div>
      </div>}
      {asks.map((q) => <div key={q.id} className="app-ask"><span className="grow">{q.text}</span>
        {q.choices.map(([v, label]) => <Button key={v} small kind={v === "no" ? "ghost" : v === "once" ? "default" : "primary"} onClick={() => q.resolve(v)}>{label}</Button>)}</div>)}
      {cur?.preview?.problems?.length > 0 && !errors.length && <div className="app-errors"><Bug size={14} />
        <span className="grow ellipsis" title={cur.preview.problems.join("\n")}>When it was tried: {cur.preview.problems[0]}</span>
        <Button small onClick={() => doRebuild(`When the app was tried it showed these problems; fix them and keep everything else as it is:\n${cur.preview.problems.join("\n")}`)} busy={busy === "build"}>Ask the builder to fix it</Button></div>}
      {shared && <div className="app-ask share"><Share2 size={14} />
        <span className="grow">Written for the registry: <code>{shared.path}</code>. {shared.upload
          ? <>It is too long for a filled-in link: open the upload page and drop the file <code>{shared.file}</code> there, then press <b>Propose changes</b>.</>
          : <>Open the link, read it on GitHub and press <b>Propose changes</b>.</>} Nothing is sent before that; the registry reviews it.</span>
        <Button small kind="primary" onClick={() => openLink(shared.link)}>{shared.upload ? "Open the upload page" : "Open the link"}</Button>
        {desktop?.showItem && <Button small onClick={() => desktop.showItem(shared.file)}>Show the file</Button>}
        <button className="icon-btn" onClick={() => setShared(null)}><X size={13} /></button></div>}
      {errors.length > 0 && <div className="app-errors"><Bug size={14} /><span className="grow ellipsis" title={errors.join("\n")}>{errors[errors.length - 1]}</span>
        <Button small onClick={fixErrors} busy={busy === "build"}>Ask the builder to fix it</Button>
        <button className="icon-btn" onClick={() => setErrors([])}><X size={13} /></button></div>}
      {app.current ? <iframe key={`${app.current}-${frameKey}`} ref={frame} className="app-frame" src={app.frame} title={app.name}
          sandbox="allow-scripts allow-pointer-lock" allow="fullscreen; gamepad; autoplay"
          onLoad={() => { if (!embedded) frame.current?.focus(); }} />
        : <div className="app-empty">{app.building ? <><Spinner size={22} /><div>The builder is writing the first version…</div>
            {app.chat_id && <Button small onClick={() => go("chat", app.chat_id)}>Watch it in the chat</Button>}</>
          : <div>No version yet: press Rebuild to have one made.</div>}</div>}
      <div className="app-foot row gap-s wrap small">
        {app.kind === "device" && app.device_part && <Button small icon={<Cpu size={13} />} busy={busy === "install"} onClick={install}
          title="Put this version's device part on the device">Install on the device ({app.device_part.target})</Button>}
        {app.installed && <span className="muted">on the device: v{app.installed.version}, {String(app.installed.when).slice(0, 16).replace("T", " ")}</span>}
        <div className="grow" />
        {app.current && <Button small kind="ghost" icon={<FlaskConical size={13} />} busy={busy === "try"} onClick={tryIt}
          title="Run this version for a few seconds in a hidden browser (nothing reaches the device or a model) and see what it shows">Try it</Button>}
        <Button small kind="ghost" icon={<Upload size={13} />} onClick={exportApp} title="A .kimiapp file to install on another computer">Export</Button>
        {app.current && <Button small kind="ghost" icon={<Share2 size={13} />} busy={busy === "share"} onClick={share}
          title="Propose it to kimios-registry, where others can install it (you read it on GitHub first)">Share</Button>}
        {desktop?.appShortcut && !solo && (app.shortcut
          ? <Button small kind="ghost" icon={<Monitor size={13} />} onClick={() => shortcut(false)}>Remove from the computer's apps</Button>
          : <Button small kind="ghost" icon={<Monitor size={13} />} onClick={() => shortcut(true)} title="Open it from the computer's own apps menu, like any other app">Add to the computer's apps</Button>)}
        <ConfirmButton small icon={<Trash2 size={13} />} onConfirm={uninstall}>Uninstall</ConfirmButton>
      </div>
    </div>
  );
}

