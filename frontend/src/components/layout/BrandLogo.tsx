import Image from "next/image";

import { cn } from "@/lib/cn";

/** The company mark, as a circle. `size` is the rendered width in pixels. */
export function BrandLogo({ size = 40, className }: { size?: number; className?: string }) {
  return (
    <Image
      src={size > 96 ? "/brand/logo.png" : "/brand/logo-96.png"}
      alt="Pouchwale"
      width={size}
      height={size}
      // Already sized for the screen; nothing for the optimiser to do.
      unoptimized
      priority
      className={cn("shrink-0 rounded-full object-cover", className)}
    />
  );
}
