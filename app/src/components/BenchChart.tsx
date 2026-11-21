import React, { useEffect, useRef, useState } from "react";
import { SLOTS } from "./Charts";

// 2.30.0: a bench device's readings over device time (temperatures may be below zero), the set points it is
// asked to hold (dashed), and its outputs as strips underneath (filled while on, shaded by level).

export type BenchRow = { t: number; mode: string;[k: string]: any };

function useWidth() {
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(600);
  useEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver((e) => setW(e[0].contentRect.width));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return [ref, w] as const;
}

function niceTicks(lo: number, hi: number, n = 5) {
  const span = Math.max(1e-6, hi - lo), raw = span / n, mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => span / s <= n) || 10 * mag;
  const out: number[] = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(6));
  return out;
}

const dur = (s: number) => (Math.abs(s) >= 3600 ? `${(s / 3600).toFixed(Math.abs(s) >= 36000 ? 0 : 1)} h` : `${Math.round(s / 60)} min`);

export function BenchChart({ rows, sensors, outputs, setpoints, height = 260 }: {
  rows: BenchRow[]; sensors: { key: string; label: string; unit: string }[]; outputs: { key: string; label: string }[];
  setpoints: Record<string, number>; height?: number;
}) {
  const [ref, width] = useWidth();
  const [hover, setHover] = useState<number | null>(null);
  const pad = { l: 78, r: 12, t: 10, b: 22 };
  const strip = 16, gap = 6;
  const W = Math.max(200, width), plotH = height;
  const H = plotH + outputs.length * (strip + 4) + gap;
  if (!rows.length) return <div ref={ref} className="muted small" style={{ padding: 20 }}>No readings yet.</div>;
  const t0 = rows[0].t, t1 = rows[rows.length - 1].t, now = t1;
  const vals = sensors.flatMap((s) => rows.map((r) => r[s.key]).filter((v) => typeof v === "number")) as number[];
  Object.entries(setpoints).forEach(([k, v]) => { if (sensors.some((s) => s.key === k)) vals.push(v); });
  let lo = Math.min(...vals), hi = Math.max(...vals);
  if (!isFinite(lo)) { lo = 0; hi = 1; }
  const padV = Math.max(0.5, (hi - lo) * 0.08); lo -= padV; hi += padV;
  const ys = niceTicks(lo, hi);
  const iw = W - pad.l - pad.r, ih = plotH - pad.t - pad.b;
  const sx = (t: number) => pad.l + ((t - t0) / Math.max(1e-9, t1 - t0)) * iw;
  const sy = (v: number) => pad.t + ih - ((v - lo) / (hi - lo)) * ih;
  const path = (key: string) => {
    let d = "", pen = false;
    rows.forEach((r) => {
      const v = r[key];
      if (typeof v !== "number") { pen = false; return; }
      d += `${pen ? "L" : "M"}${sx(r.t).toFixed(1)},${sy(v).toFixed(1)}`; pen = true;
    });
    return d;
  };
  const xt = niceTicks(t0 - now, 0, 5);
  const onMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const box = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
    const x = e.clientX - box.left, t = t0 + ((x - pad.l) / iw) * (t1 - t0);
    let best = 0;
    for (let i = 0; i < rows.length; i++) if (Math.abs(rows[i].t - t) < Math.abs(rows[best].t - t)) best = i;
    setHover(x >= pad.l && x <= W - pad.r ? best : null);
  };
  const h = hover != null ? rows[hover] : null;
  return (
    <div ref={ref} style={{ position: "relative" }}>
      <svg width={W} height={H} onMouseMove={onMove} onMouseLeave={() => setHover(null)} role="img"
        aria-label={`Readings over the last ${dur(t1 - t0)} of device time`}>
        {ys.map((v) => <g key={v}>
          <line x1={pad.l} x2={W - pad.r} y1={sy(v)} y2={sy(v)} stroke="var(--line)" strokeWidth={v === 0 ? 1.2 : 0.6} />
          <text x={pad.l - 6} y={sy(v) + 4} textAnchor="end" fontSize={11} fill="var(--text-soft)">{v}</text></g>)}
        {xt.map((v) => <text key={v} x={sx(now + v)} y={plotH - 6} textAnchor="middle" fontSize={11} fill="var(--text-soft)">{v === 0 ? "now" : `−${dur(-v)}`}</text>)}
        {sensors.map((s, i) => setpoints[s.key] != null && <line key={s.key + "sp"} x1={pad.l} x2={W - pad.r} y1={sy(setpoints[s.key])} y2={sy(setpoints[s.key])}
          stroke={SLOTS[i % SLOTS.length]} strokeDasharray="5 4" strokeWidth={1.2} opacity={0.8} />)}
        {sensors.map((s, i) => <path key={s.key} d={path(s.key)} fill="none" stroke={SLOTS[i % SLOTS.length]} strokeWidth={2} strokeLinejoin="round" />)}
        {outputs.map((o, j) => {
          const y = plotH + gap + j * (strip + 4);
          return <g key={o.key}>
            <text x={pad.l - 6} y={y + strip - 4} textAnchor="end" fontSize={11} fill="var(--text-soft)">{o.label}</text>
            <rect x={pad.l} y={y} width={iw} height={strip} rx={3} fill="var(--surface-2)" />
            {rows.map((r, i) => {
              const v = r[o.key], next = rows[i + 1];
              if (typeof v !== "number" || v <= 0 || !next) return null;
              return <rect key={i} x={sx(r.t)} y={y} width={Math.max(0.6, sx(next.t) - sx(r.t))} height={strip}
                fill={SLOTS[(sensors.length + j) % SLOTS.length]} opacity={0.25 + 0.75 * Math.min(1, v)} />;
            })}
          </g>;
        })}
        {h && <line x1={sx(h.t)} x2={sx(h.t)} y1={pad.t} y2={H} stroke="var(--text-soft)" strokeWidth={0.8} />}
      </svg>
      {h && <div className="chart-tip" style={{ position: "absolute", top: 8, left: Math.min(sx(h.t) + 10, W - 200), pointerEvents: "none" }}>
        <div className="small muted">{t1 - h.t < 1 ? "now" : `${dur(t1 - h.t)} ago`} · {h.mode}</div>
        {sensors.map((s, i) => <div key={s.key} className="small"><span style={{ color: SLOTS[i % SLOTS.length] }}>●</span> {s.label}: <b>{typeof h[s.key] === "number" ? `${h[s.key].toFixed(1)} ${s.unit}` : "—"}</b></div>)}
        {outputs.map((o) => <div key={o.key} className="small">{o.label}: <b>{typeof h[o.key] === "number" ? (h[o.key] === 1 ? "on" : h[o.key] === 0 ? "off" : `${Math.round(h[o.key] * 100)} %`) : "—"}</b></div>)}
      </div>}
      <div className="row gap-m wrap small" style={{ marginTop: 6 }}>
        {sensors.map((s, i) => <span key={s.key}><span style={{ color: SLOTS[i % SLOTS.length] }}>━</span> {s.label}{setpoints[s.key] != null ? ` (goal ${setpoints[s.key]} ${s.unit}, dashed)` : ""}</span>)}
      </div>
    </div>
  );
}
