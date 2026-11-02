"use client";

import { Loader2 } from "lucide-react";
import { forwardRef } from "react";

import { cn } from "@/lib/cn";

type Variant = "primary" | "secondary" | "ghost" | "danger" | "subtle";
type Size = "sm" | "md" | "lg" | "icon";

// PouchWale "stamp" buttons: pill, 2px ink border, hard offset shadow that
// squashes on press. Hover swaps to the colour's shade - never an opacity fade.
const STAMP =
  "border-2 border-ink shadow-[3px_3px_0_0_var(--stamp)] " +
  "active:translate-x-[2px] active:translate-y-[2px] active:shadow-none";

const VARIANTS: Record<Variant, string> = {
  primary:
    `bg-pw-pink text-pw-ink ${STAMP} hover:bg-pw-pink-shade hover:text-white ` +
    "disabled:hover:bg-pw-pink disabled:hover:text-pw-ink",
  secondary: `bg-surface text-content ${STAMP} hover:bg-pw-yellow hover:text-pw-ink`,
  ghost: "border-2 border-transparent text-muted hover:bg-surface-hover hover:text-content",
  danger: `bg-danger text-white ${STAMP} hover:bg-pw-pink-shade`,
  subtle:
    "bg-surface-2 text-content border-2 border-transparent hover:border-ink hover:bg-surface",
};

const SIZES: Record<Size, string> = {
  sm: "h-8 px-3.5 text-[13px] gap-1.5 rounded-full",
  md: "h-9.5 px-5 text-sm gap-2 rounded-full",
  lg: "h-11 px-6 text-[15px] gap-2 rounded-full",
  icon: "size-9 rounded-full",
};

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  loading?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { className, variant = "primary", size = "md", loading = false, disabled, children, ...props },
  ref,
) {
  return (
    <button
      ref={ref}
      // A loading button stays disabled: a second submit is never what the
      // user meant, and double-posting a create is worse than a slow click.
      disabled={disabled || loading}
      className={cn(
        "relative inline-flex select-none items-center justify-center font-bold",
        "transition-[background-color,border-color,color,box-shadow,transform] duration-[140ms]",
        "disabled:pointer-events-none disabled:opacity-55",
        VARIANTS[variant],
        SIZES[size],
        className,
      )}
      {...props}
    >
      {loading ? <Loader2 className="size-4 shrink-0 animate-spin" aria-hidden /> : null}
      {children}
    </button>
  );
});
