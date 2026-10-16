import type { ComponentPropsWithRef } from "react";

type ButtonProps = ComponentPropsWithRef<"button"> & {
  variant?: "default" | "primary" | "danger";
  size?: "default" | "small";
};

/** Native button behavior and refs, with shared visual variants. */
export function Button({ variant = "default", size = "default", type = "button", className = "", ...props }: ButtonProps) {
  const classes = ["button", variant !== "default" && variant, size !== "default" && size, className].filter(Boolean).join(" ");
  return <button {...props} type={type} className={classes || undefined} />;
}
