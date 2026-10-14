"use client";

import { useEffect, useRef, useState, type PointerEvent, type KeyboardEvent } from "react";

const MIN_WIDTH = 110;
const MAX_WIDTH = 1600;
const clamp = (value: number) => Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, value));

/** Keep each table's column sizes across visits without requiring browser storage. */
export function useColumnWidths(storageKey: string, count: number) {
  const tableRef = useRef<HTMLTableElement>(null);
  const drag = useRef<{ index: number; x: number; widths: number[] } | null>(null);
  const [widths, setWidths] = useState<number[] | null>(null);

  useEffect(() => {
    try {
      const saved: unknown = JSON.parse(localStorage.getItem(storageKey) ?? "null");
      if (Array.isArray(saved) && saved.length === count && saved.every((value) => typeof value === "number" && Number.isFinite(value))) {
        setWidths(saved.map(clamp));
      }
    } catch { /* Storage can be unavailable or contain outdated data. */ }
  }, [storageKey, count]);

  function measuredWidths(): number[] {
    return Array.from(tableRef.current?.querySelectorAll("thead th") ?? []).map((cell) => clamp(cell.getBoundingClientRect().width));
  }

  function remember(next: number[]) {
    setWidths(next);
    try { localStorage.setItem(storageKey, JSON.stringify(next)); } catch { /* Resizing still works without storage. */ }
  }

  function onPointerDown(index: number, event: PointerEvent<HTMLButtonElement>) {
    if (event.button !== 0) return;
    event.preventDefault();
    const current = measuredWidths();
    if (current.length !== count) return;
    drag.current = { index, x: event.clientX, widths: current };
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function onPointerMove(event: PointerEvent<HTMLButtonElement>) {
    if (!drag.current) return;
    const { index, x, widths: initial } = drag.current;
    const next = [...initial];
    next[index] = clamp(initial[index] + event.clientX - x);
    remember(next);
  }

  function onPointerEnd() { drag.current = null; }

  function onKeyDown(index: number, event: KeyboardEvent<HTMLButtonElement>) {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    const next = measuredWidths();
    if (next.length !== count) return;
    next[index] = clamp(next[index] + (event.key === "ArrowRight" ? 20 : -20));
    remember(next);
  }

  return { tableRef, widths, onPointerDown, onPointerMove, onPointerEnd, onKeyDown };
}
