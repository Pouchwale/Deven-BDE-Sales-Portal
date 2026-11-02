import Image from "next/image";

import { cn } from "@/lib/cn";

/** Width over height of the trimmed PouchWale wordmark files. */
const RATIO = 2.3625;

type Tone = "auto" | "onDark";

/**
 * The PouchWale wordmark. `height` is the rendered height in pixels.
 * "auto" is pink on the light theme and yellow on the dark one; "onDark" is
 * for a panel that is dark in both themes. The files are the brand's own
 * artwork - never re-typeset the name (the macron over the A is custom).
 */
export function BrandLogo({
  height = 28,
  tone = "auto",
  className,
}: {
  height?: number;
  tone?: Tone;
  className?: string;
}) {
  const width = Math.round(height * RATIO);
  const common = {
    width,
    height,
    // Already sized for the screen; nothing for the optimiser to do.
    unoptimized: true,
    priority: true,
  } as const;

  if (tone === "onDark") {
    return <Image src="/brand/pouchwale-yellow.png" alt="PouchWale" {...common} className={cn("shrink-0", className)} />;
  }
  return (
    <>
      <Image src="/brand/pouchwale-pink.png" alt="PouchWale" {...common} className={cn("shrink-0 dark:hidden", className)} />
      <Image
        src="/brand/pouchwale-yellow.png"
        alt=""
        {...common}
        aria-hidden
        className={cn("hidden shrink-0 dark:block", className)}
      />
    </>
  );
}

/** The square mark (white wordmark on a pink field), for tight spots. */
export function BrandMark({ size = 34, className }: { size?: number; className?: string }) {
  return (
    <Image
      src="/brand/pouchwale-mark-96.png"
      alt="PouchWale"
      width={size}
      height={size}
      unoptimized
      priority
      className={cn("shrink-0 border-2 border-ink", className)}
    />
  );
}
