import React, { useEffect, useState } from "react";
import {
  Activity as ActivityIcon, BookOpen, Brain, CalendarClock, Cpu, AudioLines, FolderKanban, Image as ImageIcon, MessageSquarePlus, Plug, Search,
  Settings as SettingsIcon, Workflow, Loader2, Pin, Rocket, WifiOff,
} from "lucide-react";
import { api } from "./api";
import { go, useApp } from "./store";
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
import Memory from "./pages/Memory";
import Settings from "./pages/Settings";
import Onboarding from "./pages/Onboarding";

const NAV = [
  { page: "activity", label: "Activity", icon: ActivityIcon },
  { page: "tasks", label: "Long-running tasks", icon: Rocket },
  { page: "schedules", label: "Schedules", icon: CalendarClock },
  { page: "images", label: "Images", icon: ImageIcon },
  { page: "sound", label: "Sound", icon: AudioLines },
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

function Sidebar() {
  const { route, projects, chats, jobs, modelStatus, connected } = useApp();
  const [q, setQ] = useState("");
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
      </div>
      <button className="new-chat" onClick={() => go("home")}><MessageSquarePlus size={16} /> New chat</button>
      <div className="search"><Search size={14} /><input placeholder="Search chats" value={q} onChange={(e) => setQ(e.target.value)} /></div>
      <div className="side-scroll">
        {!q && (
          <div className="side-section">
            <div className="side-title">
              <span>Projects</span>
              <button className="link" onClick={() => go("project", "new")}>+ New</button>
            </div>
            {projects.length === 0 && <div className="side-empty">Group chats, folders, instructions and memory.</div>}
            {projects.map((p: any) => (
              <div key={p.id} className={`side-item${route.page === "project" && route.id === p.id ? " active" : ""}`}
                onClick={() => go("project", p.id)}>
                <FolderKanban size={14} /><span className="ellipsis">{p.name}</span><span className="muted small">{p.chat_count || ""}</span>
              </div>
            ))}
          </div>
        )}
        {(!q || filtered.length > 0) && <div className="side-section">
          <div className="side-title"><span>{q ? "Chat titles" : "Chats"}</span></div>
          {filtered.slice(0, 80).map((c: any) => (
            <div key={c.id} className={`side-item${route.page === "chat" && route.id === c.id ? " active" : ""}`}
              onClick={() => go("chat", c.id)} title={c.title}>
              {c.running ? <Loader2 size={13} className="spin accent" /> : c.pinned ? <Pin size={12} /> : <span className="dot" />}
              <span className="ellipsis">{c.title || "New chat"}</span>
              {c.origin === "schedule" && <CalendarClock size={12} className="muted" />}
            </div>
          ))}
          {!q && looseChats.length === 0 && <div className="side-empty">Your chats appear here.</div>}
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
      </div>
      <nav className="side-nav">
        {NAV.map(({ page, label, icon: Icon }) => (
          <div key={page} className={`side-item${route.page === page ? " active" : ""}`} onClick={() => go(page)}>
            <Icon size={15} /><span>{label}</span>
            {page === "activity" && jobs.length > 0 && <Badge kind="accent">{jobs.length}</Badge>}
          </div>
        ))}
      </nav>
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

export default function App() {
  const { route, boot, bootError } = useApp();
  if (bootError && !boot) {
    return <div className="center-screen"><h2>Can't reach the engine</h2><p>{bootError}</p><p className="muted">If this persists, restart KimiLM. Logs are in the app's data folder.</p></div>;
  }
  if (!boot) return <div className="center-screen"><Spinner size={28} /><p className="muted">Starting…</p></div>;
  const needsOnboarding = !boot.settings?.onboarding_done && boot.models.length === 0;
  let page: React.ReactNode;
  if (needsOnboarding && !["models", "plugins", "settings"].includes(route.page)) page = <Onboarding />;
  else switch (route.page) {
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
    case "memory": page = <Memory />; break;
    case "settings": page = <Settings />; break;
    default: page = <Home projectId={route.page === "home" ? route.id : undefined} />;
  }
  return (
    <div className="shell">
      <Sidebar />
      <main className="main">{page}</main>
    </div>
  );
}
