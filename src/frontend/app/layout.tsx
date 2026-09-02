import type { Metadata } from "next";
import Link from "next/link";

import { CaptchaBanner } from "@/components/CaptchaBanner";
import { Tabs } from "@/components/Tabs";
import { ToastProvider } from "@/components/Toast";

import "./globals.css";

export const metadata: Metadata = {
  title: "GravAI",
  icons: {
    icon:
      "data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'>" +
      "<text y='26' font-size='26'>🎙️</text></svg>",
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <ToastProvider>
          <header className="topbar">
            <Link className="brand" href="/jobs">
              <span className="brand-mark" aria-hidden="true">
                ◉
              </span>
              <span>GravAI</span>
            </Link>
            <Tabs />
            <a className="docs-link" href="/docs" target="_blank" rel="noreferrer">
              API docs ↗
            </a>
          </header>

          {/* A CAPTCHA on a Google sign-in stops a recording dead until a person
              answers it, and it expires. It is the one thing worth interrupting
              whatever page somebody is on, so it lives outside the view. */}
          <CaptchaBanner />

          <main className="view">{children}</main>
        </ToastProvider>
      </body>
    </html>
  );
}
