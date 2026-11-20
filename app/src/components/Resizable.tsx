import React, { useCallback, useEffect, useRef, useState } from "react";

// 2.27.0: every part of the window can be made wider or narrower (and charts taller or shorter) by dragging the
// line between parts (the pointer turns into a double-headed arrow). Sizes are remembered on this computer.
// Double-click a line to go back to the original size; with the keyboard, Tab to the line and use the arrow keys
// (Shift for bigger steps, Home to reset).

const PREFIX = "kimilm.size.";

function stored(key: string, initial: number) {
  try {
    const v = localStorage.getItem(PREFIX + key);
    const n = v === null ? NaN : Number(v);
    return Number.isFinite(n) ? n : initial;
  } catch { return initial; }
}

/** A size (pixels, or a fraction for split panes) that is kept between starts. */
export function useSize(key: string, initial: number, min: number, max: number): [number, (v: number) => void, () => void] {
  const clamp = (v: number) => Math.min(max, Math.max(min, v));
  const [size, setSize] = useState(() => clamp(stored(key, initial)));
  const save = useCallback((v: number) => {
    const c = clamp(v);
    setSize(c);
    try { localStorage.setItem(PREFIX + key, String(Math.round(c * 1000) / 1000)); } catch {}
  }, [key, min, max]);
  const reset = useCallback(() => {
    setSize(clamp(initial));
    try { localStorage.removeItem(PREFIX + key); } catch {}
  }, [key, initial]);
  return [size, save, reset];
}

type HandleProps = {
  /** "x": drag left and right (between columns); "y": drag up and down (below a chart) */
  axis?: "x" | "y";
  /** called with how far the pointer moved since the drag began, in pixels */
  onDrag: (delta: number, start: number) => void;
  /** the size when the drag began (passed back as `start`) */
  value: number;
  onReset?: () => void;
  onStep?: (delta: number) => void;
  label?: string;
  className?: string;
  min?: number;
  max?: number;
};

/** The line between two parts. Drag it, double-click it, or focus it and use the arrow keys. */
export function Handle({ axis = "x", onDrag, value, onReset, onStep, label = "Drag to resize", className = "", min, max }: HandleProps) {
  const start = useRef<{ pos: number; value: number } | null>(null);
  const [dragging, setDragging] = useState(false);
  const down = (e: React.PointerEvent) => {
    if (e.button !== 0) return;
    e.preventDefault();
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
    start.current = { pos: axis === "x" ? e.clientX : e.clientY, value };
    setDragging(true);
  };
  const move = (e: React.PointerEvent) => {
    if (!start.current) return;
    const pos = axis === "x" ? e.clientX : e.clientY;
    onDrag(pos - start.current.pos, start.current.value);
  };
  const up = (e: React.PointerEvent) => {
    start.current = null;
    setDragging(false);
    try { (e.target as HTMLElement).releasePointerCapture(e.pointerId); } catch {}
  };
  useEffect(() => {
    if (!dragging) return;
    document.body.classList.add(axis === "x" ? "resizing-x" : "resizing-y");
    return () => document.body.classList.remove("resizing-x", "resizing-y");
  }, [dragging, axis]);
  const key = (e: React.KeyboardEvent) => {
    const step = e.shiftKey ? 64 : 16;
    const back = axis === "x" ? "ArrowLeft" : "ArrowUp", fwd = axis === "x" ? "ArrowRight" : "ArrowDown";
    if (e.key === back || e.key === fwd) {
      e.preventDefault();
      const d = e.key === fwd ? step : -step;
      onStep ? onStep(d) : onDrag(d, value);
    } else if (e.key === "Home" && onReset) { e.preventDefault(); onReset(); }
  };
  return (
    <div className={`resize-handle resize-${axis}${dragging ? " dragging" : ""} ${className}`}
      role="separator" aria-orientation={axis === "x" ? "vertical" : "horizontal"} aria-label={label}
      aria-valuenow={Math.round(value)} aria-valuemin={min} aria-valuemax={max} tabIndex={0}
      title={`${label} · double-click to reset`}
      onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={up}
      onDoubleClick={onReset} onKeyDown={key}>
      <span className="resize-grip" aria-hidden>{axis === "x" ? "⟷" : "↕"}</span>
    </div>
  );
}

/** Two columns with a line between them that can be dragged. The left column's share of the width is kept
 *  (so it follows the window when that is resized). Anything other than exactly two children is laid out as
 *  before, without a line. Below `stackBelow` pixels the columns stack, as they always did. */
export function SplitPane({ id, className = "", initial = 0.5, min = 0.2, max = 0.8, children, style }: {
  id: string; className?: string; initial?: number; min?: number; max?: number; children: React.ReactNode; style?: React.CSSProperties;
}) {
  const parts = React.Children.toArray(children).filter(Boolean);
  const box = useRef<HTMLDivElement>(null);
  const [frac, setFrac, reset] = useSize("split." + id, initial, min, max);
  if (parts.length !== 2) return <div className={className} style={style}>{children}</div>;
  const width = () => box.current?.getBoundingClientRect().width || 1;
  return (
    <div ref={box} className={`${className} splitpane`}
      style={{ ...style, ["--split" as any]: `${(frac * 100).toFixed(2)}%` }}>
      {parts[0]}
      <Handle axis="x" value={frac} onReset={reset} min={min} max={max}
        onDrag={(d, start) => setFrac(start + d / width())}
        onStep={(d) => setFrac(frac + d / width())} label="Drag to make the left side wider or narrower" />
      {parts[1]}
    </div>
  );
}

/** A box whose height can be dragged from its bottom edge (charts, logs). */
export function TallBox({ id, initial, min = 120, max = 900, children, className = "" }: {
  id: string; initial: number; min?: number; max?: number; children: (height: number) => React.ReactNode; className?: string;
}) {
  const [h, setH, reset] = useSize("height." + id, initial, min, max);
  return (
    <div className={`tallbox ${className}`}>
      {children(h)}
      <Handle axis="y" value={h} onReset={reset} min={min} max={max} onDrag={(d, start) => setH(start + d)}
        onStep={(d) => setH(h + d)} label="Drag to make this taller or shorter" />
    </div>
  );
}
