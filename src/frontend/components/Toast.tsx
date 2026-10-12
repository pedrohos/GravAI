"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { messageOf } from "@/lib/api";

type Kind = "ok" | "bad";

interface ToastApi {
  toast: (message: string, kind?: Kind) => void;
  /** Runs an API call, reporting whatever the API said went wrong. */
  attempt: <T>(action: () => Promise<T>, successMessage?: string) => Promise<T | null>;
}

const ToastContext = createContext<ToastApi | null>(null);

/** A failure is left up more than twice as long as a success, because one is
 *  read and the other is only noticed. */
const LINGER: Record<Kind, number> = { ok: 3500, bad: 8000 };

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [current, setCurrent] = useState<{ message: string; kind: Kind } | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const toast = useCallback((message: string, kind: Kind = "ok") => {
    setCurrent({ message, kind });
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setCurrent(null), LINGER[kind]);
  }, []);

  const attempt = useCallback(
    async <T,>(action: () => Promise<T>, successMessage?: string): Promise<T | null> => {
      try {
        const result = await action();
        if (successMessage) toast(successMessage);
        return result;
      } catch (error) {
        toast(messageOf(error), "bad");
        return null;
      }
    },
    [toast]
  );

  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);

  return (
    <ToastContext.Provider value={{ toast, attempt }}>
      {children}
      {current && <div id="toast" className={current.kind}>{current.message}</div>}
    </ToastContext.Provider>
  );
}

export function useToast(): ToastApi {
  const context = useContext(ToastContext);
  if (!context) throw new Error("useToast used outside ToastProvider");
  return context;
}
