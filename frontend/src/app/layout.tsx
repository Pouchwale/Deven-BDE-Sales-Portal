import type { Metadata, Viewport } from "next";
import { Mada, Mulish } from "next/font/google";

import { Providers } from "@/components/Providers";
import { THEME_INIT_SCRIPT } from "@/lib/theme";

import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: "BDE & Sales Portal",
    template: "%s · BDE & Sales Portal",
  },
  description:
    "Reference tracking, assigned leads and customer feedback over a real reporting hierarchy.",
};

// PouchWale design system: Mada (heavy display, headlines) and Mulish (body).
// Self-hosted by next/font - no request to Google at runtime, which the
// portal's Content-Security-Policy would refuse anyway.
const display = Mada({ subsets: ["latin"], variable: "--font-mada", display: "swap" });
const body = Mulish({ subsets: ["latin"], variable: "--font-mulish", display: "swap" });

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#F4F4F5" },
    { media: "(prefers-color-scheme: dark)", color: "#121214" },
  ],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning className={`${display.variable} ${body.variable}`}>
      <head>
        {/* Applies the stored theme before first paint. Without this the page
            renders light and then flips, which is worse than no dark mode. */}
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
      </head>
      <body className="antialiased">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
