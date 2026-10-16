"use client";

import { Button } from "@/components/ui/Button";
import { useEffect, useId, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { Icon } from "./Icon";

export interface DropdownOption {
  value: string;
  label: string;
  content: ReactNode;
}

/** A single-select dropdown whose selected value and options can render components. */
export function Dropdown({ label, value, onChange, options }: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: DropdownOption[];
}) {
  const id = useId();
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const selected = options.findIndex(option => option.value === value);
  const activeIndex = Math.min(active, options.length - 1);
  const search = useRef({ text: "", time: 0 });

  useEffect(() => {
    if (!open) return;
    const closeOutside = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", closeOutside);
    return () => document.removeEventListener("pointerdown", closeOutside);
  }, [open]);

  useEffect(() => {
    if (open) document.getElementById(`${id}-option-${activeIndex}`)?.scrollIntoView({ block: "nearest" });
  }, [open, activeIndex, id]);

  function choose(index: number) {
    if (!options[index]) return;
    onChange(options[index].value);
    setOpen(false);
    trigger.current?.focus();
  }

  function onKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    if (event.key === "Tab") { setOpen(false); return; }
    if (event.key === "Escape") { event.preventDefault(); setOpen(false); return; }
    if (["ArrowDown", "ArrowUp", "Home", "End", "Enter", " "].includes(event.key)) {
      event.preventDefault();
      if (event.key === "Enter" || event.key === " ") {
        if (open) choose(activeIndex);
        else { setActive(Math.max(selected, 0)); setOpen(true); }
        return;
      }
      if (event.key === "Home") setActive(0);
      else if (event.key === "End") setActive(options.length - 1);
      else if (!open) setActive(selected >= 0 ? selected : event.key === "ArrowUp" ? options.length - 1 : 0);
      else setActive((activeIndex + (event.key === "ArrowDown" ? 1 : -1) + options.length) % options.length);
      setOpen(true);
      return;
    }
    if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
      const now = Date.now();
      search.current = { text: (now - search.current.time < 700 ? search.current.text : "") + event.key.toLowerCase(), time: now };
      const index = options.findIndex(option => option.label.toLowerCase().startsWith(search.current.text));
      if (index >= 0) { setActive(index); setOpen(true); }
    }
  }

  return <div className="dropdown" ref={root} onBlur={event => {
    if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setOpen(false);
  }}>
    <Button ref={trigger} type="button" className="dropdown-trigger" role="combobox" aria-label={label} aria-haspopup="listbox" aria-expanded={open} aria-controls={`${id}-options`} aria-activedescendant={open && activeIndex >= 0 ? `${id}-option-${activeIndex}` : undefined} onKeyDown={onKeyDown} onClick={() => { setActive(Math.max(selected, 0)); setOpen(!open); }}>
      <span className="dropdown-value">{options[selected]?.content ?? "Select an option"}</span>
      <Icon name="chevron-down" />
    </Button>
    {open && <ul id={`${id}-options`} className="dropdown-options" role="listbox" aria-label={label}>
      {options.map((option, index) => <li id={`${id}-option-${index}`} key={option.value} role="option" aria-selected={option.value === value} className={`dropdown-option ${index === activeIndex ? "highlighted" : ""}`} onPointerMove={() => setActive(index)} onPointerDown={event => event.preventDefault()} onClick={() => choose(index)}>
        <span>{option.content}</span>{option.value === value && <Icon name="check" />}
      </li>)}
    </ul>}
  </div>;
}
