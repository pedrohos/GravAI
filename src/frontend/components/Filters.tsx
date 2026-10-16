import { Icon } from "./Icon";
import { Button } from "./ui/Button";
import type { ReactNode } from "react";

export function Filters({ children }: { children: ReactNode }) {
  return <fieldset className="filters row-end"><legend>Filters</legend><div className="row job-filters">{children}</div></fieldset>;
}

export function FilterField({ label, children }: { label: string; children: ReactNode }) {
  return <div className="filter-field"><span>{label}</span>{children}</div>;
}

export function FilterChip({ label, onClear, children, className = "" }: {
  label: string;
  onClear: () => void;
  children: ReactNode;
  className?: string;
}) {
  return <span className={`filter-chip ${className}`.trim()}>{children}<Button aria-label={`Clear ${label} filter`} onClick={onClear}><Icon name="x" /></Button></span>;
}
