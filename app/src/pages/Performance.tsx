import React, { useEffect, useMemo, useRef, useState } from "react";
import { Download, Maximize2, Minimize2, Pause, Play } from "lucide-react";
import { api, TOKEN } from "../api";
import { go, useApp, useShown } from "../store";
import { Badge, Button, PageHeader } from "../components/ui";
import { TallBox } from "../components/Resizable";
import { AreaChart, clock, fmt, Legend, Mark, Series, SLOTS, OTHER, Spark, Tile, useInterval } from "../components/Charts";

// 2.27.0: how the computer is used, live. The engine samples once a second (telemetry.py); on KimiOS the system
// service kimi-telemetry adds what only the system can read (GPU busy time, power, fans, throttling).

const WINDOWS = [
  { s: 60, label: "1 min" }, { s: 300, label: "5 min" }, { s: 900, label: "15 min" },
  { s: 3600, label: "1 hour" }, { s: 21600, label: "6 hours" }, { s: 86400, label: "24 hours" },
];
const KEEP = 3600;

function remembered<T>(key: string, initial: T): [T, (v: T) => void] {
  const [v, setV] = useState<T>(() => { try { const x = localStorage.getItem(key); return x === null ? initial : JSON.parse(x); } catch { return initial; } });
  return [v, (x: T) => { setV(x); try { localStorage.setItem(key, JSON.stringify(x)); } catch {} }];
}

type Row = Record<string, number | null> & { t: number };

function ChartCard({ id, title, help, legend, children, wide, onWide, height = 190 }: any) {
  return (
    <div className={`card chart-card${wide ? " wide" : ""}`}>
      <div className="card-head">
        <div className="stack-xs"><h3>{title}</h3>{help && <div className="small muted">{help}</div>}</div>
        <button className="icon-btn" onClick={onWide} title={wide ? "Back to half width" : "Use the full width"}>
          {wide ? <Minimize2 size={15} /> : <Maximize2 size={15} />}</button>
      </div>
      {legend}
      <TallBox id={`perf.${id}`} initial={height} min={110} max={720}>{(h: number) => children(wide ? Math.max(h, 220) : h)}</TallBox>
    </div>
  );
}

export default function Performance() {
  const { boot } = useApp();
  const shown = useShown();
  const [win, setWin] = remembered("kimilm.perf.window", 300);
  const [wide, setWide] = remembered<string[]>("kimilm.perf.wide", []);
  const [rows, setRows] = useState<Row[]>([]);
  const [marks, setMarks] = useState<Mark[]>([]);
  const [info, setInfo] = useState<any>(null);
  const [latest, setLatest] = useState<any>(null);
  const [paused, setPaused] = useState(false);
  const [zoom, setZoom] = useState<[number, number] | null>(null);
  const [top, setTop] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [off, setOff] = useState(false);
  const live = win <= 3600;
  const names: Record<string, string> = useMemo(() => Object.fromEntries([...(boot?.models || []), ...(boot?.cloud_models || [])].map((m: any) => [m.id, m.name])), [boot]);
  const nameOf = (id: string) => names[id] || id;

  const load = async () => {
    try {
      const h = await api.get(`/telemetry?seconds=${win}`);
      setRows(h.samples); setMarks(h.marks || []); setInfo(h.info); setOff(!h.running);
      if (h.latest) setLatest(h.latest);
    } catch {} finally { setLoading(false); }
  };
  useEffect(() => { if (shown) load(); }, [win, shown]);

  // live: one sample a second, while this screen is shown and not paused
  const pausedRef = useRef(paused);
  pausedRef.current = paused;
  useEffect(() => {
    if (!shown || !live) return;
    const src = new EventSource(`/api/telemetry/stream?token=${encodeURIComponent(TOKEN)}`);
    src.onmessage = (m) => {
      if (pausedRef.current) return;
      try {
        const s = JSON.parse(m.data);
        setLatest(s);
        setRows((r) => { const n = r.length >= KEEP ? r.slice(r.length - KEEP + 1) : r.slice(); n.push(s); return n; });
        if (s.new_marks?.length) setMarks((ms) => [...ms, ...s.new_marks].slice(-400));
      } catch {}
    };
    return () => src.close();
  }, [shown, live]);
  useInterval(() => { if (!live && !paused) load(); }, shown && !live ? 60000 : null);
  useInterval(() => api.get("/telemetry/processes").then(setTop).catch(() => {}), shown && !paused ? 3000 : null);
  useEffect(() => { if (shown) api.get("/telemetry/processes").then(setTop).catch(() => {}); }, [shown]);

  const now = rows.length ? rows[rows.length - 1].t : Date.now() / 1000;
  const domain: [number, number] = zoom || [now - win, now];
  const view = rows.filter((r) => r.t >= domain[0] - 2 && r.t <= domain[1] + 2);
  const t = view.map((r) => r.t);
  const col = (k: string) => view.map((r) => (r[k] == null ? null : (r[k] as number)));
  const diff = (a: string, b: string) => view.map((r) => (r[a] == null ? null : Math.max(0, (r[a] as number) - ((r[b] as number) || 0))));
  const has = (k: string) => view.some((r) => r[k] != null) || latest?.[k] != null;
  const stats = (k: string) => {
    const v = view.map((r) => r[k]).filter((x): x is number => x != null);
    return v.length ? { avg: v.reduce((a, b) => a + b, 0) / v.length, max: Math.max(...v), now: v[v.length - 1] } : null;
  };
  const tail = (k: string) => rows.slice(-60).map((r) => r[k] as number | null);
  const memTotal = latest?.mem_total_mb || info?.ram_mb || 0;
  const isWide = (id: string) => wide.includes(id);
  const flip = (id: string) => setWide(isWide(id) ? wide.filter((x) => x !== id) : [...wide, id]);
  const props = (id: string) => ({ id, wide: isWide(id), onWide: () => flip(id) });
  const chartBase = { t, domain, marks, onZoom: (a: number, b: number) => setZoom([a, b]), onUnzoom: () => setZoom(null) };
  const gpuIsClock = !has("gpu") && view.some((r) => (r as any).gpu_clock != null);

  const exportCsv = () => {
    const keys = Object.keys(view[0] || {}).filter((k) => k !== "t");
    const lines = ["time," + keys.join(",")].concat(view.map((r) => [new Date(r.t * 1000).toISOString(), ...keys.map((k) => r[k] ?? "")].join(",")));
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([lines.join("\n") + "\n"], { type: "text/csv" }));
    a.download = `kimilm-performance-${new Date(domain[0] * 1000).toISOString().slice(0, 19).replace(/:/g, "")}.csv`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  };

  const cpu = stats("cpu"), kc = stats("kimilm_cpu"), gpu = stats(gpuIsClock ? "gpu_clock" : "gpu"), tw = stats("tok_write");
  const tc = latest?.temp_cpu;
  const active = latest?.active || [];
  const perModel = Object.entries(latest?.models || {}) as [string, any][];

  return (
    <div className="page">
      <PageHeader title="Performance" subtitle={<>How this computer is used right now and over the last day: processor, graphics, memory, network, disks, temperature and power, and how much of each KimiLM and its models take. Drag across a chart to look closer; double-click it to go back.</>}
        actions={<>{latest?.kimios ? <Badge kind="ok" title="KimiOS's kimi-telemetry service adds GPU busy time, power, fans and throttling">KimiOS readings on</Badge>
          : <Badge title="On KimiOS, the system adds GPU busy time, power and fans">Standard readings</Badge>}</>} />
      {off && <div className="notice">Readings are switched off (Settings → Performance and usage).</div>}
      <div className="filters">
        <div className="seg">{WINDOWS.map((w) => <button key={w.s} className={`seg-btn${win === w.s && !zoom ? " on" : ""}`} onClick={() => { setZoom(null); setWin(w.s); }}>{w.label}</button>)}</div>
        <Button small kind={paused ? "primary" : "default"} icon={paused ? <Play size={13} /> : <Pause size={13} />} onClick={() => setPaused(!paused)}
          title={paused ? "Show new readings again" : "Hold the charts still (readings go on in the background)"}>{paused ? "Resume" : "Pause"}</Button>
        {zoom && <span className="chip" onClick={() => setZoom(null)} title="Back to the whole window">{clock(zoom[0])} – {clock(zoom[1])} · back to {live ? "live" : "the window"}</span>}
        <span className="grow" />
        <span className="small muted">{live ? (paused ? "Paused" : "Live, every second") : "Minute averages, refreshed every minute"}</span>
        <Button small kind="ghost" icon={<Download size={13} />} onClick={exportCsv} disabled={!view.length}>CSV</Button>
      </div>

      <div className={`tiles${loading ? " stale" : ""}`}>
        <Tile label="Processor" value={latest?.cpu != null ? fmt.pct(latest.cpu) : "—"} spark={<Spark values={tail("cpu")} max={100} />}
          sub={cpu ? `KimiLM ${fmt.pct(latest?.kimilm_cpu || 0)} · peak ${fmt.pct(cpu.max)}` : ""}
          title={`${info?.cpu || ""} · ${info?.cores || "?"} cores, ${info?.threads || "?"} threads${latest?.cpu_mhz ? ` · ${latest.cpu_mhz} MHz` : ""}`} />
        <Tile label="Memory" value={latest ? fmt.mb(latest.mem_used_mb) : "—"} spark={<Spark values={tail("mem_used_mb")} max={memTotal} color="var(--s2)" />}
          sub={latest ? `of ${fmt.mb(memTotal)} · KimiLM ${fmt.mb(latest.kimilm_mem_mb || 0)}` : ""}
          tone={latest && memTotal && latest.mem_avail_mb / memTotal < 0.08 ? "bad" : latest && memTotal && latest.mem_avail_mb / memTotal < 0.15 ? "warn" : undefined} />
        <Tile label={gpuIsClock ? "Graphics clock" : "Graphics"} value={(gpuIsClock ? latest?.gpu_clock : latest?.gpu) != null ? fmt.pct(gpuIsClock ? latest.gpu_clock : latest.gpu) : "—"}
          spark={<Spark values={tail(gpuIsClock ? "gpu_clock" : "gpu")} max={100} color="var(--s7)" />}
          sub={gpu ? `peak ${fmt.pct(gpu.max)}${latest?.kimilm_gpu != null ? ` · KimiLM ${fmt.pct(latest.kimilm_gpu)}` : ""}` : (info?.gpus?.[0]?.name || "no reading on this system")}
          title={gpuIsClock ? "This graphics driver reports its clock, not how busy it is. On KimiOS the system reads busy time." : (latest?.gpus || []).map((g: any) => g.label || g.name).join(", ")} />
        <Tile label="Models writing" value={latest?.tok_write != null ? fmt.tps(latest.tok_write) : "—"} spark={<Spark values={tail("tok_write")} color="var(--s3)" />}
          sub={active.length ? `${latest.reading} reading · ${latest.writing} writing` : tw?.max ? `peak ${fmt.tps(tw.max)}` : "no model at work"} />
        <Tile label="Network" value={latest?.net_rx != null ? `↓ ${fmt.rate(latest.net_rx)}` : "—"} spark={<Spark values={tail("net_rx")} color="var(--s1)" />}
          sub={latest?.net_tx != null ? `↑ ${fmt.rate(latest.net_tx)}` : ""} />
        <Tile label="Disk" value={latest?.disk_read != null ? `R ${fmt.rate(latest.disk_read)}` : "—"} spark={<Spark values={tail("disk_read")} color="var(--s4)" />}
          sub={latest?.disk_write != null ? `W ${fmt.rate(latest.disk_write)}` : ""} />
        {tc != null && <Tile label="Temperature" value={fmt.temp(tc)} spark={<Spark values={tail("temp_cpu")} color="var(--s8)" />}
          sub={`processor${latest?.gpu_temp != null ? ` · graphics ${fmt.temp(latest.gpu_temp)}` : ""}${latest?.throttled ? " · slowed by heat" : ""}`}
          tone={tc >= 95 || latest?.throttled ? "bad" : tc >= 85 ? "warn" : undefined} />}
        {(latest?.power_w != null || latest?.power_cpu_w != null || latest?.battery != null) && <Tile label="Power"
          value={latest?.power_w != null ? fmt.watts(latest.power_w) : latest?.power_cpu_w != null ? fmt.watts(latest.power_cpu_w) : `${latest.battery}%`}
          sub={[latest?.battery != null ? `battery ${latest.battery}%${latest.on_ac ? " · plugged in" : ""}` : null,
            latest?.power_cpu_w != null ? `processor ${fmt.watts(latest.power_cpu_w)}` : null].filter(Boolean).join(" · ")}
          spark={<Spark values={tail(latest?.power_w != null ? "power_w" : "power_cpu_w")} color="var(--s5)" />} />}
      </div>

      <div className="chart-grid-2">
        <ChartCard {...props("cpu")} title="Processor" help={cpu ? `average ${fmt.pct(cpu.avg)}, peak ${fmt.pct(cpu.max)}${kc ? ` · KimiLM average ${fmt.pct(kc.avg)}` : ""}` : "How busy all cores are together"}
          legend={<Legend items={[{ label: "KimiLM and its models", color: SLOTS[0] }, { label: "Everything else", color: OTHER }]} />}>
          {(h: number) => <AreaChart {...chartBase} height={h} stacked yMax={100} format={fmt.pct}
            series={[{ key: "k", label: "KimiLM and its models", color: SLOTS[0], values: col("kimilm_cpu") },
              { key: "o", label: "Everything else", color: OTHER, values: diff("cpu", "kimilm_cpu") }]} />}
        </ChartCard>
        <ChartCard {...props("mem")} title="Memory" help={latest ? `${fmt.mb(latest.mem_avail_mb)} free of ${fmt.mb(memTotal)}${latest.slice_max_mb ? ` · KimiLM may use up to ${fmt.mb(latest.slice_max_mb)} (KimiOS)` : ""}${latest.swap_used_mb ? ` · ${fmt.mb(latest.swap_used_mb)} swapped` : ""}` : ""}
          legend={<Legend items={[{ label: "KimiLM and its models", color: SLOTS[1] }, { label: "Everything else", color: OTHER }]} />}>
          {(h: number) => <AreaChart {...chartBase} height={h} stacked yMax={memTotal || undefined} format={fmt.mb}
            series={[{ key: "k", label: "KimiLM and its models", color: SLOTS[1], values: col("kimilm_mem_mb") },
              { key: "o", label: "Everything else", color: OTHER, values: diff("mem_used_mb", "kimilm_mem_mb") }]} />}
        </ChartCard>
        <ChartCard {...props("gpu")} title={gpuIsClock ? "Graphics (clock)" : "Graphics"}
          help={gpuIsClock ? "This driver reports how fast the graphics run, not how busy they are; KimiOS reads busy time." : (latest?.gpus || []).map((g: any) => `${g.label || g.name}${g.mem_used_mb != null ? ` · ${fmt.mb(g.mem_used_mb)}${g.mem_total_mb ? ` of ${fmt.mb(g.mem_total_mb)}` : ""}` : ""}`).join(" · ") || "No graphics reading on this system"}
          legend={<Legend items={[{ label: gpuIsClock ? "Clock, share of its top speed" : "All programs", color: SLOTS[6] }, ...(has("kimilm_gpu") ? [{ label: "KimiLM's models", color: SLOTS[0] }] : [])]} />}>
          {(h: number) => <AreaChart {...chartBase} height={h} yMax={100} format={fmt.pct} empty="No graphics reading on this system"
            series={[{ key: "g", label: gpuIsClock ? "Clock" : "All programs", color: SLOTS[6], values: col(gpuIsClock ? "gpu_clock" : "gpu") },
              { key: "k", label: "KimiLM's models", color: SLOTS[0], values: col("kimilm_gpu") }]} />}
        </ChartCard>
        <ChartCard {...props("tok")} title="What the models are doing" help="Tokens written each second (counted), and read each second while a prompt is read (about: from the model's measured reading speed)"
          legend={<Legend items={[{ label: "Writing", color: SLOTS[2] }, { label: "Reading (about)", color: SLOTS[3] }]} />}>
          {(h: number) => <AreaChart {...chartBase} height={h} minTop={5} format={(v) => fmt.tps(v)} axisFormat={(v) => `${Math.round(v)}`} empty="No model has worked in this window"
            series={[{ key: "w", label: "Writing", color: SLOTS[2], values: col("tok_write") },
              { key: "r", label: "Reading (about)", color: SLOTS[3], values: col("tok_read"), dashed: true }]} />}
        </ChartCard>
        <ChartCard {...props("net")} title="Network" help={(() => { const a = stats("net_rx"), b = stats("net_tx"); return a && b ? `peak ↓ ${fmt.rate(a.max)} · ↑ ${fmt.rate(b.max)}` : ""; })()}
          legend={<Legend items={[{ label: "Received", color: SLOTS[0] }, { label: "Sent", color: SLOTS[1] }]} />}>
          {(h: number) => <AreaChart {...chartBase} height={h} minTop={10240} format={fmt.rate}
            series={[{ key: "rx", label: "Received", color: SLOTS[0], values: col("net_rx") }, { key: "tx", label: "Sent", color: SLOTS[1], values: col("net_tx") }]} />}
        </ChartCard>
        <ChartCard {...props("disk")} title="Disks" help={(() => { const a = stats("disk_read"), b = stats("disk_write"); return a && b ? `peak read ${fmt.rate(a.max)} · write ${fmt.rate(b.max)} (loading a model reads it from disk)` : ""; })()}
          legend={<Legend items={[{ label: "Read", color: SLOTS[3] }, { label: "Written", color: SLOTS[4] }]} />}>
          {(h: number) => <AreaChart {...chartBase} height={h} minTop={102400} format={fmt.rate}
            series={[{ key: "r", label: "Read", color: SLOTS[3], values: col("disk_read") }, { key: "w", label: "Written", color: SLOTS[4], values: col("disk_write") }]} />}
        </ChartCard>
        {(has("temp_cpu") || has("gpu_temp")) && <ChartCard {...props("temp")} title="Temperature" help={latest?.throttled ? "The processor is slowing itself down to cool off" : "Processor and graphics"}
          legend={<Legend items={[{ label: "Processor", color: SLOTS[7] }, ...(has("gpu_temp") ? [{ label: "Graphics", color: SLOTS[6] }] : [])]} />}>
          {(h: number) => <AreaChart {...chartBase} height={h} minTop={60} format={fmt.temp}
            series={[{ key: "c", label: "Processor", color: SLOTS[7], values: col("temp_cpu") }, { key: "g", label: "Graphics", color: SLOTS[6], values: col("gpu_temp") }]} />}
        </ChartCard>}
        {(has("power_w") || has("power_cpu_w") || has("gpu_power_w")) && <ChartCard {...props("power")} title="Power" help="From the battery while unplugged; processor and graphics from the chip's own meters (KimiOS)"
          legend={<Legend items={[{ label: "Whole computer (battery)", color: SLOTS[4] }, { label: "Processor", color: SLOTS[0] }, { label: "Graphics", color: SLOTS[6] }].filter((x, i) => has(["power_w", "power_cpu_w", "gpu_power_w"][i]) || (i === 2 && has("power_gpu_w")))} />}>
          {(h: number) => <AreaChart {...chartBase} height={h} minTop={10} format={fmt.watts}
            series={[{ key: "b", label: "Whole computer (battery)", color: SLOTS[4], values: col("power_w") },
              { key: "c", label: "Processor", color: SLOTS[0], values: col("power_cpu_w") },
              { key: "g", label: "Graphics", color: SLOTS[6], values: view.map((r) => (r.power_gpu_w ?? r.gpu_power_w) as number | null) }]} />}
        </ChartCard>}
        {has("psi_cpu") && <ChartCard {...props("psi")} title="Waiting" help="Share of time programs waited for the processor, memory or the disk (Linux pressure). Above a few percent the computer feels slow."
          legend={<Legend items={[{ label: "Processor", color: SLOTS[0] }, { label: "Memory", color: SLOTS[1] }, { label: "Disk", color: SLOTS[3] }]} />}>
          {(h: number) => <AreaChart {...chartBase} height={h} minTop={10} format={fmt.pct}
            series={[{ key: "c", label: "Processor", color: SLOTS[0], values: col("psi_cpu") }, { key: "m", label: "Memory", color: SLOTS[1], values: col("psi_mem") },
              { key: "i", label: "Disk", color: SLOTS[3], values: col("psi_io") }]} />}
        </ChartCard>}

        <div className="card stack-s">
          <h3>Models at work</h3>
          {active.length === 0 && perModel.length === 0 && <div className="small muted">No model is loaded. Loading one (a chat, or Models → Load) shows here, with what it takes.</div>}
          {active.length > 0 && <div className="now-list">{active.map((c: any, i: number) => <div key={i} className="now-row">
            <span className={`phase phase-${c.phase}`}>{c.phase === "reading" ? "R" : "W"}</span>
            <span className="ellipsis">{nameOf(c.model)} · {c.phase === "reading" ? "reading its prompt" : `writing (${c.tokens} tokens)`}</span>
            <span className="small muted">{fmt.dur(c.seconds)}</span></div>)}</div>}
          {perModel.length > 0 && <table className="proc-table"><tbody>
            {perModel.map(([id, m]) => <tr key={id}><td className="ellipsis">{nameOf(id)}</td><td>{fmt.pct(m.cpu || 0)} processor</td><td>{fmt.mb(m.mem_mb)}</td></tr>)}
          </tbody></table>}
          <div className="row gap-s"><Button small kind="ghost" onClick={() => go("models")}>Models</Button><Button small kind="ghost" onClick={() => go("usage")}>Usage over time</Button></div>
        </div>
        {latest?.cpu_cores?.length > 0 && <div className="card stack-s">
          <h3>Each core, now</h3>
          <div className="cores">{latest.cpu_cores.map((c: number, i: number) => <div key={i} className="core" title={`Core ${i + 1}: ${c}%`}>
            <div className="core-bar"><span style={{ height: `${Math.max(2, c)}%` }} /></div>{i + 1}</div>)}</div>
          <div className="small muted">Models write fastest with one thread per fast core; the rest of the cores stay free for you.</div>
        </div>}
        <div className="card stack-s">
          <h3>Programs using the most</h3>
          {!top ? <div className="small muted">Reading…</div> : <div className="row gap-m wrap" style={{ alignItems: "flex-start" }}>
            {(["cpu", "memory"] as const).map((k) => <div key={k} className="grow" style={{ minWidth: 220 }}>
              <div className="small muted">{k === "cpu" ? "Processor" : "Memory"}</div>
              <table className="proc-table"><tbody>{(top[k] || []).slice(0, 8).map((p: any) => <tr key={p.pid} className={p.kimilm ? "ours" : ""}>
                <td className="ellipsis" title={`${p.name} (process ${p.pid})${p.kimilm ? " · part of KimiLM" : ""}`}>{p.name}{p.kimilm ? " · KimiLM" : ""}</td>
                <td>{k === "cpu" ? fmt.pct(p.cpu || 0) : fmt.mb(p.mem_mb)}</td></tr>)}</tbody></table>
            </div>)}
          </div>}
        </div>
        <div className="card stack-s">
          <h3>What happened</h3>
          <div className="small muted">Models loaded and unloaded, tasks started and finished: the dotted lines on the charts.</div>
          {marks.filter((m) => m.t >= domain[0]).length === 0 ? <div className="small muted">Nothing in this window.</div> :
            <div className="marks-list">{[...marks].filter((m) => m.t >= domain[0]).reverse().map((m, i) => <div key={i}><span className="muted">{clock(m.t)}</span><span>{m.text}</span></div>)}</div>}
        </div>
      </div>
    </div>
  );
}
