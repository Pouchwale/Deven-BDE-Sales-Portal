import { cn } from "@/lib/cn";
import { hueFor, initials } from "@/lib/format";

const SIZES = {
  xs: "size-6 text-[10px]",
  sm: "size-8 text-[11px]",
  md: "size-9.5 text-xs",
  lg: "size-12 text-sm",
  xl: "size-16 text-lg",
} as const;

/**
 * Initials on a hue derived from the name, so the same person is the same
 * colour everywhere without storing anything.
 */
export function Avatar({
  name,
  size = "md",
  className,
}: {
  name: string;
  size?: keyof typeof SIZES;
  className?: string;
}) {
  const hue = hueFor(name);

  return (
    <span
      aria-hidden
      title={name}
      style={{
        backgroundColor: `oklch(0.92 0.055 ${hue})`,
        color: `oklch(0.38 0.115 ${hue})`,
        borderColor: `oklch(0.86 0.07 ${hue})`,
      }}
      className={cn(
        "inline-flex shrink-0 select-none items-center justify-center rounded-full",
        "border font-semibold tracking-wide",
        // Dark mode needs the inverse relationship, which inline styles
        // cannot express — so the palette is overridden wholesale.
        "dark:border-transparent dark:bg-[color-mix(in_oklch,var(--brand-400)_22%,var(--surface-2))] dark:text-brand-200",
        SIZES[size],
        className,
      )}
    >
      {initials(name)}
    </span>
  );
}
