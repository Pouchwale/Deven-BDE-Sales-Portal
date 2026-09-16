"use client";

import { ChevronDown } from "lucide-react";
import { forwardRef, useId } from "react";

import { cn } from "@/lib/cn";

const CONTROL =
  "w-full rounded-lg border border-line bg-surface px-3 text-sm text-content " +
  "placeholder:text-subtle transition-[border-color,box-shadow] duration-150 " +
  "hover:border-line-strong focus:border-brand-500 focus:outline-none " +
  "focus:ring-4 focus:ring-brand-500/12 disabled:cursor-not-allowed disabled:opacity-60 " +
  "disabled:hover:border-line";

const INVALID = "border-danger hover:border-danger focus:border-danger focus:ring-danger/12";

/* --------------------------------------------------------------- field */
export interface FieldProps {
  label: string;
  htmlFor?: string;
  hint?: string;
  error?: string | null;
  required?: boolean;
  className?: string;
  children: React.ReactNode;
}

export function Field({
  label,
  htmlFor,
  hint,
  error,
  required,
  className,
  children,
}: FieldProps) {
  return (
    <div className={cn("space-y-1.5", className)}>
      <label
        htmlFor={htmlFor}
        className="flex items-center gap-1 text-[13px] font-medium text-content"
      >
        {label}
        {required ? (
          <span className="text-danger" aria-hidden>
            *
          </span>
        ) : null}
      </label>
      {children}
      {error ? (
        <p className="text-[12.5px] font-medium text-danger" role="alert">
          {error}
        </p>
      ) : hint ? (
        <p className="text-[12.5px] text-subtle">{hint}</p>
      ) : null}
    </div>
  );
}

/* --------------------------------------------------------------- input */
export interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  invalid?: boolean;
  icon?: React.ReactNode;
}

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { className, invalid, icon, ...props },
  ref,
) {
  const control = (
    <input
      ref={ref}
      aria-invalid={invalid || undefined}
      className={cn(CONTROL, "h-9.5", icon && "pl-9", invalid && INVALID, className)}
      {...props}
    />
  );

  if (!icon) return control;
  return (
    <div className="relative">
      <span
        className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-subtle"
        aria-hidden
      >
        {icon}
      </span>
      {control}
    </div>
  );
});

/* -------------------------------------------------------------- select */
export interface SelectProps extends React.SelectHTMLAttributes<HTMLSelectElement> {
  invalid?: boolean;
}

export const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select(
  { className, invalid, children, ...props },
  ref,
) {
  return (
    <div className="relative">
      <select
        ref={ref}
        aria-invalid={invalid || undefined}
        className={cn(
          CONTROL,
          "h-9.5 cursor-pointer appearance-none pr-9",
          invalid && INVALID,
          className,
        )}
        {...props}
      >
        {children}
      </select>
      <ChevronDown
        className="pointer-events-none absolute right-3 top-1/2 size-4 -translate-y-1/2 text-subtle"
        aria-hidden
      />
    </div>
  );
});

/* ------------------------------------------------------------ textarea */
export const Textarea = forwardRef<
  HTMLTextAreaElement,
  React.TextareaHTMLAttributes<HTMLTextAreaElement> & { invalid?: boolean }
>(function Textarea({ className, invalid, ...props }, ref) {
  return (
    <textarea
      ref={ref}
      aria-invalid={invalid || undefined}
      className={cn(CONTROL, "min-h-20 py-2 leading-relaxed", invalid && INVALID, className)}
      {...props}
    />
  );
});

/* ------------------------------------------------------------- toggle */
export function Switch({
  checked,
  onChange,
  label,
  hint,
  disabled,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label: string;
  /** A second line under the label — a count, usually. Without it a filter
   *  that reveals one row in twenty looks like it did nothing. */
  hint?: string;
  disabled?: boolean;
}) {
  const id = useId();
  return (
    <div className="flex items-center gap-2.5">
      <button
        id={id}
        type="button"
        role="switch"
        aria-checked={checked}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={cn(
          "relative inline-flex h-5.5 w-10 shrink-0 items-center rounded-full",
          // The knob is a flex child rather than absolutely positioned, and
          // the track clips: it cannot escape its own pill however the
          // surrounding layout is squeezed.
          "overflow-hidden p-0.5 transition-colors duration-200",
          "disabled:cursor-not-allowed disabled:opacity-60",
          checked ? "bg-brand-600" : "bg-line-strong",
        )}
      >
        <span
          className={cn(
            "size-4.5 rounded-full bg-white shadow-sm transition-transform duration-200",
            checked ? "translate-x-4.5" : "translate-x-0",
          )}
        />
      </button>
      <label htmlFor={id} className="cursor-pointer select-none text-sm text-content">
        {label}
        {hint ? (
          <span className="ml-1.5 text-[12px] text-subtle">{hint}</span>
        ) : null}
      </label>
    </div>
  );
}
