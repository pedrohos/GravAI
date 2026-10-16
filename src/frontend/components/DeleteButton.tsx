import { Button } from "@/components/ui/Button";
import type { ButtonHTMLAttributes } from "react";

export function DeleteButton({ children = "Delete", className = "", ...props }: ButtonHTMLAttributes<HTMLButtonElement>) {
  return <Button {...props} className={`small ${className.includes("delete-transcription") ? "" : "delete-both"} ${className}`}>{children}</Button>;
}
