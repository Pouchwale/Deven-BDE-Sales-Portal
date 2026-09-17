"use client";

import { useEffect } from "react";

import { THEME_INIT_SCRIPT } from "@/lib/theme";

import { StatusScreen, linkButtonClass } from "./_status/StatusScreen";
import "./globals.css";

/**
 * Last resort: the root layout itself failed. This replaces the whole
 * document, so it brings its own <html>/<body>, stylesheet and theme. It uses
 * no app components beyond the static status card - nothing that depends on
 * the providers that may be what broke.
 */
export default function GlobalError({
  error,
  retry,
}: {
  error: Error & { digest?: string };
  retry: () => void;
}) {
  useEffect(() => {
    if (process.env.NODE_ENV !== "production") console.error(error);
  }, [error]);

  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <title>Something went wrong · BDE &amp; Sales Portal</title>
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
      </head>
      <body className="antialiased">
        <StatusScreen
          kind="error"
          title="The portal couldn't load"
          description="Something unexpected stopped the portal from starting. Please try again in a moment - if it keeps happening, let your administrator know."
          reference={error.digest}
          actions={
            <>
              <button type="button" onClick={() => retry()} className={linkButtonClass.primary}>
                Try again
              </button>
              <a href="/dashboard" className={linkButtonClass.secondary}>
                Go to dashboard
              </a>
            </>
          }
        />
      </body>
    </html>
  );
}
