import type { ComponentPropsWithoutRef } from "react";

/** Keep wide tables inside a keyboard-accessible scrolling region. */
export function TableScroll({ className = "", ...props }: ComponentPropsWithoutRef<"div">) {
  return <div tabIndex={0} {...props} className={`table-scroll ${className}`.trim()} />;
}
