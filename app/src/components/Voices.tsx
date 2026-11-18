// Your own voice (2.18.0): read eight sentences aloud, each take checked at once. The voice model on this computer
// takes a voice from about one second of speech, so the voice says a sentence starting from each take and the user
// chooses, by ear, the one that sounds most like them. The voice keeps its takes and can be heard, renamed,
// extended, recorded again take by take, and deleted with all that belongs to it.
import React, { useEffect, useRef, useState } from "react";
import { Check, Mic, RotateCcw, Square, Star, Trash2, Upload, UserRound, Volume2 } from "lucide-react";
import { api, fileUrl, fmtTime } from "../api";
import { useApp, useEvent } from "../store";
import { Badge, Button, ConfirmButton, Field, Modal, Spinner } from "./ui";
import { RecordButton, useRecorder } from "./Audio";

const VERDICT: any = { good: ["ok", "good"], fair: ["warn", "usable"], again: ["bad", "record it again"] };
const secs = (s?: number) => (s == null ? "" : `${Math.round(s * 10) / 10} s`);
const mmss = (s: number) => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`;
const discard = (paths: (string | undefined)[]) => { const p = paths.filter(Boolean); if (p.length) api.post("/sound/voices/discard", { paths: p }).catch(() => {}); };

// What the check said about a take.
function Verdict({ check }: { check: any }) {
  if (!check?.verdict) return null;
  const [kind, word] = VERDICT[check.verdict] || ["neutral", check.verdict];
  return (
    <div className="stack-s">
      <div className="row gap-s wrap"><Badge kind={kind}>{word}</Badge>
        <span className="small muted">{secs(check.seconds)}{check.level_db != null && check.level_db > -100 ? ` · loudness ${Math.round(check.level_db)} dB` : ""}{check.match != null ? ` · ${Math.round(check.match * 100)}% of the words heard` : ""}</span></div>
      {(check.problems || []).map((p: string, i: number) => <div key={`p${i}`} className="small bad">{p}</div>)}
      {(check.notes || []).map((p: string, i: number) => <div key={`n${i}`} className="small muted">{p}</div>)}
    </div>
  );
}

// One sentence, recorded: Record, Stop, the check, listen, again. `take` is {audio, check} and belongs to the caller.
export function TakeRecorder({ sentence, take, onTake }: { sentence: any; take: any; onTake: (t: any) => void }) {
  const { toast } = useApp();
  const r = useRecorder();
  const [busy, setBusy] = useState(false);
  const click = async () => {
    try {
      if (!r.recording) return await r.start();
      const file = await r.stop();
      if (!file) return;
      setBusy(true);
      const up = await api.upload("/sound/upload", file);
      const check = await api.post("/sound/voices/check", { audio: up.path, sentence: sentence.id, text: sentence.id ? undefined : sentence.text });
      discard([take?.audio]);                    // the attempt before this one is not kept
      onTake({ audio: up.path, check });
    } catch (e: any) { toast(e.name === "NotAllowedError" ? "The microphone is not allowed for this app" : e.message, "error"); }
    setBusy(false);
  };
  return (
    <div className="stack-s">
      <div className="row gap-s wrap">
        <button className={`btn btn-${r.recording || !take ? "primary" : "default"}`} onClick={click} disabled={busy}>
          {r.recording ? <Square size={14} /> : take ? <RotateCcw size={14} /> : <Mic size={14} />}
          <span>{r.recording ? `Stop (${mmss(r.seconds)})` : take ? "Record it again" : "Record"}</span>
        </button>
        {r.recording && <span className="small muted">Read the sentence, then press Stop.</span>}
        {busy && <span className="note"><Spinner size={14} /> Checking the take…</span>}
        {take && !busy && !r.recording && <audio controls preload="metadata" src={fileUrl(take.audio)} />}
      </div>
      {take && !busy && <Verdict check={take.check} />}
    </div>
  );
}

// A new voice, sentence by sentence.
export function GuidedVoice({ onClose }: { onClose: (made?: any) => void }) {
  const { toast } = useApp();
  const [script, setScript] = useState<any>(null);
  const [step, setStep] = useState(-1);
  const [takes, setTakes] = useState<Record<string, any>>({});
  const [name, setName] = useState("");
  const [whose, setWhose] = useState("mine");
  const [confirmed, setConfirmed] = useState(false);
  const [person, setPerson] = useState("");
  const [proof, setProof] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const proofFile = useRef<HTMLInputElement>(null);
  const latest = useRef(takes);
  latest.current = takes;
  useEffect(() => { api.get("/sound/voices/script").then(setScript).catch((e) => toast(e.message, "error")); }, []);
  const close = () => { discard(Object.values(latest.current).map((t: any) => t.audio)); onClose(); };     // nothing tried stays behind
  if (!script) return <Modal title="Record your voice" onClose={close}><Spinner /></Modal>;
  const sentences: any[] = script.sentences, n = sentences.length;
  const usable = sentences.filter((s) => takes[s.id] && takes[s.id].check.verdict !== "again");
  const whoOk = whose === "mine" ? confirmed : person.trim() && proof;
  const make = async () => {
    setBusy(true);
    try {
      const voice = await api.post("/sound/voices", {
        name, takes: usable.map((s) => ({ sentence: s.id, audio: takes[s.id].audio, check: takes[s.id].check })),
        consent: whose === "mine" ? { whose, confirmed } : { whose, person, consent_audio: proof?.path } });
      discard(sentences.filter((s) => takes[s.id] && !usable.includes(s)).map((s) => takes[s.id].audio));     // the ones left out
      onClose(voice);
    } catch (e: any) { toast(e.message, "error"); }
    setBusy(false);
  };
  const s = step >= 0 && step < n ? sentences[step] : null;
  const footer = step < 0 ? <Button kind="primary" disabled={!name.trim()} onClick={() => setStep(0)}>Start</Button>
    : s ? <>
      <Button onClick={() => setStep(step - 1)}>Back</Button>
      {!takes[s.id] && <Button onClick={() => setStep(step + 1)}>Skip this one</Button>}
      <Button kind="primary" disabled={!takes[s.id]} onClick={() => setStep(step + 1)}>{step === n - 1 ? "Done recording" : "Next sentence"}</Button>
    </> : <>
      <Button onClick={() => setStep(n - 1)}>Back</Button>
      <Button kind="primary" busy={busy} disabled={!whoOk || usable.length < 1} onClick={make}>Make the voice</Button>
    </>;
  return (
    <Modal wide title={step < 0 ? "Record your voice" : s ? `Sentence ${step + 1} of ${n}` : "Your voice"} onClose={close} footer={footer}>
      {step >= 0 && <div className="voice-dots">{sentences.map((x, i) => <i key={x.id} title={x.text} onClick={() => setStep(i)}
        className={`${i === step ? "now" : ""} ${takes[x.id] ? (takes[x.id].check.verdict === "again" ? "bad" : "ok") : ""}`} />)}</div>}
      {step < 0 && <div className="stack">
        <p>You read {n} short sentences aloud, one at a time, each in a different way. Each one is checked as soon as you stop: whether it is loud enough, clear of noise{script.words_checked ? ", and whether the words were heard" : ""}. The recordings stay on this computer.</p>
        <p className="small muted">The voice model here takes a voice from about one second of speech. KimiLM finds the clearest second of each sentence; afterwards your voice says the same thing starting from each of them, and you choose the one that sounds most like you. That is why there are several sentences, said in different ways.</p>
        <Field label="Name of the voice"><input className="input" autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. My voice" /></Field>
        <Field label="Whose voice is it?">
          <select className="input" value={whose} onChange={(e) => setWhose(e.target.value)}>
            <option value="mine">My own voice</option><option value="other">Another person's voice, with their consent</option>
          </select>
        </Field>
        <div><div className="field-label">Before you start</div><ul className="voice-tips">{script.tips.map((t: string) => <li key={t}>{t}</li>)}</ul></div>
        {!script.words_checked && <div className="small muted">With a transcription model on this computer (Whisper, under Voices & models) the words of each take are checked too.</div>}
      </div>}
      {s && <div className="stack">
        <div className="voice-how">Say it {s.how}</div>
        <div className="voice-say">{s.text}</div>
        <TakeRecorder key={s.id} sentence={s} take={takes[s.id]} onTake={(t) => setTakes((all) => ({ ...all, [s.id]: t }))} />
      </div>}
      {step >= n && <div className="stack">
        <div>
          {sentences.map((x, i) => {
            const t = takes[x.id];
            return (
              <div key={x.id} className="voice-take">
                <div className="row between gap-s">
                  <span className={t ? "" : "muted"}>{x.text}</span>
                  {t ? <Badge kind={(VERDICT[t.check.verdict] || [])[0]}>{(VERDICT[t.check.verdict] || [])[1]}</Badge> : <button className="link" onClick={() => setStep(i)}>not recorded: record it</button>}
                </div>
                {t && <div className="row gap-s wrap"><audio controls preload="metadata" src={fileUrl(t.audio)} /><button className="link" onClick={() => setStep(i)}>record it again</button></div>}
              </div>
            );
          })}
        </div>
        <div className={usable.length < 1 ? "small bad" : "small muted"}>
          {usable.length < 1 ? "None of the sentences passed the check yet: record at least one again."
            : `${usable.length} of ${n} sentences go into the voice${usable.length < Object.keys(takes).length ? " (the ones marked “record it again” are left out)" : ""}. Next, your voice says a sentence starting from each of them, and you choose the one that sounds most like you.`}
        </div>
        {whose === "mine" ? <label className="row gap-s"><input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} /> I confirm that these recordings are my own voice.</label> : <>
          <Field label="Who is it?"><input className="input" value={person} onChange={(e) => setPerson(e.target.value)} placeholder="Their name" /></Field>
          <Field label="Their consent, recorded" help={'A recording in which the person says that they agree, for example: "I, …, agree that my voice is used in KimiLM by …". It is kept with the voice.'}>
            <div className="row gap-s wrap">
              <RecordButton onFile={setProof} label="Record their consent" />
              <Button small icon={<Upload size={13} />} onClick={() => proofFile.current?.click()}>Choose a file…</Button>
              <input ref={proofFile} type="file" accept="audio/*,video/*" hidden onChange={async (e) => { const f = e.target.files?.[0]; if (f) try { setProof(await api.upload("/sound/upload", f)); } catch (err: any) { toast(err.message, "error"); } }} />
              {proof && <Badge kind="ok">{proof.name || "recorded"}</Badge>}
            </div>
          </Field>
        </>}
      </div>}
    </Modal>
  );
}

// One voice, opened: hear it, choose by ear which take it is taken from, rename it, record sentences again, delete it.
export function VoiceDetail({ id, onClose, fresh }: { id: string; onClose: () => void; fresh?: boolean }) {
  const { toast } = useApp();
  const [voice, setVoice] = useState<any>(null);
  const [script, setScript] = useState<any>(null);
  const [name, setName] = useState("");
  const [note, setNote] = useState("");
  const [say, setSay] = useState("");
  const [speaking, setSpeaking] = useState(false);
  const [redo, setRedo] = useState<string | null>(null);        // the sentence being recorded
  const [attempt, setAttempt] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const path = `/sound/voices/${encodeURIComponent(id)}`;
  const take = (v: any) => { setVoice(v); setName(v.name); setNote(v.note || ""); setSpeaking(!!v.sampling); };
  const load = () => api.get(path).then(take).catch(() => onClose());
  const hear = async (which: string) => { setSpeaking(true); try { await api.post(`${path}/sample`, { text: say, which }); } catch (e: any) { setSpeaking(false); toast(e.message, "error"); } };
  useEffect(() => {
    load(); api.get("/sound/voices/script").then(setScript).catch(() => {});
    if (fresh) hear("all");                      // just made: let it speak from every sentence, to choose from
  }, [id]);
  useEvent((e) => {
    if (e.kind !== "voice_sample" || e.voice !== id) return;
    if (e.state === "failed") toast(e.error || "The voice could not speak", "error");
    load();
  }, [id]);
  if (!voice || !script) return <Modal wide title="Voice" onClose={onClose}><Spinner /></Modal>;
  const change = async (body: any) => { try { take(await api.put(path, body)); } catch (e: any) { toast(e.message, "error"); setName(voice.name); } };
  const keep = async () => {
    setBusy(true);
    try { take(await api.post(`${path}/takes`, { sentence: redo, audio: attempt.audio, check: attempt.check })); setRedo(null); setAttempt(null); }
    catch (e: any) { toast(e.message, "error"); }
    setBusy(false);
  };
  const cancel = () => { discard([attempt?.audio]); setRedo(null); setAttempt(null); };
  const close = () => { discard([attempt?.audio]); onClose(); };
  const bySentence: Record<string, any> = Object.fromEntries(voice.takes.filter((t: any) => t.sentence).map((t: any) => [t.sentence, t]));
  const others = voice.takes.filter((t: any) => !t.sentence);
  const several = voice.takes.length > 1;
  const row = (t: any, text: string, s?: any) => (
    <div key={s?.id || t.id} className={`voice-take${t?.lead ? " voice-lead" : ""}`}>
      <div className="row between gap-s">
        <span className={t ? "" : "muted"}>{text}{s && t ? <span className="small muted"> · said {s.how}</span> : null}</span>
        <div className="row gap-s">
          {t?.verdict && <Badge kind={(VERDICT[t.verdict] || [])[0]}>{(VERDICT[t.verdict] || [])[1]}</Badge>}
          {t?.lead && <Badge kind="accent" title={`The voice model is given the clearest ${voice.heard} seconds of this take`}>the voice is taken from here</Badge>}
        </div>
      </div>
      {redo === (s?.id || "") && s ? <div className="stack-s">
        <div className="voice-how">Say it {s.how}</div>
        <TakeRecorder sentence={s} take={attempt} onTake={setAttempt} />
        <div className="row gap-s"><Button small kind="primary" icon={<Check size={13} />} busy={busy} disabled={!attempt} onClick={keep}>Use this take</Button><Button small onClick={cancel}>Cancel</Button></div>
      </div> : <div className="voice-pair">
        {t && <div><div className="small muted">What you said{t.seconds ? ` · ${secs(t.seconds)}` : ""}</div><audio controls preload="metadata" src={fileUrl(t.path)} /></div>}
        {t?.try && <div><div className="small muted">Your voice, taken from it</div><audio controls preload="metadata" src={fileUrl(t.try.path)} /></div>}
        <div className="row gap-s wrap">
          {t && !t.lead && !voice.old && <Button small kind={t.try ? "primary" : "default"} onClick={() => change({ lead: t.id })} title="The voice is taken from the clearest second of this take from now on">This one sounds like me</Button>}
          {s && <button className="link" onClick={() => { cancel(); setRedo(s.id); }}>{t ? "record it again" : "record it"}</button>}
          {t && !t.whole && several && <ConfirmButton small kind="ghost" title="Remove this take" onConfirm={() => api.del(`${path}/takes/${t.id}`).then(take).catch((e: any) => toast(e.message, "error"))}><Trash2 size={13} /></ConfirmButton>}
        </div>
      </div>}
      {t && [...(t.problems || []), ...(t.notes || [])].map((p: string, i: number) => <div key={i} className="small muted">{p}</div>)}
    </div>
  );
  return (
    <Modal wide title={voice.name} onClose={close} footer={<>
      <ConfirmButton onConfirm={() => api.del(path).then(onClose).catch((e: any) => toast(e.message, "error"))}><Trash2 size={13} /> Delete the voice and its recordings</ConfirmButton>
      {!voice.default && voice.ready && <Button icon={<Star size={13} />} onClick={() => api.put("/sound/settings", { voice: voice.id }).then(load).catch((e: any) => toast(e.message, "error"))}>Use by default</Button>}
      <Button kind="primary" onClick={close}>Close</Button>
    </>}>
      <div className="stack">
        <div className="row gap-s wrap">
          <Badge>{voice.whose === "mine" ? "my own voice" : `${voice.person || "another person"}, with recorded consent`}</Badge>
          {voice.default && <Badge kind="accent">default voice</Badge>}
          {voice.ready ? <Badge kind="ok">ready</Badge> : <Badge kind="warn">needs Pocket TTS (Voices & models)</Badge>}
          <span className="small muted">made {fmtTime(voice.created)}{voice.updated && +new Date(voice.updated) - +new Date(voice.created) > 60000 ? ` · changed ${fmtTime(voice.updated)}` : ""} · {voice.statement}</span>
        </div>
        <div className="row gap-m wrap">
          <Field label="Name"><input className="input" value={name} onChange={(e) => setName(e.target.value)} onBlur={() => name.trim() && name.trim() !== voice.name && change({ name })} /></Field>
          <div className="grow"><Field label="Note"><input className="input" value={note} onChange={(e) => setNote(e.target.value)} onBlur={() => note !== (voice.note || "") && change({ note })} placeholder="e.g. recorded at home, evening voice" /></Field></div>
        </div>
        <div className="card stack-s">
          <div className="field-label"><Volume2 size={14} /> Hear it</div>
          <div className="row gap-s wrap">
            <input className="input grow" value={say} onChange={(e) => setSay(e.target.value)} placeholder={script.sample} />
            <Button kind="primary" busy={speaking} disabled={!voice.ready} onClick={() => hear("lead")}>{voice.sample ? "Say it again" : "Say it"}</Button>
            {several && <Button busy={speaking} disabled={!voice.ready} onClick={() => hear("all")} title="The voice says it once for every sentence you recorded, taken from each in turn: listen below and choose">Say it from every sentence</Button>}
          </div>
          {speaking && <div className="note"><Spinner size={14} /> The voice is speaking. On a processor this takes a few seconds for each time it says the sentence.</div>}
          {voice.sample && !speaking && <div className="row gap-s wrap"><audio controls preload="metadata" src={fileUrl(voice.sample.path)} /><span className="small muted">“{voice.sample.text}”</span></div>}
          <div className="small muted">The same voice is in every voice list: Speak, Podcast, a video's narration, and agents can ask for it by its name.</div>
        </div>
        <div>
          <div className="field-label">Where the voice is taken from</div>
          <div className="small muted">The voice model on this computer takes a voice from about one second of speech ({voice.heard} s). KimiLM gives it the clearest second of one take. {several ? "Which take that is changes how the voice sounds: press “Say it from every sentence”, listen, and choose the one that sounds most like you." : "Record more sentences to have several to choose from."}</div>
          {voice.old && <div className="note"><span>This voice was made before this version, from the first second of its recording, which is often a breath or a pause. <button className="link" onClick={() => change({ lead: "whole" })}>Take it from the clearest second instead</button> and hear the difference.</span></div>}
          {script.sentences.map((s: any) => row(bySentence[s.id], s.text, s))}
          {others.map((t: any) => row(t, t.whole || !t.text ? "The recording this voice was made from" : t.text))}
        </div>
      </div>
    </Modal>
  );
}

// The voices from recordings, in Sound → Voices & models.
export function MyVoices({ state, reload, onFile }: { state: any; reload: () => void; onFile: () => void }) {
  const [voices, setVoices] = useState<any[] | null>(null);
  const [guided, setGuided] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const [fresh, setFresh] = useState(false);
  const load = () => api.get("/sound/voices/mine").then((r) => setVoices(r.voices)).catch(() => setVoices([]));
  useEffect(() => { load(); }, []);
  useEvent((e) => { if (e.kind === "sound_changed") load(); });
  const can = state.can.clone;
  return (
    <>
      <div className="row between gap-s wrap">
        <h3><UserRound size={16} /> Your own voice</h3>
        <div className="row gap-s">
          <Button small kind="primary" icon={<Mic size={13} />} disabled={!can} onClick={() => setGuided(true)}>Record my voice</Button>
          <Button small icon={<Upload size={13} />} disabled={!can} onClick={onFile} title="Make a voice from one recording you already have (five seconds or more of clear speech)">From a recording I have…</Button>
        </div>
      </div>
      {!can && <div className="small muted">Download “Pocket TTS” below first: it is the model that speaks with a voice from a recording.</div>}
      {can && voices?.length === 0 && <div className="small muted">None yet. Read eight short sentences aloud (about two minutes with the checks), choose the result that sounds most like you, and KimiLM can read any English text with your voice, on this computer.</div>}
      {(voices || []).map((v) => {
        const n = v.takes.filter((t: any) => t.sentence).length;
        return (
          <div key={v.id} className="list-row" onClick={() => { setFresh(false); setOpen(v.id); }} title="Open: hear it, choose how it sounds, rename it, record sentences again">
            <div><b>{v.name}</b> <Badge>{v.whose === "mine" ? "my own voice" : "with recorded consent"}</Badge> {v.default && <Badge kind="accent">default</Badge>} {!v.ready && <Badge kind="warn">not ready</Badge>}
              <div className="small muted">{n ? `${n} sentence${n === 1 ? "" : "s"} recorded` : "from one recording"}{v.old ? " · made before this version: open it to improve it" : ""}{v.note ? ` · ${v.note}` : ""}</div></div>
            <div className="row gap-s" onClick={(e) => e.stopPropagation()}>
              {v.sample && <audio controls preload="none" src={fileUrl(v.sample.path)} />}
              <Button small onClick={() => { setFresh(false); setOpen(v.id); }}>Open</Button>
            </div>
          </div>
        );
      })}
      {guided && <GuidedVoice onClose={(made) => { setGuided(false); load(); reload(); if (made) { setFresh(true); setOpen(made.id); } }} />}
      {open && <VoiceDetail id={open} fresh={fresh} onClose={() => { setOpen(null); setFresh(false); load(); reload(); }} />}
    </>
  );
}
