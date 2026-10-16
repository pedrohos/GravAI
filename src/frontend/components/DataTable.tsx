import type { ReactNode } from "react";
import type { useColumnWidths } from "@/lib/useColumnWidths";
import { TableHeader } from "./TableControls";

/** Shared column sizing, sorting, and table markup for jobs and recordings. */
export function DataTable({ columns, defaultWidths, sizing, sorting, children }: {
  columns: string[];
  defaultWidths: number[];
  sizing: ReturnType<typeof useColumnWidths>;
  sorting: { sort: { index: number; descending: boolean }; toggle: (index: number) => void };
  children: ReactNode;
}) {
  return <table className="data-table" ref={sizing.tableRef} style={sizing.widths ? { width: sizing.widths.reduce((sum, width) => sum + width, 0) } : undefined}>
    <colgroup>{columns.map((label, index) => <col key={label} style={{ width: sizing.widths ? sizing.widths[index] : `${defaultWidths[index]}%` }} />)}</colgroup>
    <TableHeader labels={columns} sizing={sizing} sorting={sorting} sortableCount={columns.length - 1} />
    {children}
  </table>;
}
