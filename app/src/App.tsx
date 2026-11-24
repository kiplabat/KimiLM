import React, { useEffect, useRef, useState } from "react";
import {
  Activity as ActivityIcon, BookOpen, Brain, CalendarClock, Cpu, AudioLines, FolderKanban, Image as ImageIcon, MessageSquarePlus, Plug, Search,
  Settings as SettingsIcon, Workflow, Loader2, Pin, Rocket, WifiOff, Film, ChevronDown, ChevronRight, Gauge, ChartColumn, Usb,
  LayoutGrid, Gamepad2,
} from "lucide-react";
import { api } from "./api";
import { go, Shown, useApp } from "./store";
import { Badge, Spinner } from "./components/ui";
import Home from "./pages/Home";
import ChatView from "./pages/ChatView";
import ProjectView from "./pages/ProjectView";
import Models from "./pages/Models";
import Plugins from "./pages/Plugins";
import Skills from "./pages/Skills";
import Harnesses from "./pages/Harnesses";
import Schedules from "./pages/Schedules";
import ActivityPage from "./pages/Activity";
import Missions from "./pages/Missions";
import Images from "./pages/Images";
import Sound from "./pages/Sound";
import Video from "./pages/Video";
import Memory from "./pages/Memory";
import Settings from "./pages/Settings";
import Onboarding from "./pages/Onboarding";
import Performance from "./pages/Performance";
import Usage from "./pages/Usage";
import { Handle, useSize } from "./components/Resizable";
import { NoticeBell } from "./components/Notices";
import Devices from "./pages/Devices";
import Apps from "./pages/Apps";
import Games from "./pages/Games";
import AppRunner from "./components/AppRunner";

const NAV = [
  { page: "activity", label: "Activity", icon: ActivityIcon },
  { page: "performance", label: "Performance", icon: Gauge },
  { page: "usage", label: "Usage", icon: ChartColumn },
  { page: "apps", label: "Apps", icon: LayoutGrid },
  { page: "games", label: "Games", icon: Gamepad2 },
  { page: "devices", label: "Devices", icon: Usb },
  { page: "tasks", label: "Long-running tasks", icon: Rocket },
  { page: "schedules", label: "Schedules", icon: CalendarClock },
  { page: "images", label: "Images", icon: ImageIcon },
  { page: "sound", label: "Sound", icon: AudioLines },
  { page: "video", label: "Video", icon: Film },
  { page: "models", label: "Models", icon: Cpu },
  { page: "plugins", label: "Plugins & connectors", icon: Plug },
  { page: "skills", label: "Skills", icon: BookOpen },
  { page: "harnesses", label: "Harnesses", icon: Workflow },
  { page: "memory", label: "Memory", icon: Brain },
  { page: "settings", label: "Settings", icon: SettingsIcon },
];

function highlight(snippet: string) {
  const esc = (snippet || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  return esc.replace(/\*\*(.+?)\*\*/g, "<mark>$1</mark>");
}

// 2.21.0: the sidebar scrolls as one, projects, chats and the pages below them. Projects and Chats fold by their
// title (remembered), and Chats shows the most recent CHATS_SHOWN until "Show all", so the pages stay within reach.
const CHATS_SHOWN = 20;
function remembered(key: string, initial: boolean): [boolean, (v: boolean) => void] {
  const read = () => { try { const v = localStorage.getItem(key); return v === null ? initial : v === "1"; } catch { return initial; } };
  const [v, setV] = useState(read);
  return [v, (x: boolean) => { setV(x); try { localStorage.setItem(key, x ? "1" : "0"); } catch {} }];
}

function Fold({ open, onToggle, label }: any) {
  return <span className="side-fold" onClick={onToggle} title={open ? "Fold" : "Unfold"}>
    {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}{label}</span>;
}

function Sidebar() {
  const { route, projects, chats, jobs, modelStatus, connected } = useApp();
  const [q, setQ] = useState("");
  const [projectsOpen, setProjectsOpen] = remembered("kimilm.side.projects", true);
  const [chatsOpen, setChatsOpen] = remembered("kimilm.side.chats", true);
  const [allChats, setAllChats] = useState(false);
  const [found, setFound] = useState<any[] | null>(null);
  const looseChats = chats.filter((c: any) => !c.project_id);
  const filtered = q ? chats.filter((c: any) => (c.title || "").toLowerCase().includes(q.toLowerCase())) : looseChats;
  useEffect(() => {      // search inside chats too (words, and meaning once the search model is installed)
    setFound(null);
    if (q.trim().length < 2) return;
    const t = setTimeout(() => api.get(`/search?q=${encodeURIComponent(q)}&limit=25`).then((r) => setFound(r.results)).catch(() => setFound([])), 300);
    return () => clearTimeout(t);
  }, [q]);
  const titleIds = new Set(filtered.map((c: any) => c.id));
  const inside = (found || []).filter((r: any) => !titleIds.has(r.id));
  return (
    <aside className="sidebar">
      <div className="brand" onClick={() => go("home")}>
        <img src="./icon.png" alt="" /> <span>KimiLM</span>
        <NoticeBell />
      </div>
      <button className="new-chat" onClick={() => go("home")}><MessageSquarePlus size={16} /> New chat</button>
      <div className="search"><Search size={14} /><input placeholder="Search chats" value={q} onChange={(e) => setQ(e.target.value)} /></div>
      <div className="side-scroll">
        {!q && (
          <div className="side-section">
            <div className="side-title">
              <Fold open={projectsOpen} onToggle={() => setProjectsOpen(!projectsOpen)} label={`Projects${!projectsOpen && projects.length ? ` (${projects.length})` : ""}`} />
              <button className="link" onClick={() => go("project", "new")}>+ New</button>
            </div>
            {projectsOpen && projects.length === 0 && <div className="side-empty">Group chats, folders, instructions and memory.</div>}
            {projectsOpen && projects.map((p: any) => (
              <div key={p.id} className={`side-item${route.page === "project" && route.id === p.id ? " active" : ""}`}
                onClick={() => go("project", p.id)}>
                <FolderKanban size={14} /><span className="ellipsis">{p.name}</span><span className="muted small">{p.chat_count || ""}</span>
              </div>
            ))}
          </div>
        )}
        {(!q || filtered.length > 0) && <div className="side-section">
          <div className="side-title">{q ? <span>Chat titles</span> :
            <Fold open={chatsOpen} onToggle={() => setChatsOpen(!chatsOpen)} label={`Chats${!chatsOpen && looseChats.length ? ` (${looseChats.length})` : ""}`} />}</div>
          {(q || chatsOpen) && filtered.slice(0, q ? 80 : allChats ? 300 : CHATS_SHOWN).map((c: any) => (
            <div key={c.id} className={`side-item${route.page === "chat" && route.id === c.id ? " active" : ""}`}
              onClick={() => go("chat", c.id)} title={c.title}>
              {c.running ? <Loader2 size={13} className="spin accent" /> : c.pinned ? <Pin size={12} /> : <span className="dot" />}
              <span className="ellipsis">{c.title || "New chat"}</span>
              {c.origin === "schedule" && <CalendarClock size={12} className="muted" />}
            </div>
          ))}
          {!q && chatsOpen && looseChats.length === 0 && <div className="side-empty">Your chats appear here.</div>}
          {!q && chatsOpen && looseChats.length > CHATS_SHOWN && <div className="side-more" onClick={() => setAllChats(!allChats)}>
            {allChats ? "Show fewer" : `Show all ${looseChats.length} chats`}</div>}
        </div>}
        {q.trim().length >= 2 && (
          <div className="side-section">
            <div className="side-title"><span>Inside chats</span>{found === null && <Spinner size={12} />}</div>
            {found !== null && inside.length === 0 && <div className="side-empty">No other chat mentions this.</div>}
            {inside.map((r: any) => (
              <div key={r.id} className={`side-item side-hit${route.page === "chat" && route.id === r.id ? " active" : ""}`}
                onClick={() => go("chat", r.id)} title={r.title}>
                <div className="ellipsis"><b>{r.title || "New chat"}</b>{r.project_name ? <span className="muted"> · {r.project_name}</span> : null}</div>
                {r.matches?.[0] && <div className="side-snippet" dangerouslySetInnerHTML={{ __html: highlight(r.matches[0].snippet) }} />}
              </div>
            ))}
          </div>
        )}
        <nav className="side-nav">
          {NAV.map(({ page, label, icon: Icon }) => (
            <div key={page} className={`side-item${route.page === page ? " active" : ""}`} onClick={() => go(page)}>
              <Icon size={15} /><span>{label}</span>
              {page === "activity" && jobs.length > 0 && <Badge kind="accent">{jobs.length}</Badge>}
            </div>
          ))}
        </nav>
      </div>
      <div className="model-chip" onClick={() => go("models")}
        title={modelStatus.error || "Model status (models unload after a while to free memory)"}>
        {!connected ? <><WifiOff size={13} /> Reconnecting to the engine…</> :
          modelStatus.state === "loading" ? <><Spinner size={13} /> Loading {modelStatus.model_id}…</> :
          modelStatus.state === "ready" ? <><span className="live-dot" /> {modelStatus.model_id} · {String(modelStatus.compute || "").toUpperCase()}</> :
          modelStatus.state === "failed" ? <><span className="err-dot" /> Model failed to load</> :
          <><span className="idle-dot" /> No model loaded</>}
      </div>
    </aside>
  );
}

// 2.21.0: a screen you leave is kept, not rebuilt. Before, every screen was made afresh on each visit: what you had
// typed was gone, and a planner setting up a long-running task finished for a screen that no longer existed. Kept
// screens stay in the order they were first opened (moving one would reset it) and are hidden; the least recently
// seen go when there are more than KEEP (chats: KEEP_CHATS, they can be long).
const KEEP = 12, KEEP_CHATS = 6;
const SOLO = new URLSearchParams(location.search).get("app") || "";    // 2.34.0: kimilm --open-app=<id>
const SIDE_MIN = 200, SIDE_MAX = 560;
// one kept screen per chat or project (2.34.0: and per open app, which keeps running while you look elsewhere)
const PER_ID = ["chat", "project", "home", "apps"];

function slotKey(page: string, id?: string) {
  return PER_ID.includes(page) ? `${page}:${id || ""}` : page;
}

function useKept(key: string, page: React.ReactNode) {
  const kept = useRef(new Map<string, React.ReactNode>());     // in the order first opened
  const recent = useRef<string[]>([]);                          // most recent last
  if (!key) return [];
  kept.current.set(key, page);                                  // the current screen gets the current route
  recent.current = [...recent.current.filter((k) => k !== key), key];
  const drop = (k: string) => { kept.current.delete(k); recent.current = recent.current.filter((x) => x !== k); };
  for (const k of recent.current.filter((x) => x.startsWith("chat:")).slice(0, -KEEP_CHATS)) drop(k);
  for (const k of recent.current.slice(0, -KEEP)) drop(k);
  return [...kept.current.entries()];
}

export default function App() {
  const { route, boot, bootError } = useApp();
  const needsOnboarding = !!boot && !boot.settings?.onboarding_done && boot.models.length === 0;
  const onboarding = needsOnboarding && !["models", "plugins", "settings"].includes(route.page);
  let page: React.ReactNode = null;
  if (boot && onboarding) page = <Onboarding />;
  else if (boot) switch (route.page) {
    case "chat": page = <ChatView key={route.id} chatId={route.id} />; break;
    case "project": page = <ProjectView key={route.id} projectId={route.id} />; break;
    case "models": page = <Models tab={route.id} />; break;
    case "plugins": page = <Plugins tab={route.id} />; break;
    case "skills": page = <Skills selected={route.id} />; break;
    case "harnesses": page = <Harnesses selected={route.id} />; break;
    case "schedules": page = <Schedules />; break;
    case "activity": page = <ActivityPage />; break;
    case "tasks": page = <Missions />; break;
    case "images": page = <Images />; break;
    case "sound": page = <Sound />; break;
    case "video": page = <Video tab={route.id} />; break;
    case "memory": page = <Memory />; break;
    case "settings": page = <Settings section={route.id} />; break;
    case "performance": page = <Performance />; break;
    case "usage": page = <Usage />; break;
    case "devices": page = <Devices />; break;
    case "apps": page = <Apps openId={route.id} />; break;
    case "games": page = <Games />; break;
    default: page = <Home projectId={route.page === "home" ? route.id : undefined} />;
  }
  const key = onboarding ? "onboarding" : slotKey(route.page, route.id);
  const slots = useKept(boot ? key : "", page);
  const [sideW, setSideW, resetSide] = useSize("sidebar", 268, SIDE_MIN, SIDE_MAX);   // 2.27.0
  if (bootError && !boot) {
    return <div className="center-screen"><h2>Can't reach the engine</h2><p>{bootError}</p><p className="muted">If this persists, restart KimiLM. Logs are in the app's data folder.</p></div>;
  }
  if (!boot) return <div className="center-screen"><Spinner size={28} /><p className="muted">Starting…</p></div>;
  if (SOLO) return <div className="solo-shell"><AppRunner appId={SOLO} solo /></div>;      // 2.34.0: an app in its own window
  return (
    <div className="shell" style={{ ["--side-w" as any]: `${sideW}px` }}>
      <Sidebar />
      <Handle axis="x" className="shell-handle" value={sideW} min={SIDE_MIN} max={SIDE_MAX} onReset={resetSide}
        onDrag={(d, start) => setSideW(start + d)} onStep={(d) => setSideW(sideW + d)}
        label="Drag to make the sidebar wider or narrower" />
      <main className="main">
        {slots.map(([k, el]) => (
          <Shown.Provider key={k} value={k === key}>
            <div className={`slot${k === key ? "" : " slot-off"}`} aria-hidden={k !== key}>{el}</div>
          </Shown.Provider>
        ))}
      </main>
    </div>
  );
}
