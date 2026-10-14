import type { Metadata } from "next";
import Link from "next/link";
import Image from "next/image";

import { CaptchaBanner } from "@/components/CaptchaBanner";
import { Icon } from "@/components/Icon";
import { Tabs } from "@/components/Tabs";
import { ToastProvider } from "@/components/Toast";

import "./globals.css";

export const metadata: Metadata = {
  title: "GravAI",
  icons: {
    icon: "/icons/app-icon.png",
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <ToastProvider>
          <header className="topbar">
            <Link className="brand" href="/jobs">
              <Image src="/icons/app-icon.png" alt="" width={28} height={28} className="app-icon" />
              <span>GravAI</span>
            </Link>
            <Tabs />
            <a className="docs-link" href="/docs" target="_blank" rel="noreferrer">
              API docs <Icon name="external" />
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
