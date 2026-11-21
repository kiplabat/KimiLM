import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { api, connectEvents, desktop, onEvent } from "./api";
import { NoticeStack } from "./components/Notices";

export type Route = { page: string; id?: string; tab?: string };

function parseHash(): Route {
  const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);
  return { page: parts[0] || "home", id: parts[1], tab: parts[2] };
}

export function go(page: string, id?: string, tab?: string) {
  location.hash = "/" + [page, id, tab].filter(Boolean).map((x) => encodeURIComponent(x as string)).join("/");
}

type Toast = { id: number; text: string; kind?: "ok" | "error" | "info" };
// 2.29.0: notifications (finished, needs you, a device came or went): on screen with what you can do about them,
// in the list under the bell, and on the desktop when the window is not in front
export type Note = { id: number; seq?: number; title: string; body?: string; detail?: string; level: string; chat_id?: string;
  approval_id?: string; device_id?: string; time: number; read?: boolean; shown?: boolean };
const STARTED = Date.now();

function chime() {        // two soft notes: something needs you
  try {
    const ctx = new (window.AudioContext || (window as any).webkitAudioContext)();
    [660, 880].forEach((f, i) => {
      const o = ctx.createOscillator(), g = ctx.createGain();
      o.frequency.value = f; o.type = "sine";
      g.gain.setValueAtTime(0.0001, ctx.currentTime + i * 0.16);
      g.gain.exponentialRampToValueAtTime(0.12, ctx.currentTime + i * 0.16 + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + i * 0.16 + 0.3);
      o.connect(g); g.connect(ctx.destination); o.start(ctx.currentTime + i * 0.16); o.stop(ctx.currentTime + i * 0.16 + 0.32);
    });
    setTimeout(() => ctx.close(), 900);
  } catch {}
}

const Ctx = createContext<any>(null);
export const useApp = () => useContext(Ctx);

// 2.21.0: screens are kept when you go elsewhere (App.tsx), so what you typed, a planner at work and the place you
// had scrolled to are still there when you come back. A kept screen is hidden: it knows it through this context.
export const Shown = createContext(true);
export const useShown = () => useContext(Shown);

// A draft kept outside any screen, for as long as the app is open: like useState, but a screen made again finds it,
// and work that ends while its screen is gone (a planner's proposal) still lands in it.
const drafts = new Map<string, any>();
export function useDraft<T>(key: string, initial: T): [T, (v: T | ((cur: T) => T)) => void] {
  const [value, setValue] = useState<T>(() => (drafts.has(key) ? drafts.get(key) : initial));
  const first = useRef(initial);
  const set = useCallback((v: any) => {
    const cur = drafts.has(key) ? drafts.get(key) : first.current;
    const next = typeof v === "function" ? v(cur) : v;
    drafts.set(key, next);
    setValue(next);
  }, [key]);
  return [value, set];
}
export function clearDrafts(prefix: string) {
  for (const k of [...drafts.keys()]) if (k.startsWith(prefix)) drafts.delete(k);
}

export function useEvent(handler: (e: any) => void, deps: any[] = []) {
  const ref = useRef(handler);
  ref.current = handler;
  useEffect(() => onEvent((e) => ref.current(e)), deps);
}

export function AppProvider({ children }: { children: React.ReactNode }) {
  const [route, setRoute] = useState<Route>(parseHash());
  const [boot, setBoot] = useState<any>(null);
  const [bootError, setBootError] = useState<string>("");
  const [projects, setProjects] = useState<any[]>([]);
  const [chats, setChats] = useState<any[]>([]);
  const [jobs, setJobs] = useState<any[]>([]);
  const [connected, setConnected] = useState(true);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [modelStatus, setModelStatus] = useState<any>({ state: "idle" });
  const [downloads, setDownloads] = useState<Record<string, any>>({});
  const [notes, setNotes] = useState<Note[]>([]);
  const routeRef = useRef(route);
  routeRef.current = route;
  const bootRef = useRef<any>(null);
  bootRef.current = boot;
  const dismiss = useCallback((id: number) => setNotes((n) => n.map((x) => x.id === id ? { ...x, shown: false, read: true } : x)), []);
  const markAllRead = useCallback(() => setNotes((n) => n.map((x) => ({ ...x, read: true, shown: false }))), []);
  const notify = useCallback((e: any) => {
    const watching = e.chat_id && routeRef.current.page === "chat" && routeRef.current.id === e.chat_id && !document.hidden && document.hasFocus();
    if (e.level === "done" && watching) return;                       // you saw it finish
    const old = e.time && e.time * 1000 < STARTED - 3000;               // from before this window opened
    const id = e.seq || Date.now() + Math.random();
    const note: Note = { id, seq: e.seq, title: e.title, body: e.body, detail: e.detail, level: e.level || "info", chat_id: e.chat_id,
      approval_id: e.approval_id, device_id: e.device_id, time: (e.time || Date.now() / 1000), read: !!old, shown: !old };
    setNotes((n) => (n.some((x) => x.id === id) ? n : [...n, note]).slice(-60));
    if (old) return;
    if (note.level !== "action") setTimeout(() => setNotes((n) => n.map((x) => x.id === id ? { ...x, shown: false } : x)), note.level === "device" ? 7000 : 10000);
    if (desktop && (document.hidden || !document.hasFocus())) desktop.notify(e.title, e.body || "", { chatId: e.chat_id, level: note.level });
    if (note.level === "action" && bootRef.current?.settings?.notify_sound !== false) chime();
  }, []);

  const toast = useCallback((text: string, kind: Toast["kind"] = "info") => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, text, kind }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), kind === "error" ? 7000 : 4000);
  }, []);

  const refreshBoot = useCallback(async () => {
    try {
      const b = await api.get("/bootstrap");
      setBoot(b);
      setModelStatus(b.runtime.status);
      setBootError("");
    } catch (e: any) { setBootError(e.message); }
  }, []);
  const refreshProjects = useCallback(async () => setProjects((await api.get("/projects")).projects), []);
  const refreshChats = useCallback(async () => setChats((await api.get("/chats?limit=300")).chats), []);
  const refreshJobs = useCallback(async () => setJobs((await api.get("/jobs?status=active")).jobs), []);

  useEffect(() => {
    const onHash = () => setRoute(parseHash());
    window.addEventListener("hashchange", onHash);
    refreshBoot(); refreshProjects(); refreshChats(); refreshJobs();
    api.get("/downloads").then((r) => setDownloads(Object.fromEntries(r.downloads.map((d: any) => [d.id, d])))).catch(() => {});
    api.get("/notifications").then((r) => (r.notifications || []).forEach((e: any) => notify(e))).catch(() => {});
    const off = desktop?.onOpen?.((where: any) => { if (where?.chatId) go("chat", where.chatId); });
    connectEvents(setConnected);
    return () => { window.removeEventListener("hashchange", onHash); off?.(); };
  }, []);

  useEvent((e) => {
    if (e.kind === "chats_changed") refreshChats();
    if (e.kind === "projects_changed") refreshProjects();
    if (e.kind === "job") { refreshJobs(); if (["done", "failed", "cancelled"].includes(e.status)) refreshChats(); }
    if (e.kind === "model_status") setModelStatus(e);
    if (e.kind === "models_changed" || e.kind === "settings_changed" || e.kind === "harnesses_changed") refreshBoot();
    if (e.kind === "download") {
      setDownloads((d) => { const n = { ...d }; if (e.state === "removed") delete n[e.id]; else n[e.id] = e; return n; });
      if (e.state === "done") { toast(`Downloaded ${e.name}`, "ok"); refreshBoot(); }
      if (e.state === "failed") toast(`Download failed: ${e.error}`, "error");
    }
    if (e.kind === "notify") notify(e);
    if (e.kind === "chat" && (e.type === "approval_done" || e.type === "agent_approval_done"))    // answered elsewhere
      setNotes((n) => n.map((x) => x.approval_id === e.approval_id ? { ...x, shown: false, read: true, level: "info" } : x));
    if (e.kind === "runtime" && e.action === "installed") { toast(`Installed the ${e.backend} runtime`, "ok"); refreshBoot(); }
    if (e.kind === "runtime" && e.action === "install_failed") toast(`Runtime install failed: ${e.error}`, "error");
    if (e.kind === "image_engine" && e.action === "installed") toast("The image engine is installed", "ok");
    if (e.kind === "image_engine" && e.action === "install_failed") toast(`The image engine could not be installed: ${e.error}`, "error");
    if (e.kind === "image_download" && e.state === "done") toast(`Downloaded ${e.name}: it can make ${/video/i.test(e.name) ? "clips" : "pictures"} now`, "ok");
    if (e.kind === "image_download" && e.state === "failed") toast(`${e.name} could not be downloaded: ${e.error}`, "error");
    if (e.kind === "sound_download" && e.state === "done") toast(`Downloaded ${e.name}`, "ok");
    if (e.kind === "music_engine" && e.action === "installed") toast("The music engine is installed", "ok");
    if (e.kind === "sound_job" && e.state === "done" && e.result?.params?.mode === "podcast") toast(`Your podcast “${e.result.params.title}” is ready`, "ok");
    if (e.kind === "video_job" && e.state === "done" && e.result?.path) toast(`Your video “${e.result.params?.title || e.result.name}” is ready`, "ok");
    if (e.kind === "video_job" && e.state === "failed" && e.what !== "storyboard") toast(`The video could not be made: ${e.error}`, "error");
    if (e.kind === "sound_download" && e.state === "failed") toast(`${e.name} could not be downloaded: ${e.error}`, "error");
  });

  const value = {
    route, boot, bootError, refreshBoot, projects, refreshProjects, chats, refreshChats, jobs, refreshJobs,
    connected, toast, modelStatus, downloads, setDownloads, notes, dismiss, markAllRead,
  };
  return (
    <Ctx.Provider value={value}>
      {children}
      <div className="toasts">
        <NoticeStack notes={notes.filter((n) => n.shown)} dismiss={dismiss} />
        {toasts.map((t) => <div key={t.id} className={`toast toast-${t.kind}`}>{t.text}</div>)}
      </div>
    </Ctx.Provider>
  );
}
