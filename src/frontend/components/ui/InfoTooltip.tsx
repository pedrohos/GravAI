import type { ReactNode } from "react";
import { Icon } from "../Icon";
import { Button } from "./Button";

export function InfoTooltip({ id, label, children }: { id: string; label: string; children: ReactNode }) {
  return <span className="info-tooltip"><Button className="info-trigger" aria-label={label} aria-describedby={id}><Icon name="info" /></Button><span id={id} role="tooltip" className="info-content">{children}</span></span>;
}
