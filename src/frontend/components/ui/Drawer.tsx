import type { ComponentPropsWithoutRef, ReactNode } from "react";

export function Drawer({ summary, summaryTitle, bodyClassName, muted = false, unavailable = false, className = "", children, ...props }: Omit<ComponentPropsWithoutRef<"details">, "title"> & {
  summary: ReactNode;
  summaryTitle?: string;
  bodyClassName?: string;
  muted?: boolean;
  unavailable?: boolean;
}) {
  const classes = `drawer ${muted || unavailable ? "unavailable" : ""} ${className}`.trim();
  if (unavailable) return <div className={classes}><span className="unavailable-label" aria-disabled="true" title={summaryTitle}>{summary}</span></div>;
  return <details {...props} className={classes}><summary title={summaryTitle}>{summary}</summary><div className={bodyClassName}>{children}</div></details>;
}
