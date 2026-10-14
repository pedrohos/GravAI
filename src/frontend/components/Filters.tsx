import type { ReactNode } from "react";

export function Filters({ children }: { children: ReactNode }) {
  return <fieldset className="filters row-end"><legend>Filters</legend><div className="row job-filters">{children}</div></fieldset>;
}

export function FilterField({ label, children }: { label: string; children: ReactNode }) {
  return <div className="filter-field"><span>{label}</span>{children}</div>;
}
