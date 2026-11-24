// 2.34.0: Games: describe a game and a model builds it (2D on a canvas, 3D with three.js), with things that move and
// the computer as a player. Every game is an app (Apps): play it here, in its own window or from the computer's
// apps menu; rebuild it with another prompt or a better model, and the earlier versions stay.
import React, { useState } from "react";
import { Box, Gamepad2, Hammer, Sparkles, Square } from "lucide-react";
import { api } from "../api";
import { go, useApp, useDraft } from "../store";
import { Badge, Button, Empty, Field, PageHeader, Spinner } from "../components/ui";
import { ModelPicker } from "../components/Pickers";
import { AppTile, useApps } from "./Apps";

const IDEAS: [string, string][] = [
  ["2D", "A space shooter: my ship moves at the bottom, waves of enemy ships dodge and fire back, power-ups drop"],
  ["2D", "Air hockey against the computer, with a puck that bounces realistically and a goal light"],
  ["2D", "A platformer where a computer rival races me to the flag across three levels"],
  ["3D", "A marble maze I tilt with the arrow keys, with a timer and three mazes"],
  ["3D", "A boat race on a lake around buoys against three computer boats"],
  ["3D", "A snowball fight in a small village: computer kids hide behind walls and throw back"],
];

export default function Games() {
  const { toast } = useApp();
  const { apps } = useApps("game");
  const [idea, setIdea] = useDraft("games.idea", "");
  const [dims, setDims] = useDraft("games.dims", "2D");
  const [name, setName] = useDraft("games.name", "");
  const [model, setModel] = useDraft("games.model", "");
  const [busy, setBusy] = useState(false);
  const build = async () => {
    setBusy(true);
    try {
      const r = await api.post("/apps", { kind: "game", prompt: idea.trim(), dims, name: name.trim() || undefined, model: model || undefined });
      setIdea(""); setName("");
      toast(`Building ${r.app.name}: follow it in the chat; it opens in Apps when it is saved`, "ok");
      go("chat", r.chat_id);
    } catch (e: any) { toast(e.message, "error"); } finally { setBusy(false); }
  };
  const building = (apps || []).filter((a: any) => a.building);
  return (
    <div className="page">
      <PageHeader title="Games" subtitle="Describe a game and a model builds it: 2D or 3D, things that move, and the computer as your opponent. Play it here, in its own window or from the computer's apps menu." />
      <div className="card stack-s">
        <h3><Sparkles size={15} className="accent" /> A new game</h3>
        <textarea className="input" rows={3} value={idea} onChange={(e) => setIdea(e.target.value)}
          placeholder="What is the game? Who moves what, what does the computer do, how do you win?"
          onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && idea.trim()) build(); }} />
        <div className="row gap-s wrap">
          <div className="seg">
            <button className={`seg-btn${dims === "2D" ? " on" : ""}`} onClick={() => setDims("2D")}><Square size={13} /> 2D</button>
            <button className={`seg-btn${dims === "3D" ? " on" : ""}`} onClick={() => setDims("3D")}><Box size={13} /> 3D</button>
          </div>
          <input className="input" style={{ maxWidth: 220 }} placeholder="Name (optional)" value={name} onChange={(e) => setName(e.target.value)} />
          <ModelPicker value={model} onChange={setModel} />
          <div className="grow" />
          <Button kind="primary" icon={<Hammer size={14} />} busy={busy} disabled={!idea.trim()} onClick={build}>Build it</Button>
        </div>
        <div className="muted small">A game is written by the App builder in a chat you can follow and answer. A larger model writes better games; a small one on a processor may take a while. Every rebuild is a new version, and the old ones stay.</div>
        <div className="row gap-s wrap">{IDEAS.map(([d, text]) => <button key={text} className="chip" onClick={() => { setIdea(text); setDims(d); }}>{d} · {text.split(":")[0].split(",")[0]}</button>)}</div>
      </div>
      {building.length > 0 && <div className="card stack-s">
        <h3><Spinner size={14} /> Being built</h3>
        {building.map((a: any) => <div key={a.id} className="row gap-s"><b>{a.name}</b><span className="muted small grow">{a.building.prompt?.slice(0, 120)}</span>
          <Button small onClick={() => go("chat", a.building.chat_id)}>Open the chat</Button></div>)}
      </div>}
      <h2 style={{ marginTop: 22 }}><Gamepad2 size={18} /> Your games</h2>
      {apps === null ? <Spinner /> : apps.length === 0 ? <Empty icon={<Gamepad2 />} title="No games yet">Describe one above.</Empty> :
        <div className="app-grid">{apps.map((a: any) => <AppTile key={a.id} app={a} onOpen={() => go("apps", a.id)} />)}</div>}
      <div className="muted small" style={{ marginTop: 10 }}>Paddle Duel, Four in a Row, Orbit Racer and Tank Arena come with KimiLM as examples: open one and press Rebuild to have a model change it. <Badge>Games are apps</Badge></div>
    </div>
  );
}
