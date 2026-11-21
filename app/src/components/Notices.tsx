import React, { useEffect, useRef, useState } from "react";
import { Bell, Check, CheckCircle2, CircleAlert, Hand, Info, Plug, X } from "lucide-react";
import { api } from "../api";
import { go, useApp, type Note } from "../store";

// 2.29.0: notifications on screen. Something that needs you stays until you answer it (an approval can be answered
// right here); "done" and device notes go away by themselves. Every one is also kept under the bell.

const ICON: Record<string, any> = { action: Hand, done: CheckCircle2, failed: CircleAlert, device: Plug, info: Info };

function when(t: number) {
  const s = Math.max(0, Date.now() / 1000 - t);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return new Date(t * 1000).toLocaleDateString();
}

function open(n: Note) {
  if (n.chat_id) go("chat", n.chat_id);
  else if (n.device_id) go("devices");
}

function NoteCard({ n, dismiss, compact }: { n: Note; dismiss: (id: number) => void; compact?: boolean }) {
  const { toast } = useApp();
  const Icon = ICON[n.level] || Info;
  const decide = async (allow: boolean, always = false) => {
    try { await api.post(`/approvals/${n.approval_id}`, { allow, always }); dismiss(n.id); }
    catch (e: any) { toast(e.message, "error"); dismiss(n.id); }
  };
  return (
    <div className={`note note-${n.level}${compact ? " note-compact" : ""}${n.read ? " note-read" : ""}`} role={n.level === "action" ? "alert" : "status"}>
      <Icon size={16} className="note-icon" />
      <div className="note-main" onClick={() => { open(n); if (n.level !== "action" || !n.approval_id) dismiss(n.id); }}>
        <div className="note-title">{n.title}</div>
        {n.body && <div className="note-body">{n.body}</div>}
        {!compact && n.detail && n.detail !== n.body && <div className="note-detail">{n.detail}</div>}
        {compact && <div className="note-when">{when(n.time)}</div>}
      </div>
      {!compact && n.level === "action" && n.approval_id && (
        <div className="note-actions">
          <button className="btn btn-primary btn-sm" onClick={() => decide(true)}><Check size={13} /> Allow</button>
          <button className="btn btn-default btn-sm" onClick={() => decide(true, true)} title="Allow this kind of action in that chat from now on">Always</button>
          <button className="btn btn-ghost btn-sm" onClick={() => decide(false)}>Decline</button>
        </div>
      )}
      {!compact && <button className="note-close" aria-label="Dismiss" onClick={() => dismiss(n.id)}><X size={14} /></button>}
    </div>
  );
}

export function NoticeStack({ notes, dismiss }: { notes: Note[]; dismiss: (id: number) => void }) {
  return <>{notes.slice(-4).map((n) => <NoteCard key={n.id} n={n} dismiss={dismiss} />)}</>;
}

export function NoticeBell() {
  const { notes, dismiss, markAllRead, route } = useApp();
  const [openList, setOpen] = useState(false);
  useEffect(() => setOpen(false), [route.page, route.id]);
  const box = useRef<HTMLDivElement>(null);
  const unread = (notes as Note[]).filter((n) => !n.read).length;
  const needs = (notes as Note[]).filter((n) => !n.read && n.level === "action").length;
  useEffect(() => {
    if (!openList) return;
    const close = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [openList]);
  return (
    <div className="bell" ref={box} onClick={(e) => e.stopPropagation()}>
      <button className={`bell-btn${needs ? " bell-needs" : ""}`} title={unread ? `${unread} new notification${unread === 1 ? "" : "s"}` : "Notifications"}
        aria-label="Notifications" onClick={() => setOpen(!openList)}>
        <Bell size={15} />{unread > 0 && <span className="bell-count">{unread > 9 ? "9+" : unread}</span>}
      </button>
      {openList && (
        <div className="bell-list">
          <div className="row between bell-head"><b>Notifications</b>{notes.length > 0 && <button className="link" onClick={markAllRead}>Mark all read</button>}</div>
          {notes.length === 0 && <div className="muted small bell-empty">Nothing yet. You are told here when work finishes, when something needs you and when a device is plugged in.</div>}
          {[...(notes as Note[])].reverse().map((n) => <NoteCard key={n.id} n={n} dismiss={dismiss} compact />)}
        </div>
      )}
    </div>
  );
}
