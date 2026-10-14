import type { ButtonHTMLAttributes } from "react";

export function DeleteButton({ children = "Delete", className = "", ...props }: ButtonHTMLAttributes<HTMLButtonElement>) {
  return <button {...props} className={`small ${className.includes("delete-transcription") ? "" : "delete-both"} ${className}`}>{children}</button>;
}
