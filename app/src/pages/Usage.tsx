import React, { useEffect, useMemo, useState } from "react";
import { Download } from "lucide-react";
import { api, TOKEN } from "../api";
import { go, useApp, useShown } from "../store";
import { Badge, Button, ConfirmButton, PageHeader } from "../components/ui";
import { TallBox } from "../components/Resizable";
import { BarList, Columns, delta, fmt, Legend, OTHER, Series, SLOTS, Tile, WeekHeat } from "../components/Charts";

// 2.27.0: what the models did, from every call they made (meter.py): tokens, time and speed per model, per day,
// per hour of the week, per chat, per kind of work, and how evaluations went.

const PERIODS = [{ d: 1, label: "Today" }, { d: 7, label: "7 days" }, { d: 30, label: "30 days" }, { d: 90, label: "90 days" }, { d: 365, label: "1 year" }];
const PURPOSE: Record<string, string> = {
  chat: "Chats", agent: "Agents of a team", planner: "The planner", check: "Checking results", schedule: "Scheduled tasks",
  eval: "Evaluations", memory: "Memory and learning", snapshot: "Reading instructions once (snapshots)", other: "Other",
};
const TOP = 6;
// each kind of work keeps its colour whatever its rank (colour follows the thing, never the order)
const PURPOSE_COLOUR: Record<string, string> = {
  chat: SLOTS[0], agent: SLOTS[1], planner: SLOTS[2], check: SLOTS[3], schedule: SLOTS[4], memory: SLOTS[5], snapshot: SLOTS[6], eval: SLOTS[7],
};      // models with their own colour; the rest are "Other models"

function day(iso: string, d: number) {
  const x = new Date(iso + "T12:00:00");
  return d <= 31 ? x.toLocaleDateString([], { day: "numeric", month: "short" }) : x.toLocaleDateString([], { month: "short", day: "numeric" });
}

export default function Usage() {
  const { toast } = useApp();
  const shown = useShown();
  const [days, setDays] = useState<number>(() => { try { return Number(localStorage.getItem("kimilm.usage.days")) || 30; } catch { return 30; } });
  const [data, setData] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [measure, setMeasure] = useState<"tokens" | "written" | "seconds">("tokens");
  const load = async () => { setBusy(true); try { setData(await api.get(`/usage?days=${days}`)); } catch (e: any) { toast(e.message, "error"); } finally { setBusy(false); } };
  useEffect(() => { if (shown) load(); }, [days, shown]);
  const pick = (d: number) => { setDays(d); try { localStorage.setItem("kimilm.usage.days", String(d)); } catch {} };

  // colours follow the model, in the order of most tokens over the period; beyond TOP they fold into Other
  const colourOf = useMemo(() => {
    const m: Record<string, string> = {};
    (data?.by_model || []).forEach((x: any, i: number) => { m[x.model_id] = i < TOP ? SLOTS[i] : OTHER; });
    return m;
  }, [data]);
  if (!data) return <div className="page"><PageHeader title="Usage" /><div className="muted">Adding up…</div></div>;

  const T = data.totals, P = data.previous, H = data.highlights;
  const models = data.by_model as any[];
  const daily = data.daily as any[];
  const labels = daily.map((d) => day(d.day, days));
  const topIds = models.slice(0, TOP).map((m) => m.model_id);
  const dayModels: Series[] = [
    ...topIds.map((id, i) => ({ key: id, label: data.names[id] || id, color: SLOTS[i], values: daily.map((d) => d.models[id] || 0) })),
    ...(models.length > TOP ? [{ key: "other", label: "Other models", color: OTHER,
      values: daily.map((d) => Object.entries(d.models).filter(([id]) => !topIds.includes(id)).reduce((a, [, v]) => a + (v as number), 0)) }] : []),
  ];
  const kinds: Series[] = [
    { key: "read", label: "Read", color: SLOTS[3], values: daily.map((d) => d.read) },
    { key: "cached", label: "Read again from the cache", color: SLOTS[0], values: daily.map((d) => d.cached) },
    { key: "written", label: "Written", color: SLOTS[2], values: daily.map((d) => d.written) },
  ];
  const timeSeries: Series[] = [{ key: "s", label: "Time with models", color: SLOTS[0], values: daily.map((d) => d.seconds / 3600) }];
  const act = data.activity;
  const actSeries: Series[] = [
    { key: "you", label: "Your messages", color: SLOTS[1], values: act.daily.map((d: any) => d.you) },
    { key: "replies", label: "Replies", color: SLOTS[0], values: act.daily.map((d: any) => d.replies) },
  ];
  const media = data.media || {};
  const made = [["image", "pictures"], ["sound", "sounds"], ["audio", "sounds"], ["video", "videos"]].filter(([k]) => media[k]).map(([k, w]) => `${media[k]} ${w}`);
  const exportUrl = `/api/usage/export?days=${days}&token=${encodeURIComponent(TOKEN)}`;
  const hour = (h: number) => `${String(h).padStart(2, "0")}:00`;
  const speedModels = Object.entries(data.speed_trend || {}).filter(([, v]: any) => Object.keys(v).length >= 2).slice(0, TOP);

  return (
    <div className="page">
      <PageHeader title="Usage" subtitle="Every call a model made, on this computer or online: tokens read and written, time, speed, by model, day, hour, chat and kind of work. Nothing leaves this computer."
        actions={<><a className="btn btn-sm" href={exportUrl} download><Download size={13} /> CSV</a></>} />
      <div className="filters">
        <div className="seg">{PERIODS.map((p) => <button key={p.d} className={`seg-btn${days === p.d ? " on" : ""}`} onClick={() => pick(p.d)}>{p.label}</button>)}</div>
        <span className="grow" />
        {data.has_history && <span className="small muted" title="Replies from before KimiLM 2.27.0 are counted once per reply, from what each reply recorded">includes replies from before calls were counted</span>}
      </div>

      <div className={`tiles${busy ? " stale" : ""}`}>
        <Tile label="Tokens" value={fmt.tokens(T.tokens)} sub={delta(T.tokens, P.tokens)} title={`${fmt.num(T.tokens)} tokens: read ${fmt.num(T.read)}, from the cache ${fmt.num(T.cached)}, written ${fmt.num(T.written)}`} />
        <Tile label="Written" value={fmt.tokens(T.written)} sub={`about ${fmt.tokens(H.words_written)} words · ${Math.round(H.pages_written)} pages`} />
        <Tile label="Model calls" value={fmt.num(T.calls)} sub={T.failed ? `${T.failed} did not finish (${Math.round((100 * T.failed) / Math.max(1, T.calls))}%)` : delta(T.calls, P.calls)}
          tone={T.calls && T.failed / T.calls > 0.15 ? "warn" : undefined} />
        <Tile label="Time with models" value={fmt.dur(T.seconds)} sub={delta(T.seconds, P.seconds)} />
        <Tile label="Read from the cache" value={`${T.cache_percent}%`} sub={H.cache_saved_seconds ? `saved about ${fmt.dur(H.cache_saved_seconds)} of reading` : "of everything read"} />
        <Tile label="Writing speed" value={T.write_tps ? fmt.tps(T.write_tps) : "—"} sub={T.read_tps ? `reading ${fmt.tps(T.read_tps)}` : ""} />
        <Tile label="Chats" value={fmt.num(T.chats)} sub={`${act.new_chats} new · ${fmt.num(act.you)} messages from you`} />
        {T.cost > 0 && <Tile label="Online models" value={`$${T.cost.toFixed(2)}`} sub={delta(T.cost, P.cost)} />}
        <Tile label="Days in a row" value={fmt.num(H.streak_days)} sub={`longest ${H.longest_streak} day${H.longest_streak === 1 ? "" : "s"}`} />
      </div>

      {T.calls === 0 && <div className="card empty"><h3>Nothing yet in this period</h3><p>Start a chat; every model call is counted from now on.</p></div>}

      {T.calls > 0 && <div className="chart-grid-2">
        <div className="card chart-card wide">
          <div className="card-head"><div className="stack-xs"><h3>Each day</h3>
            <div className="small muted">{measure === "tokens" ? "Tokens by model (read, from the cache and written together)" : measure === "written" ? "What was read, read again from the cache, and written" : "Hours models were working"}</div></div>
            <div className="seg">{([["tokens", "By model"], ["written", "By kind"], ["seconds", "Time"]] as const).map(([k, l]) =>
              <button key={k} className={`seg-btn${measure === k ? " on" : ""}`} onClick={() => setMeasure(k)}>{l}</button>)}</div></div>
          <Legend items={(measure === "tokens" ? dayModels : measure === "written" ? kinds : timeSeries).map((s) => ({ label: s.label, color: s.color }))} />
          <TallBox id="usage.daily" initial={230} min={140} max={600}>{(h) =>
            <Columns labels={labels} height={h} series={measure === "tokens" ? dayModels : measure === "written" ? kinds : timeSeries}
              format={measure === "seconds" ? (v) => fmt.dur(v * 3600) : fmt.tokens}
              tipTitle={(i) => new Date(daily[i].day + "T12:00:00").toLocaleDateString([], { weekday: "long", day: "numeric", month: "long" })} />}
          </TallBox>
        </div>

        <div className="card chart-card wide">
          <h3>Each model</h3>
          <div style={{ overflowX: "auto" }}>
            <table className="data-table usage-table">
              <thead><tr><th>Model</th><th>Share</th><th>Calls</th><th>Read</th><th>From cache</th><th>Written</th><th>Time</th><th>Reads</th><th>Writes</th><th>Did not finish</th>{T.cost > 0 && <th>Cost</th>}</tr></thead>
              <tbody>{models.map((m) => <tr key={m.model_id}>
                <td><span className="key-dot" style={{ background: colourOf[m.model_id] }} />{m.name}{m.online && <> <Badge>online</Badge></>}</td>
                <td><span className="share"><span style={{ width: `${m.share}%`, background: colourOf[m.model_id] }} /></span>{m.share}%</td>
                <td>{fmt.num(m.calls)}</td><td>{fmt.tokens(m.read)}</td><td>{fmt.tokens(m.cached)} ({m.cache_percent}%)</td><td>{fmt.tokens(m.written)}</td>
                <td>{fmt.dur(m.seconds)}</td><td>{m.read_tps ? fmt.tps(m.read_tps) : "—"}</td><td>{m.write_tps ? fmt.tps(m.write_tps) : "—"}</td>
                <td>{m.failed ? `${m.failed} (${Math.round((100 * m.failed) / Math.max(1, m.calls))}%)` : "—"}</td>{T.cost > 0 && <td>{m.cost ? `$${m.cost.toFixed(2)}` : "—"}</td>}
              </tr>)}</tbody>
            </table>
          </div>
        </div>

        <div className="card chart-card">
          <h3>When you use models</h3>
          <div className="small muted">Tokens by hour of the week{H.peak_hour != null ? ` · busiest around ${hour(H.peak_hour)}` : ""}</div>
          <WeekHeat data={data.heat} format={(v) => `${fmt.tokens(v)} tokens`} />
        </div>

        <div className="card chart-card">
          <h3>Kind of work</h3>
          <div className="small muted">Who asked the models: you in a chat, a team's agents, the planner deciding, checks, schedules</div>
          <BarList rows={data.by_purpose.map((p: any) => ({ key: p.key, label: PURPOSE[p.key] || p.key, value: p.tokens, color: PURPOSE_COLOUR[p.key] || OTHER,
            sub: `${fmt.num(p.calls)} calls · ${fmt.dur(p.seconds)}${p.failed ? ` · ${p.failed} did not finish` : ""}` }))} format={fmt.tokens} />
        </div>

        <div className="card chart-card">
          <h3>Chat activity</h3>
          <div className="small muted">{fmt.num(act.you)} messages from you, {fmt.num(act.replies)} replies, {act.new_chats} new chats</div>
          <Legend items={actSeries.map((s) => ({ label: s.label, color: s.color }))} />
          <TallBox id="usage.activity" initial={170} min={110} max={500}>{(h) =>
            <Columns labels={labels} height={h} series={actSeries} format={(v) => fmt.num(v)} />}</TallBox>
        </div>

        <div className="card chart-card">
          <h3>Chats that used the most</h3>
          <BarList rows={data.top_chats.map((c: any) => ({ key: c.chat_id, label: <>{c.title}{c.agent ? <span className="muted"> · agent</span> : c.origin === "eval" ? <span className="muted"> · evaluation</span> : c.origin === "schedule" ? <span className="muted"> · scheduled</span> : null}</>,
            value: c.tokens, color: "var(--s1)", sub: `${fmt.num(c.calls)} calls · ${fmt.dur(c.seconds)} · ${c.models.join(", ")}` }))}
            format={fmt.tokens} onPick={(id) => { const c = data.top_chats.find((x: any) => x.chat_id === id); if (c?.exists) go("chat", id); }} />
        </div>

        {data.by_harness.length > 0 && <div className="card chart-card">
          <h3>Harnesses</h3>
          <BarList rows={data.by_harness.map((p: any) => ({ key: p.key, label: p.key === "other" ? "None (memory, snapshots…)" : p.key, value: p.tokens, color: "var(--s7)",
            sub: `${fmt.num(p.calls)} calls · ${fmt.dur(p.seconds)}` }))} format={fmt.tokens} />
        </div>}

        <div className="card chart-card">
          <h3>Waiting for answers</h3>
          <div className="small muted">From asking to the first word (reading the prompt), and to the end of the answer, over {fmt.num(data.latency.calls)} calls</div>
          <div className="records">
            <div className="record"><span className="small muted">First word, usually</span><b>{data.latency.first_p50 != null ? fmt.dur(data.latency.first_p50) : "—"}</b><span className="small muted">1 in 10 waits over {data.latency.first_p90 != null ? fmt.dur(data.latency.first_p90) : "—"}</span></div>
            <div className="record"><span className="small muted">Whole answer, usually</span><b>{data.latency.total_p50 != null ? fmt.dur(data.latency.total_p50) : "—"}</b><span className="small muted">1 in 10 takes over {data.latency.total_p90 != null ? fmt.dur(data.latency.total_p90) : "—"}</span></div>
          </div>
          {speedModels.length > 0 && <>
            <div className="small muted">Writing speed by day</div>
            <BarList rows={speedModels.map(([id, v]: any) => {
              const ds = Object.keys(v).sort(), first = v[ds[0]], last = v[ds[ds.length - 1]];
              return { key: id, label: data.names[id] || id, value: last, color: colourOf[id] || OTHER,
                sub: `${fmt.tps(first)} on ${day(ds[0], days)} → ${fmt.tps(last)} on ${day(ds[ds.length - 1], days)}` };
            })} format={fmt.tps} />
          </>}
        </div>

        {data.tests.length > 0 && <div className="card chart-card">
          <h3>Evaluations</h3>
          <div className="small muted">Settings → Check this setup, in this period</div>
          <BarList rows={data.tests.map((e: any) => ({ key: e.id, label: `${new Date(e.created).toLocaleDateString([], { day: "numeric", month: "short" })} · ${e.model} · ${e.version}`,
            value: e.total ? (100 * e.passed) / e.total : 0, color: "var(--s3)", sub: `${e.passed} of ${e.total} passed · ${fmt.dur(e.seconds || 0)}${e.status !== "done" ? ` · ${e.status}` : ""}` }))}
            format={fmt.pct} onPick={() => go("settings", "check")} />
        </div>}

        <div className="card chart-card wide">
          <h3>Records</h3>
          <div className="records">
            {H.busiest_day && <div className="record"><span className="small muted">Busiest day</span><b>{new Date(H.busiest_day.day + "T12:00:00").toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" })}</b><span className="small muted">{fmt.tokens(H.busiest_day.tokens)} tokens · {fmt.num(H.busiest_day.calls)} calls</span></div>}
            {H.biggest_prompt && <div className={`record${H.biggest_prompt.chat_id ? " job-open" : ""}`} onClick={() => H.biggest_prompt.chat_id && go("chat", H.biggest_prompt.chat_id)}>
              <span className="small muted">Longest prompt</span><b>{fmt.tokens(H.biggest_prompt.tokens)} tokens</b><span className="small muted ellipsis">{H.biggest_prompt.model}{H.biggest_prompt.chat ? ` · ${H.biggest_prompt.chat}` : ""}</span></div>}
            {H.longest_call && <div className={`record${H.longest_call.chat_id ? " job-open" : ""}`} onClick={() => H.longest_call.chat_id && go("chat", H.longest_call.chat_id)}>
              <span className="small muted">Longest single call</span><b>{fmt.dur(H.longest_call.seconds)}</b><span className="small muted ellipsis">{H.longest_call.model}{H.longest_call.chat ? ` · ${H.longest_call.chat}` : ""}</span></div>}
            {H.fastest_model && <div className="record"><span className="small muted">Fastest writer on this computer</span><b>{H.fastest_model.name}</b><span className="small muted">{fmt.tps(H.fastest_model.write_tps)}</span></div>}
            <div className="record"><span className="small muted">Written by models</span><b>about {fmt.num(H.words_written)} words</b><span className="small muted">some {Math.round(H.pages_written).toLocaleString()} pages of text</span></div>
            {H.cache_saved_seconds > 0 && <div className="record"><span className="small muted">Not read twice</span><b>{fmt.dur(H.cache_saved_seconds)}</b><span className="small muted">of reading saved by the cache and snapshots</span></div>}
            {made.length > 0 && <div className="record"><span className="small muted">Made</span><b>{made.join(", ")}</b><span className="small muted">Images, Sound and Video</span></div>}
          </div>
        </div>
      </div>}

      <div className="row gap-s mt">
        <span className="small muted grow">Counted on this computer only. Words and pages are estimates (about ¾ of a word per token, 500 words a page).</span>
        <ConfirmButton small onConfirm={async () => { await api.del("/usage"); load(); toast("Usage statistics cleared", "ok"); }}>Clear statistics</ConfirmButton>
      </div>
    </div>
  );
}
