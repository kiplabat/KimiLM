import React, { useEffect, useRef, useState } from "react";

// 2.32.0: the details of what you choose in a list appear right below it, so nobody scrolls to the bottom of the
// page to see them. Pages with a list beside its details (Skills, Harnesses) keep the two columns on a wide window
// (the details stay in view beside the list) and put the details under the chosen row on a narrow one, where the
// columns would otherwise stack with the details last. Grids of cards (Devices) always open the details under the
// chosen card's row (`.inline-grid`: grid-auto-flow dense lets the cards after it fill that row first).

/** True while the window is narrower than `px` (the width below which two columns stack). */
export function useNarrow(px = 1100) {
  const query = `(max-width: ${px}px)`;
  const [narrow, setNarrow] = useState(() => typeof window !== "undefined" && !!window.matchMedia?.(query).matches);
  useEffect(() => {
    const m = window.matchMedia?.(query);
    if (!m) return;
    const on = () => setNarrow(m.matches);
    on();
    m.addEventListener?.("change", on);
    return () => m.removeEventListener?.("change", on);
  }, [query]);
  return narrow;
}

/** The details of the chosen item, placed right after it in a list or a grid, brought into view when it opens. */
export function InlineDetail({ id, children, className = "" }: { id: string; children: React.ReactNode; className?: string }) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = box.current;
    if (!el || !el.scrollIntoView) return;
    const t = setTimeout(() => {
      const r = el.getBoundingClientRect();
      if (r.top < 0 || r.top > window.innerHeight - 120) el.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }, 60);
    return () => clearTimeout(t);
  }, [id]);
  return <div ref={box} className={`inline-detail ${className}`} data-for={id}>{children}</div>;
}

/** Words typed in a search box, matched (all of them) against an item's text. */
export function matches(q: string, ...texts: any[]) {
  const terms = q.trim().toLowerCase().split(/\s+/).filter(Boolean);
  if (!terms.length) return true;
  const hay = texts.flat().filter((t) => t != null).map(String).join(" ").toLowerCase();
  return terms.every((t) => hay.includes(t));
}
