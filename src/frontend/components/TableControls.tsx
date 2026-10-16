"use client";

import { Button } from "@/components/ui/Button";
import { Icon } from "./Icon";
import { useState } from "react";
import { useColumnWidths } from "@/lib/useColumnWidths";

export function useTableSort<T>(rows: T[], values: ((row: T) => string | number | null | undefined)[], initial = 0, descending = false) {
  const [sort, setSort] = useState({ index: initial, descending });
  const sorted = [...rows].sort((a, b) => {
    const left = values[sort.index](a), right = values[sort.index](b);
    const comparison = left == null ? (right == null ? 0 : 1) : right == null ? -1 :
      typeof left === "number" && typeof right === "number" ? left - right :
      String(left).localeCompare(String(right), undefined, { numeric: true, sensitivity: "base" });
    return sort.descending ? -comparison : comparison;
  });
  return { sorted, sort, toggle: (index: number) => setSort(current => ({ index, descending: current.index === index ? !current.descending : false })) };
}

export function TableHeader({ labels, sizing, sorting, sortableCount = labels.length }: {
  labels: string[];
  sizing: ReturnType<typeof useColumnWidths>;
  sorting: { sort: { index: number; descending: boolean }; toggle: (index: number) => void };
  sortableCount?: number;
}) {
  return <thead><tr>{labels.map((label, index) => <th key={label} scope="col" aria-sort={index >= sortableCount ? undefined : sorting.sort.index === index ? sorting.sort.descending ? "descending" : "ascending" : "none"}>
    {index < sortableCount ? <Button className="sort-button" onClick={() => sorting.toggle(index)}><span className="sort-label">{label}</span><Icon name={sorting.sort.index === index ? sorting.sort.descending ? "arrow-down" : "arrow-up" : "sort"} /></Button> : label}
    {index < labels.length - 1 && <Button type="button" className="column-resizer" aria-label={`Resize ${label} column`} title="Drag to resize; use arrow keys when focused" onPointerDown={event => sizing.onPointerDown(index, event)} onPointerMove={sizing.onPointerMove} onPointerUp={sizing.onPointerEnd} onPointerCancel={sizing.onPointerEnd} onLostPointerCapture={sizing.onPointerEnd} onKeyDown={event => sizing.onKeyDown(index, event)} />}
  </th>)}</tr></thead>;
}
