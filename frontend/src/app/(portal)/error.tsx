"use client";

import { RefreshCw } from "lucide-react";
import { useEffect } from "react";

import { Button } from "@/components/ui/Button";

import { StatusScreen, linkButtonClass } from "../_status/StatusScreen";

/**
 * A page inside the signed-in shell failed to render. The sidebar and top bar
 * belong to the layout above this boundary, so they stay usable and the user
 * can simply navigate elsewhere.
 */
export default function PortalError({
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
      fullPage={false}
      title="This page couldn't be displayed"
      description={
        process.env.NODE_ENV !== "production" && error.message
          ? error.message
          : "Something unexpected happened while showing this page. Try again, or pick another page from the menu."
      }
      reference={error.digest}
      actions={
        <>
          <Button onClick={() => retry()}>
            <RefreshCw className="size-3.5" aria-hidden />
            Try again
          </Button>
          <a href="/dashboard" className={linkButtonClass.secondary}>
            Go to dashboard
          </a>
        </>
      }
    />
  );
}
