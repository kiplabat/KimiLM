// Pictures made by an image model: a row of thumbnails, and a large view with what made each one (2.11.0).
import React, { useState } from "react";
import { Copy, Download, FolderOpen, Wand2 } from "lucide-react";
import { desktop, fileUrl } from "../api";
import { go, useApp } from "../store";
import { Button, Modal } from "./ui";

export const EDIT_KEY = "kimilm-picture-to-change";

function facts(p: any) {
  const a = p.params || {};
  return [p.model_name || p.model, a.width && a.height ? `${a.width}×${a.height}` : "", a.seed != null ? `seed ${a.seed}` : "",
    a.steps ? `${a.steps} steps` : "", a.seconds ? `${a.seconds} s` : "", a.cost ? `about US$${a.cost}` : "",
    p.source === "online" ? "made online" : "made on this computer"].filter(Boolean).join(" · ");
}

export function PictureView({ item, onClose, onChange }: any) {
  const { toast } = useApp();
  const change = onChange || ((p: any) => { sessionStorage.setItem(EDIT_KEY, p.path); go("images"); });
  return (
    <Modal wide title={item.name} onClose={onClose}
      footer={<>
        <Button icon={<Copy size={14} />} onClick={() => { navigator.clipboard?.writeText(item.prompt || ""); toast("Prompt copied", "ok"); }}>Copy prompt</Button>
        <Button icon={<Wand2 size={14} />} onClick={() => { onClose(); change(item); }}>Change this picture</Button>
        {desktop && <Button icon={<FolderOpen size={14} />} onClick={() => desktop.showItem(item.path)}>Show in folder</Button>}
        <a className="btn btn-primary" href={fileUrl(item.path)} download={item.name}><Download size={14} /><span>Download</span></a>
      </>}>
      <div className="picture-large"><img src={fileUrl(item.path)} alt={item.prompt || item.name} /></div>
      {item.prompt && <p className="picture-prompt">{item.prompt}</p>}
      <div className="small muted">{facts(item)}</div>
      <div className="small muted ellipsis" title={item.path}>{item.path}</div>
    </Modal>
  );
}

export function PictureGrid({ items, large, onChange }: any) {
  const [open, setOpen] = useState<any>(null);
  if (!items?.length) return null;
  return (
    <div className={`pictures${large ? " pictures-large" : ""}`}>
      {items.map((p: any) => (
        <figure key={p.id || p.path} className="picture" onClick={() => setOpen(p)} title={p.prompt || p.name}>
          <img src={fileUrl(p.path)} alt={p.prompt || p.name} loading="lazy" />
          <a className="picture-save" href={fileUrl(p.path)} download={p.name} title="Download" onClick={(e) => e.stopPropagation()}><Download size={14} /></a>
        </figure>
      ))}
      {open && <PictureView item={open} onClose={() => setOpen(null)} onChange={onChange} />}
    </div>
  );
}
