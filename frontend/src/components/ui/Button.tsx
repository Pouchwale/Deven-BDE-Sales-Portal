"use client";

import { Loader2 } from "lucide-react";
import { forwardRef } from "react";

import { cn } from "@/lib/cn";

type Variant = "primary" | "secondary" | "ghost" | "danger" | "subtle";
type Size = "sm" | "md" | "lg" | "icon";

const VARIANTS: Record<Variant, string> = {
  primary:
    "bg-brand-600 text-white shadow-sm hover:bg-brand-700 active:bg-brand-800 " +
    "disabled:hover:bg-brand-600",
  secondary:
    "bg-surface text-content border border-line hover:bg-surface-hover " +
    "hover:border-line-strong active:bg-surface-2",
  ghost: "text-muted hover:bg-surface-hover hover:text-content",
  danger:
    "bg-danger text-white shadow-sm hover:brightness-110 active:brightness-95 " +
    "disabled:hover:brightness-100",
  subtle:
    "bg-surface-2 text-content border border-transparent hover:bg-surface-hover " +
    "hover:border-line",
};

const SIZES: Record<Size, string> = {
  sm: "h-8 px-3 text-[13px] gap-1.5 rounded-lg",
  md: "h-9.5 px-4 text-sm gap-2 rounded-lg",
  lg: "h-11 px-5 text-[15px] gap-2 rounded-xl",
  icon: "size-9 rounded-lg",
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
        "relative inline-flex select-none items-center justify-center font-medium",
        "transition-[background-color,border-color,color,box-shadow,transform] duration-150",
        "active:scale-[0.985] disabled:pointer-events-none disabled:opacity-55",
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
