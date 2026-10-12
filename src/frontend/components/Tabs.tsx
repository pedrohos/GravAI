"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const TABS = [
  ["/jobs", "Jobs"],
  ["/recordings", "Recordings"],
  ["/settings", "Settings"],
] as const;

export function Tabs() {
  const pathname = usePathname();

  return (
    <nav className="tabs">
      {TABS.map(([href, label]) => (
        <Link
          key={href}
          href={href}
          // One recording is under /recordings/<id>, and it is still the
          // Recordings tab, so this is a prefix match rather than an equality.
          className={pathname.startsWith(href) ? "active" : undefined}
        >
          {label}
        </Link>
      ))}
    </nav>
  );
}
