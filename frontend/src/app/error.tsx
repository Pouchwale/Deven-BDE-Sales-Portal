"use client";

import { RefreshCw } from "lucide-react";
import { useEffect } from "react";

import { Button } from "@/components/ui/Button";

import { StatusScreen, linkButtonClass } from "./_status/StatusScreen";

/**
 * Catches a rendering error anywhere outside the signed-in shell (sign-in,
 * password change, the index redirect). The error's own text never reaches
 * the screen in a production build - only the digest, which matches a server
 * log line and says nothing about the cause.
 */
export default function RootError({
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
    <StatusScreen
      kind="error"
      title="Something went wrong"
      description={
        process.env.NODE_ENV !== "production" && error.message
          ? error.message
          : "This page hit an unexpected problem. Please try again - if it keeps happening, let your administrator know."
      }
      reference={error.digest}
      actions={
        <>
          <Button onClick={() => retry()}>
            <RefreshCw className="size-3.5" aria-hidden />
            Try again
          </Button>
          {/* A plain <a>: a full navigation clears whatever state broke. */}
          <a href="/dashboard" className={linkButtonClass.secondary}>
            Go to dashboard
          </a>
        </>
      }
    />
  );
}
