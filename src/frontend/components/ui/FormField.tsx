"use client";

import { createContext, useContext, type ComponentPropsWithRef, type ReactNode } from "react";

const FieldContext = createContext<{ id: string; describedBy?: string; invalid: boolean } | null>(null);

/** Controls inherit their label, hint, and error associations from FormField. */
export function Input({ type = "text", className = "", ...props }: ComponentPropsWithRef<"input">) {
  const field = useContext(FieldContext);
  return <input id={field?.id} aria-describedby={field?.describedBy} aria-invalid={field?.invalid || undefined} {...props} type={type} className={`${type === "checkbox" ? "checkbox-control" : "form-control"} ${className}`.trim()} />;
}

export function Select({ className = "", ...props }: ComponentPropsWithRef<"select">) {
  const field = useContext(FieldContext);
  return <select id={field?.id} aria-describedby={field?.describedBy} aria-invalid={field?.invalid || undefined} {...props} className={`form-control ${className}`.trim()} />;
}

export function FormField({ id, label, labelAction, hint, error, children, className = "field" }: {
  id: string;
  label: ReactNode;
  labelAction?: ReactNode;
  hint?: ReactNode;
  error?: string;
  children: ReactNode;
  className?: string;
}) {
  const describedBy = [hint && `${id}-hint`, error && `${id}-error`].filter(Boolean).join(" ") || undefined;
  return <FieldContext.Provider value={{ id, describedBy, invalid: Boolean(error) }}>
    <div className={className || undefined}>
      {labelAction ? <div className="field-label-row"><label htmlFor={id}>{label}</label>{labelAction}</div> : <label htmlFor={id}>{label}</label>}
      {children}
      {hint && <div className="hint" id={`${id}-hint`}>{hint}</div>}
      {error && <div id={`${id}-error`} className="field-error" role="alert">{error}</div>}
    </div>
  </FieldContext.Provider>;
}

export function Checkbox({ children, ...props }: Omit<ComponentPropsWithRef<"input">, "type">) {
  return <label className="check"><Input {...props} type="checkbox" />{children}</label>;
}
