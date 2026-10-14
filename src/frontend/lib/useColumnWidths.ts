"use client";

import { useCallback, useEffect, useRef, useState, type PointerEvent, type KeyboardEvent } from "react";

const MIN_WIDTH = 110;

/** Fit saved proportions to the viewport while reserving space for every column. */
function fitWidths(values: number[], total: number): number[] {
  const extra = Math.max(0, total - values.length * MIN_WIDTH);
  const weights = values.map(value => Math.max(0, value - MIN_WIDTH));
  const sum = weights.reduce((a, b) => a + b, 0);
  return weights.map(weight => MIN_WIDTH + extra * (sum ? weight / sum : 1 / values.length));
}

/** Move the shared boundary between two columns without moving either table edge. */
function moveBoundary(widths: number[], index: number, delta: number): number[] {
  const next = [...widths];
  const pair = widths[index] + widths[index + 1];
  next[index] = Math.max(MIN_WIDTH, Math.min(pair - MIN_WIDTH, widths[index] + delta));
  next[index + 1] = pair - next[index];
  return next;
}

export function useColumnWidths(storageKey: string, count: number) {
  const table = useRef<HTMLTableElement | null>(null);
  const [tableNode, setTableNode] = useState<HTMLTableElement | null>(null);
  const tableRef = useCallback((node: HTMLTableElement | null) => {
    table.current = node;
    setTableNode(node);
  }, []);
  const drag = useRef<{ index: number; x: number; widths: number[] } | null>(null);
  const currentWidths = useRef<number[] | null>(null);
  const [widths, setWidths] = useState<number[] | null>(null);

  function measuredWidths(): number[] {
    return Array.from(table.current?.querySelectorAll("thead th") ?? []).map(cell => cell.getBoundingClientRect().width);
  }

  function remember(next: number[]) {
    currentWidths.current = next;
    setWidths(next);
    try { localStorage.setItem(storageKey, JSON.stringify(next)); } catch { /* Resizing still works without storage. */ }
  }

  useEffect(() => {
    if (!tableNode?.parentElement) return;
    const container = tableNode.parentElement;
    let saved: number[] | null = null;
    try {
      const parsed: unknown = JSON.parse(localStorage.getItem(storageKey) ?? "null");
      if (Array.isArray(parsed) && parsed.length === count && parsed.every(value => typeof value === "number" && Number.isFinite(value) && value > 0)) saved = parsed;
    } catch { /* Storage can be unavailable or contain outdated data. */ }
    currentWidths.current = saved;
    const fit = () => {
      const initial = currentWidths.current ?? measuredWidths();
      if (initial.length !== count) return;
      const minimum = parseFloat(getComputedStyle(tableNode).minWidth) || count * MIN_WIDTH;
      const total = Math.max(container.clientWidth, minimum, count * MIN_WIDTH);
      const previousTotal = initial.reduce((a, b) => a + b, 0);
      if (currentWidths.current && initial.every(width => width >= MIN_WIDTH) && Math.abs(previousTotal - total) < 0.5) {
        setWidths(initial);
        return;
      }
      drag.current = null;
      remember(fitWidths(initial, total));
    };
    fit();
    const observer = new ResizeObserver(fit);
    observer.observe(container);
    return () => observer.disconnect();
  }, [tableNode, storageKey, count]);

  function onPointerDown(index: number, event: PointerEvent<HTMLButtonElement>) {
    if (event.button !== 0 || index >= count - 1) return;
    event.preventDefault();
    const current = measuredWidths();
    if (current.length !== count) return;
    drag.current = { index, x: event.clientX, widths: current };
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function onPointerMove(event: PointerEvent<HTMLButtonElement>) {
    if (!drag.current) return;
    const { index, x, widths: initial } = drag.current;
    remember(moveBoundary(initial, index, event.clientX - x));
  }

  function onPointerEnd() { drag.current = null; }

  function onKeyDown(index: number, event: KeyboardEvent<HTMLButtonElement>) {
    if ((event.key !== "ArrowLeft" && event.key !== "ArrowRight") || index >= count - 1) return;
    event.preventDefault();
    const current = measuredWidths();
    if (current.length !== count) return;
    remember(moveBoundary(current, index, event.key === "ArrowRight" ? 20 : -20));
  }

  return { tableRef, widths, onPointerDown, onPointerMove, onPointerEnd, onKeyDown };
}
