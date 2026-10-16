import type { ComponentPropsWithoutRef } from "react";

export function EmptyState({ className = "", ...props }: ComponentPropsWithoutRef<"div">) {
  return <div {...props} className={`empty ${className}`.trim()} />;
}

export function ErrorMessage({ className = "", ...props }: ComponentPropsWithoutRef<"div">) {
  return <div role="alert" {...props} className={`error-box ${className}`.trim()} />;
}
