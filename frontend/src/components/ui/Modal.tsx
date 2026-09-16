"use client";

import { X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/cn";
import { useKeyboardInset } from "@/lib/useKeyboardInset";

const SIZES = {
  sm: "max-w-sm",
  md: "max-w-md",
  lg: "max-w-lg",
  xl: "max-w-2xl",
} as const;

/** Matches --animate-scale-out. The panel stays mounted this long after
 *  `open` goes false so the exit has something to play on. */
const EXIT_MS = 140;

export interface ModalProps {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: string;
  size?: keyof typeof SIZES;
  footer?: React.ReactNode;
  children: React.ReactNode;
}

export function Modal({
  open,
  onClose,
  title,
  description,
  size = "md",
  footer,
  children,
}: ModalProps) {
  const panelRef = useRef<HTMLDivElement>(null);

  // A bottom sheet on a phone: a form field near its foot would sit under the
  // keyboard without this. Zero on desktop, where the dialog is centred.
  const keyboardInset = useKeyboardInset(open);

  // The panel outlives `open` by one exit animation, so closing is a movement
  // rather than a disappearance. `closing` is derived as the prop flips —
  // React's "adjust state when a prop changes" pattern — rather than from an
  // effect, which would be a second render for nothing.
  const [previousOpen, setPreviousOpen] = useState(open);
  const [closing, setClosing] = useState(false);

  if (previousOpen !== open) {
    setPreviousOpen(open);
    setClosing(!open);
  }

  useEffect(() => {
    if (!closing) return;
    const timer = window.setTimeout(() => setClosing(false), EXIT_MS);
    return () => window.clearTimeout(timer);
  }, [closing]);

  const mounted = open || closing;

  // Escape to close, and the page behind must not scroll while it is open.
  useEffect(() => {
    if (!open) return;

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKeyDown);

    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    // Focus the panel so screen readers announce it and Escape reaches us
    // even when the click that opened it left focus behind.
    panelRef.current?.focus();

    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = previousOverflow;
    };
  }, [open, onClose]);

  const onBackdropClick = useCallback(
    (event: React.MouseEvent<HTMLDivElement>) => {
      if (event.target === event.currentTarget) onClose();
    },
    [onClose],
  );

  if (!mounted || typeof document === "undefined") return null;

  return createPortal(
    <div
      className={cn(
        "fixed inset-0 z-50 flex items-end justify-center overflow-y-auto bg-black/45 p-0",
        "backdrop-blur-[2px] sm:items-center sm:p-4",
        closing ? "animate-fade-out" : "animate-fade-in",
      )}
      onMouseDown={onBackdropClick}
    >
      <div
        ref={panelRef}
        style={
          keyboardInset > 0
            ? ({ marginBottom: `${keyboardInset}px` } as React.CSSProperties)
            : undefined
        }
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        className={cn(
          "w-full rounded-t-2xl border border-line bg-surface card-shadow-lg",
          "focus:outline-none sm:rounded-2xl",
          closing ? "animate-scale-out" : "animate-scale-in",
          SIZES[size],
        )}
      >
        <div className="flex items-start justify-between gap-4 border-b border-line px-5 py-4">
          <div className="min-w-0">
            <h2 className="text-[15px] font-semibold text-content">{title}</h2>
            {description ? (
              <p className="mt-0.5 text-[13px] leading-snug text-muted">{description}</p>
            ) : null}
          </div>
          <Button variant="ghost" size="icon" onClick={onClose} aria-label="Close">
            <X className="size-4" aria-hidden />
          </Button>
        </div>

        {/* pb-safe clears the home indicator on a phone, where this panel
            is a bottom sheet and its last control would otherwise sit under
            the gesture bar. */}
        <div className="max-h-[70dvh] overflow-y-auto px-5 py-4 max-sm:pb-safe sm:max-h-[65dvh]">
          {children}
        </div>

        {footer ? (
          <div
            className={cn(
              "flex items-center gap-2 border-t border-line bg-surface-2 px-5 py-3.5",
              // Stacked and full-width on a phone: two 90px buttons in the
              // corner of a 390px screen are a thumb-stretch away from each
              // other, and the primary action should be the easy one.
              "max-sm:flex-col-reverse max-sm:[&>*]:w-full",
              "justify-end max-sm:pb-safe",
            )}
          >
            {footer}
          </div>
        ) : null}
      </div>
    </div>,
    document.body,
  );
}

export function ConfirmDialog({
  open,
  onClose,
  onConfirm,
  title,
  description,
  confirmLabel = "Confirm",
  destructive = false,
  loading = false,
  children,
}: {
  open: boolean;
  onClose: () => void;
  onConfirm: () => void;
  title: string;
  description?: string;
  confirmLabel?: string;
  destructive?: boolean;
  loading?: boolean;
  children?: React.ReactNode;
}) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      title={title}
      description={description}
      size="sm"
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={loading}>
            Cancel
          </Button>
          <Button
            variant={destructive ? "danger" : "primary"}
            onClick={onConfirm}
            loading={loading}
          >
            {confirmLabel}
          </Button>
        </>
      }
    >
      {children ?? <p className="text-sm text-muted">This cannot be undone from the portal.</p>}
    </Modal>
  );
}
