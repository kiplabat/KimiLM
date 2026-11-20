import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

// 2.27.0: small charts drawn in SVG, without a library: live areas (Performance), columns, a heatmap and bar
// lists (Usage). Colours are the validated categorical palette in styles.css (--s1 … --s8); text never takes a
// series colour. Every chart has a crosshair or per-mark tooltip; values are also in tiles and tables.

export const SLOTS = ["var(--s1)", "var(--s2)", "var(--s3)", "var(--s4)", "var(--s5)", "var(--s6)", "var(--s7)", "var(--s8)"];
export const OTHER = "var(--s-other)";

export type Series = { key: string; label: string; color: string; values: (number | null | undefined)[]; dashed?: boolean };
export type Mark = { t: number; text: string; kind?: string };

function useWidth<T extends HTMLElement>(): [React.RefObject<T | null>, number] {
  const ref = useRef<T>(null);
  const [w, setW] = useState(0);
  useLayoutEffect(() => {
    if (!ref.current) return;
    const el = ref.current;
    setW(el.getBoundingClientRect().width);
    const ro = new ResizeObserver((e) => setW(e[0].contentRect.width));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w];
}

/** Round numbers for a y axis from 0 to about max. */
function ticks(max: number, n = 4) {
  if (!(max > 0)) return [0, 1];
  const raw = max / n;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) || raw;
  const out = [];
  for (let v = 0; v <= max + step * 0.001; v += step) out.push(+v.toFixed(10));
  if (out[out.length - 1] < max) out.push(out[out.length - 1] + step);
  return out;
}

export const clock = (t: number, withSeconds = true) =>
  new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", ...(withSeconds ? { second: "2-digit" } : {}) });

type AreaProps = {
  t: number[];
  series: Series[];
  height: number;
  stacked?: boolean;
  yMax?: number;               // fixed top (100 for percent); otherwise from the data
  format: (v: number) => string;
  axisFormat?: (v: number) => string;
  marks?: Mark[];
  domain?: [number, number];   // the time shown (live: the window up to now)
  onZoom?: (from: number, to: number) => void;
  onUnzoom?: () => void;
  empty?: string;
  minTop?: number;             // the y axis reaches at least this (so a quiet chart does not magnify noise)
};

/** Areas over time with a crosshair. Several series are drawn over each other, or stacked when they add up to a
 *  whole (KimiLM + everything else). Drag across it to zoom into that time; double-click to go back. */
export function AreaChart({ t, series, height, stacked, yMax, format, axisFormat, marks = [], domain, onZoom, onUnzoom, empty, minTop = 0 }: AreaProps) {
  const [box, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const [brush, setBrush] = useState<[number, number] | null>(null);
  const pad = { l: 44, r: 10, t: 10, b: 20 };
  const W = Math.max(60, width), H = height;
  const iw = W - pad.l - pad.r, ih = H - pad.t - pad.b;
  const x0 = domain ? domain[0] : t[0] ?? 0, x1 = domain ? domain[1] : t[t.length - 1] ?? 1;
  const sx = (v: number) => pad.l + ((v - x0) / Math.max(1e-9, x1 - x0)) * iw;

  const shown = series.filter((s) => s.values.some((v) => v != null));
  const tops = useMemo(() => {             // the stacked tops of each series
    if (!stacked) return shown.map((s) => s.values.map((v) => (v == null ? null : v)));
    const acc = t.map(() => 0);
    return shown.map((s) => s.values.map((v, i) => { if (v == null) return null; acc[i] += Math.max(0, v); return acc[i]; }));
  }, [t, shown, stacked]);
  const dataMax = Math.max(0, ...tops.flat().filter((v): v is number => v != null));
  const ys = ticks(yMax ?? (Math.max(dataMax, minTop) || 1)).filter((v) => yMax == null || v <= yMax * 1.0001);
  const top = yMax ?? ys[ys.length - 1];
  const sy = (v: number) => pad.t + ih - (Math.min(v, top) / (top || 1)) * ih;

  // one point per pixel column at most: the largest value in it, so peaks stay visible
  const step = Math.max(1, Math.floor(t.length / Math.max(1, iw)));
  const idx = useMemo(() => {
    const out: number[] = [];
    for (let i = 0; i < t.length; i += step) {
      let best = i;
      for (let j = i; j < Math.min(t.length, i + step); j++) {
        const a = tops[tops.length - 1]?.[j], b = tops[tops.length - 1]?.[best];
        if (a != null && (b == null || a > b)) best = j;
      }
      out.push(best);
    }
    return out;
  }, [t, tops, step]);

  const paths = shown.map((s, k) => {
    const upper = tops[k];
    const lower = stacked && k > 0 ? tops[k - 1] : null;
    let line = "", area = "", run: [number, number, number][] = [];
    const flush = () => {
      if (run.length) {
        line += run.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join("");
        area += run.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join("") +
          [...run].reverse().map(([x, , y0]) => `L${x.toFixed(1)},${y0.toFixed(1)}`).join("") + "Z";
      }
      run = [];
    };
    for (const i of idx) {
      const v = upper[i];
      if (v == null || t[i] < x0 - 1 || t[i] > x1 + 1) { flush(); continue; }
      const base = lower ? lower[i] ?? 0 : 0;
      run.push([sx(t[i]), sy(v), sy(base)]);
    }
    flush();
    return { s, line, area };
  });

  const pick = (clientX: number) => {
    const r = box.current!.getBoundingClientRect();
    const tx = x0 + ((clientX - r.left - pad.l) / iw) * (x1 - x0);
    let lo = 0, hi = t.length - 1;
    while (lo < hi) { const m = (lo + hi) >> 1; if (t[m] < tx) lo = m + 1; else hi = m; }
    if (lo > 0 && Math.abs(t[lo - 1] - tx) < Math.abs(t[lo] - tx)) lo--;
    return { i: lo, tx };
  };
  const drag = useRef<number | null>(null);
  const onDown = (e: React.PointerEvent) => {
    if (!onZoom || e.button !== 0 || !t.length) return;
    drag.current = pick(e.clientX).tx;
    (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
  };
  const onMove = (e: React.PointerEvent) => {
    if (!t.length) return;
    const p = pick(e.clientX);
    setHover(p.i);
    if (drag.current != null) setBrush([Math.min(drag.current, p.tx), Math.max(drag.current, p.tx)]);
  };
  const onUp = () => {
    if (brush && onZoom && sx(brush[1]) - sx(brush[0]) > 8) onZoom(brush[0], brush[1]);
    drag.current = null;
    setBrush(null);
  };
  const xt = useMemo(() => {
    const span = x1 - x0, n = Math.max(1, Math.floor(iw / (span < 1800 ? 130 : 100)));
    return Array.from({ length: n + 1 }, (_, k) => x0 + (span * k) / n);
  }, [x0, x1, iw]);
  const near = hover != null ? marks.filter((m) => Math.abs(sx(m.t) - sx(t[hover])) < 6) : [];
  const hx = hover != null ? sx(t[hover]) : 0;
  const noData = !shown.length;
  return (
    <div className="chart" ref={box} style={{ height: H }}>
      {width > 0 && <svg width={W} height={H} onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp}
        onPointerLeave={() => { setHover(null); if (drag.current == null) setBrush(null); }} onDoubleClick={onUnzoom}
        role="img" aria-label={shown.map((s) => s.label).join(", ")}>
        {ys.map((v) => <g key={v}>
          <line x1={pad.l} x2={W - pad.r} y1={sy(v)} y2={sy(v)} className={v === 0 ? "chart-base" : "chart-grid"} />
          <text x={pad.l - 6} y={sy(v) + 3.5} className="chart-axis" textAnchor="end">{(axisFormat || format)(v)}</text>
        </g>)}
        {xt.map((v, k) => <text key={k} x={sx(v)} y={H - 5} className="chart-axis"
          textAnchor={k === 0 ? "start" : k === xt.length - 1 ? "end" : "middle"}>{clock(v, x1 - x0 < 1800)}</text>)}
        {marks.map((m, k) => m.t >= x0 && m.t <= x1 && <g key={k} className={`chart-mark mark-${m.kind || "info"}`}>
          <line x1={sx(m.t)} x2={sx(m.t)} y1={pad.t} y2={pad.t + ih} />
          <circle cx={sx(m.t)} cy={pad.t + 3} r={3} />
        </g>)}
        {paths.map(({ s, area }) => <path key={s.key + "a"} d={area} fill={s.color} className={stacked ? "chart-band" : "chart-wash"} />)}
        {paths.map(({ s, line }) => <path key={s.key + "l"} d={line} stroke={s.color} className="chart-line" strokeDasharray={s.dashed ? "4 3" : undefined} />)}
        {brush && <rect x={sx(brush[0])} y={pad.t} width={Math.max(1, sx(brush[1]) - sx(brush[0]))} height={ih} className="chart-brush" />}
        {hover != null && <g>
          <line x1={hx} x2={hx} y1={pad.t} y2={pad.t + ih} className="chart-cross" />
          {shown.map((s, k) => tops[k][hover] != null && <circle key={s.key} cx={hx} cy={sy(tops[k][hover] as number)} r={4} fill={s.color} className="chart-dot" />)}
        </g>}
      </svg>}
      {noData && <div className="chart-empty">{empty || "No readings yet"}</div>}
      {hover != null && !noData && <div className={`chart-tip${hx > W * 0.62 ? " left" : ""}`} style={{ left: hx }}>
        <div className="chart-tip-time">{clock(t[hover])}</div>
        {[...shown].reverse().map((s) => <div key={s.key} className="chart-tip-row">
          <i style={{ background: s.color }} /><b>{s.values[hover] == null ? "—" : format(s.values[hover] as number)}</b><span>{s.label}</span>
        </div>)}
        {near.map((m, k) => <div key={k} className="chart-tip-mark">{m.text}</div>)}
      </div>}
    </div>
  );
}

export function Legend({ items }: { items: { label: string; color: string; value?: string }[] }) {
  return <div className="legend">{items.map((it) => <span key={it.label} className="legend-item">
    <i style={{ background: it.color }} />{it.label}{it.value ? <b>{it.value}</b> : null}</span>)}</div>;
}

/** Columns, optionally stacked, with a tooltip per column. */
export function Columns({ labels, series, height, format, tipTitle, onPick }: {
  labels: string[]; series: Series[]; height: number; format: (v: number) => string; tipTitle?: (i: number) => string;
  onPick?: (i: number) => void;
}) {
  const [box, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const pad = { l: 48, r: 6, t: 8, b: 22 };
  const W = Math.max(60, width), H = height, iw = W - pad.l - pad.r, ih = H - pad.t - pad.b;
  const totals = labels.map((_, i) => series.reduce((a, s) => a + (s.values[i] || 0), 0));
  const ys = ticks(Math.max(1, ...totals));
  const top = ys[ys.length - 1];
  const sy = (v: number) => pad.t + ih - (v / top) * ih;
  const bw = iw / Math.max(1, labels.length);
  const gap = Math.min(4, bw * 0.25);
  const every = Math.max(1, Math.ceil(labels.length / Math.max(2, Math.floor(iw / 64))));
  return (
    <div className="chart" ref={box} style={{ height: H }}>
      {width > 0 && <svg width={W} height={H} onPointerLeave={() => setHover(null)} role="img" aria-label="columns">
        {ys.map((v) => <g key={v}>
          <line x1={pad.l} x2={W - pad.r} y1={sy(v)} y2={sy(v)} className={v === 0 ? "chart-base" : "chart-grid"} />
          <text x={pad.l - 6} y={sy(v) + 3.5} className="chart-axis" textAnchor="end">{format(v)}</text>
        </g>)}
        {labels.map((l, i) => {
          let acc = 0;
          const x = pad.l + i * bw + gap / 2, w = Math.max(1, bw - gap);
          const segs = series.map((s) => {
            const v = s.values[i] || 0;
            if (v <= 0) return null;
            const y1 = sy(acc + v), y0 = sy(acc);
            acc += v;
            return { s, y: y1, h: Math.max(0, y0 - y1 - (acc < totals[i] ? 2 : 0)) };   // a 2px gap between parts
          }).filter(Boolean) as { s: Series; y: number; h: number }[];
          return <g key={i} onPointerEnter={() => setHover(i)} onClick={() => onPick?.(i)} style={{ cursor: onPick ? "pointer" : "default" }}
            className={hover === i ? "col-hover" : ""}>
            <rect x={pad.l + i * bw} y={pad.t} width={bw} height={ih} fill="transparent" />
            {segs.map((g, k) => <rect key={k} x={x} y={g.y} width={w} height={g.h} fill={g.s.color}
              rx={k === segs.length - 1 ? Math.min(4, w / 2) : 0} className="col-seg" />)}
            {i % every === 0 && <text x={x + w / 2} y={H - 6} className="chart-axis" textAnchor="middle">{l}</text>}
          </g>;
        })}
      </svg>}
      {hover != null && <div className={`chart-tip${pad.l + hover * bw > W * 0.62 ? " left" : ""}`} style={{ left: pad.l + (hover + 0.5) * bw }}>
        <div className="chart-tip-time">{tipTitle ? tipTitle(hover) : labels[hover]}</div>
        <div className="chart-tip-row"><i style={{ background: "var(--text-soft)" }} /><b>{format(totals[hover])}</b><span>in all</span></div>
        {[...series].reverse().filter((s) => (s.values[hover] || 0) > 0).map((s) => <div key={s.key} className="chart-tip-row">
          <i style={{ background: s.color }} /><b>{format(s.values[hover] as number)}</b><span>{s.label}</span></div>)}
      </div>}
    </div>
  );
}

const WEEK = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const RAMP = ["var(--q1)", "var(--q2)", "var(--q3)", "var(--q4)", "var(--q5)", "var(--q6)"];

/** The hours of the week, darker where more was done. */
export function WeekHeat({ data, format }: { data: number[][]; format: (v: number) => string }) {
  const [hover, setHover] = useState<[number, number] | null>(null);
  const max = Math.max(0, ...data.flat());
  const level = (v: number) => (v <= 0 || !max ? -1 : Math.min(RAMP.length - 1, Math.floor((Math.sqrt(v / max)) * RAMP.length)));
  return (
    <div className="heat" onPointerLeave={() => setHover(null)}>
      <div className="heat-grid">
        <span />
        {Array.from({ length: 24 }, (_, h) => <span key={h} className="heat-hour">{h % 3 === 0 ? h : ""}</span>)}
        {data.map((row, d) => <React.Fragment key={d}>
          <span className="heat-day">{WEEK[d]}</span>
          {row.map((v, h) => {
            const l = level(v);
            return <span key={h} className={`heat-cell${hover && hover[0] === d && hover[1] === h ? " on" : ""}`}
              style={{ background: l < 0 ? "var(--surface-2)" : RAMP[l] }} onPointerEnter={() => setHover([d, h])}
              tabIndex={-1} aria-label={`${WEEK[d]} ${h}:00 ${format(v)}`} />;
          })}
        </React.Fragment>)}
      </div>
      <div className="heat-foot">
        <span className="muted small">{hover ? <><b>{format(data[hover[0]][hover[1]])}</b> on {WEEK[hover[0]]}s, {String(hover[1]).padStart(2, "0")}:00–{String(hover[1] + 1).padStart(2, "0")}:00</> : "Point at a square for its total"}</span>
        <span className="heat-scale"><span className="muted small">less</span>{RAMP.map((c) => <i key={c} style={{ background: c }} />)}<span className="muted small">more</span></span>
      </div>
    </div>
  );
}

/** Rows with a bar each (share of the largest), a label and a value. */
export function BarList({ rows, format, onPick }: {
  rows: { key: string; label: React.ReactNode; value: number; color?: string; sub?: React.ReactNode }[];
  format: (v: number) => string; onPick?: (key: string) => void;
}) {
  const max = Math.max(1, ...rows.map((r) => r.value));
  return <div className="barlist">{rows.map((r) => (
    <div key={r.key} className={`barlist-row${onPick ? " pickable" : ""}`} onClick={() => onPick?.(r.key)}>
      <div className="barlist-top"><span className="ellipsis">{r.label}</span><b>{format(r.value)}</b></div>
      <div className="barlist-track"><span style={{ width: `${Math.max(1, (100 * r.value) / max)}%`, background: r.color || "var(--s1)" }} /></div>
      {r.sub && <div className="small muted">{r.sub}</div>}
    </div>))}
  </div>;
}

/** A small line of recent values (stat tiles). */
export function Spark({ values, color = "var(--s1)", max }: { values: (number | null | undefined)[]; color?: string; max?: number }) {
  const v = values.filter((x): x is number => x != null);
  if (v.length < 2) return <svg className="spark" />;
  const top = max ?? Math.max(1e-9, ...v);
  const pts = v.map((x, i) => `${((i / (v.length - 1)) * 100).toFixed(2)},${(28 - (Math.min(x, top) / top) * 26).toFixed(2)}`);
  return <svg className="spark" viewBox="0 0 100 30" preserveAspectRatio="none">
    <path d={`M0,30L${pts.join("L")}L100,30Z`} fill={color} className="chart-wash" />
    <path d={`M${pts.join("L")}`} stroke={color} className="chart-line spark-line" vectorEffect="non-scaling-stroke" />
  </svg>;
}

export function Tile({ label, value, sub, spark, title, tone }: {
  label: string; value: React.ReactNode; sub?: React.ReactNode; spark?: React.ReactNode; title?: string; tone?: "good" | "warn" | "bad";
}) {
  return <div className={`tile${tone ? " tile-" + tone : ""}`} title={title}>
    <div className="tile-label">{label}</div>
    <div className="tile-value">{value}</div>
    {sub && <div className="tile-sub">{sub}</div>}
    {spark}
  </div>;
}

// formatting shared by Performance and Usage
export const fmt = {
  pct: (v: number) => `${Math.round(v)}%`,
  mb: (v: number) => (v >= 1024 ? `${(v / 1024).toFixed(v >= 10240 ? 0 : 1)} GB` : `${Math.round(v)} MB`),
  rate: (v: number) => {
    const u = ["B/s", "KB/s", "MB/s", "GB/s"];
    let i = 0;
    while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
    return `${v.toFixed(v >= 10 || i === 0 ? 0 : 1)} ${u[i]}`;
  },
  tokens: (v: number) => (v >= 1e6 ? `${(v / 1e6).toFixed(v >= 1e7 ? 0 : 1)}M` : v >= 1e4 ? `${Math.round(v / 1e3)}k` : v >= 1000 ? `${(v / 1e3).toFixed(1)}k` : `${Math.round(v)}`),
  tps: (v: number) => `${v >= 10 ? Math.round(v) : v.toFixed(1)} tok/s`,
  temp: (v: number) => `${Math.round(v)} °C`,
  watts: (v: number) => `${v >= 10 ? Math.round(v) : v.toFixed(1)} W`,
  dur: (s: number) => s < 90 ? `${Math.round(s)} s` : s < 5400 ? `${Math.round(s / 60)} min` : `${(s / 3600).toFixed(s >= 36000 ? 0 : 1)} h`,
  num: (v: number) => Math.round(v).toLocaleString(),
};

/** Change against an earlier period, as "+12%" (or "new"). */
export function delta(now: number, before: number) {
  if (!before) return now ? "new" : "";
  const d = Math.round((100 * (now - before)) / before);
  return d === 0 ? "same as before" : `${d > 0 ? "+" : "−"}${Math.abs(d)}% vs the period before`;
}

export function useInterval(fn: () => void, ms: number | null) {
  const ref = useRef(fn);
  ref.current = fn;
  useEffect(() => {
    if (ms == null) return;
    const t = setInterval(() => ref.current(), ms);
    return () => clearInterval(t);
  }, [ms]);
}
