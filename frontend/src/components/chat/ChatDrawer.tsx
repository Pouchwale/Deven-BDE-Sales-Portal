"use client";

import { History, Minus, SquarePen, Trash2 } from "lucide-react";
import { usePathname } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { AssistantMark } from "@/components/chat/AssistantMark";
import { ChatComposer } from "@/components/chat/ChatComposer";
import { ChatEmptyState } from "@/components/chat/ChatEmptyState";
import { ChatUnconfigured } from "@/components/chat/ChatUnconfigured";
import { MessageList } from "@/components/chat/MessageList";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/primitives/tooltip";
import {
  composerPlaceholder,
  orderSuggestions,
  workloadLine,
} from "@/lib/assistantContext";
import { useChat } from "@/lib/chat";
import { cn } from "@/lib/cn";
import { formatRelative } from "@/lib/format";
import { gsap, prefersReducedMotion, useGSAP } from "@/lib/motion";
import { useKeyboardInset } from "@/lib/useKeyboardInset";
import type { User } from "@/types/api";

const PHONE = "(max-width: 639px)";

/**
 * The assistant panel.
 *
 * Desktop: a floating panel anchored bottom-right, where the launcher was -
 * the page behind stays live, because reading an answer against the numbers
 * it came from is the point. Phone: a near-full-screen sheet over a dimmed
 * page, because there is no room to sit beside anything.
 *
 * Loaded lazily by ChatLauncher on first open, so GSAP and everything here
 * stays out of the bundle of a visit that never opens it.
 */
export function ChatDrawer({ user }: { user: User }) {
  const {
    open,
    setOpen,
    enabled,
    setup,
    turns,
    suggestions,
    busy,
    send,
    retry,
    stop,
    reset,
    history,
    load,
    remove,
    dashboard,
  } = useChat();
  const [showHistory, setShowHistory] = useState(false);
  const panelRef = useRef<HTMLElement>(null);
  const backdropRef = useRef<HTMLDivElement>(null);
  const pathname = usePathname();

  // On a phone the sheet is pinned to the bottom edge, and the keyboard would
  // otherwise cover the very input it just opened for. Zero on desktop.
  const keyboardInset = useKeyboardInset(open);

  // Reordering only - the list arrives already filtered to this role, and
  // the tool dispatcher re-checks every call regardless.
  const ordered = useMemo(
    () => orderSuggestions(suggestions, pathname),
    [suggestions, pathname],
  );
  const workload = useMemo(() => workloadLine(dashboard), [dashboard]);
  const placeholder = useMemo(() => composerPlaceholder(pathname), [pathname]);

  // The panel outlives `open` by one exit animation, so closing is a
  // movement rather than a disappearance. Derived during render, the same
  // way Modal does it.
  const [rendered, setRendered] = useState(open);
  const [previousOpen, setPreviousOpen] = useState(open);
  if (previousOpen !== open) {
    setPreviousOpen(open);
    if (open) setRendered(true);
    else setShowHistory(false);
  }

  // Open and close. Transform and opacity only; the close is the open played
  // quicker in reverse. Reduced motion: no movement, and the panel goes the
  // moment it is closed.
  useGSAP(
    () => {
      const panel = panelRef.current;
      if (!panel) return;
      const reduce = prefersReducedMotion();
      const phone = window.matchMedia(PHONE).matches;
      const hidden = phone ? { opacity: 0, y: 32 } : { opacity: 0, y: 12, scale: 0.97 };

      if (open) {
        if (!reduce) {
          gsap.fromTo(panel, hidden, {
            opacity: 1,
            y: 0,
            scale: 1,
            duration: phone ? 0.34 : 0.28,
            ease: "power3.out",
            clearProps: "opacity,transform",
          });
          if (backdropRef.current) {
            gsap.fromTo(backdropRef.current, { opacity: 0 }, { opacity: 1, duration: 0.24 });
          }
        }
        // Desktop: straight into the composer. Phone: the panel itself, so
        // the keyboard does not cover the suggestions before they are read.
        const field = panel.querySelector<HTMLTextAreaElement>("textarea");
        if (field && !phone) field.focus({ preventScroll: true });
        else panel.focus({ preventScroll: true });
        return;
      }

      const finish = () => {
        setRendered(false);
        // Back to where the person came from, if they were still in here.
        if (!document.activeElement || panel.contains(document.activeElement)) {
          document.querySelector<HTMLElement>("[data-chat-launcher]")?.focus();
        }
      };
      if (reduce) {
        finish();
        return;
      }
      gsap.to(panel, { ...hidden, duration: 0.18, ease: "power2.in", onComplete: finish });
      if (backdropRef.current) gsap.to(backdropRef.current, { opacity: 0, duration: 0.18 });
    },
    { dependencies: [open] },
  );

  useEffect(() => {
    if (!open) return;

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };

    // Clicking outside closes it on desktop, the way every panel of this
    // shape behaves. `mousedown` rather than `click`: a selection that STARTS
    // inside the panel and drifts out must not count as leaving.
    const onPointerDown = (event: MouseEvent) => {
      const target = event.target as Node | null;
      if (!target) return;
      if (panelRef.current?.contains(target)) return;
      // Menus and tooltips render in their own portals.
      if ((target as Element).closest?.("[data-radix-popper-content-wrapper]")) return;
      // The launcher owns its own toggle; letting this fire too would close
      // and immediately reopen.
      if ((target as Element).closest?.("[data-chat-launcher]")) return;
      setOpen(false);
    };

    document.addEventListener("keydown", onKeyDown);
    // Capture phase, so a stopPropagation deeper in the page cannot swallow it.
    document.addEventListener("mousedown", onPointerDown, true);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.removeEventListener("mousedown", onPointerDown, true);
    };
  }, [open, setOpen]);

  if (!rendered || typeof document === "undefined") return null;

  const status = !enabled
    ? { label: "Setup needed", dot: "bg-warning" }
    : busy
      ? { label: "Working on it…", dot: "bg-brand-500 motion-safe:animate-pulse" }
      : { label: "Ready", dot: "bg-success" };

  return createPortal(
    <>
      {/* Phone only. On a wider screen the page behind stays live. */}
      <div
        ref={backdropRef}
        aria-hidden
        className="fixed inset-0 z-40 bg-black/40 sm:hidden"
        onMouseDown={() => setOpen(false)}
      />

      <aside
        ref={panelRef}
        role="dialog"
        aria-label="Portal assistant"
        tabIndex={-1}
        style={
          keyboardInset > 0
            ? ({ "--keyboard-inset": `${keyboardInset}px` } as React.CSSProperties)
            : undefined
        }
        className={cn(
          "fixed z-50 flex flex-col overflow-hidden border border-line bg-surface card-shadow-lg focus:outline-none",
          // Phone: nearly the whole screen, keyboard-aware.
          "inset-x-0 bottom-[var(--keyboard-inset,0px)] top-[max(0.5rem,env(safe-area-inset-top))] rounded-t-2xl border-b-0",
          // Tablet and up: a floating panel where the launcher sits.
          "sm:inset-auto sm:bottom-5 sm:right-5 sm:h-[min(680px,calc(100dvh-2.5rem))] sm:w-[400px]",
          "sm:origin-bottom-right sm:rounded-2xl sm:border-b",
        )}
      >
        {/* Phone only: the affordance that says "this is a sheet". */}
        <div className="flex justify-center pt-2 sm:hidden" aria-hidden>
          <span className="h-1 w-9 rounded-full bg-line-strong" />
        </div>

        {/* ------------------------------------------------------- header */}
        <header className="flex items-center gap-2.5 border-b border-line py-2.5 pl-4 pr-2">
          <div className="relative grid size-8 shrink-0 place-items-center rounded-lg bg-brand-600 text-white">
            <AssistantMark className="size-[18px]" />
            <span
              aria-hidden
              className={cn(
                "absolute -bottom-0.5 -right-0.5 size-2.5 rounded-full ring-2 ring-surface",
                status.dot,
              )}
            />
          </div>
          <div className="min-w-0 flex-1">
            <p className="truncate text-[14px] font-semibold leading-tight text-content">
              Assistant
            </p>
            <p className="truncate text-[11.5px] leading-tight text-subtle" aria-live="polite">
              {status.label}
            </p>
          </div>

          {enabled ? (
            <>
              <HeaderButton
                label={showHistory ? "Back to chat" : "Past conversations"}
                pressed={showHistory}
                onClick={() => setShowHistory((value) => !value)}
              >
                <History className="size-4" aria-hidden />
              </HeaderButton>
              <HeaderButton
                label="New conversation"
                onClick={() => {
                  reset();
                  setShowHistory(false);
                }}
              >
                <SquarePen className="size-4" aria-hidden />
              </HeaderButton>
            </>
          ) : null}
          <HeaderButton label="Close assistant" onClick={() => setOpen(false)}>
            <Minus className="size-4" aria-hidden />
          </HeaderButton>
        </header>

        {/* -------------------------------------------------------- body */}
        {showHistory ? (
          <div className="flex-1 overflow-y-auto overscroll-contain px-3 py-3">
            {history.length === 0 ? (
              <div className="px-4 py-10 text-center">
                <History className="mx-auto size-5 text-subtle" aria-hidden />
                <p className="mt-2 text-[13px] font-medium text-content">No past conversations</p>
                <p className="mt-0.5 text-[12.5px] text-subtle">
                  Conversations you finish appear here.
                </p>
              </div>
            ) : (
              <ul className="space-y-0.5">
                {history.map((conversation) => (
                  <li key={conversation.id} className="group flex items-center gap-1">
                    <button
                      type="button"
                      onClick={() => {
                        void load(conversation.id);
                        setShowHistory(false);
                      }}
                      className="min-w-0 flex-1 rounded-lg px-2.5 py-2 text-left transition-colors hover:bg-surface-hover"
                    >
                      <span className="block truncate text-[13px] text-content">
                        {conversation.title ?? "Untitled"}
                      </span>
                      <span className="block text-[11px] text-subtle">
                        {formatRelative(conversation.updated_at)}
                      </span>
                    </button>
                    <button
                      type="button"
                      aria-label={`Delete conversation: ${conversation.title ?? "Untitled"}`}
                      onClick={() => void remove(conversation.id)}
                      className={cn(
                        "grid size-9 shrink-0 place-items-center rounded-lg text-subtle transition",
                        "hover:bg-danger-soft hover:text-danger",
                        // Always visible on touch screens, where there is no hover.
                        "sm:opacity-0 sm:group-hover:opacity-100 sm:focus:opacity-100",
                      )}
                    >
                      <Trash2 className="size-3.5" aria-hidden />
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        ) : !enabled ? (
          <div className="flex-1 overflow-y-auto">
            <ChatUnconfigured setup={setup} />
          </div>
        ) : turns.length === 0 ? (
          <div className="flex-1 overflow-y-auto overscroll-contain">
            <ChatEmptyState
              name={user.name}
              suggestions={ordered}
              workload={workload}
              onPick={send}
            />
          </div>
        ) : (
          <MessageList
            turns={turns}
            role={user.role}
            onNavigate={() => setOpen(false)}
            onRetry={retry}
          />
        )}

        {/* No composer without a key: every send would be a 503, and a dead
            input is a worse answer than an explanation. */}
        {!showHistory && enabled ? (
          <ChatComposer busy={busy} onSend={send} onStop={stop} placeholder={placeholder} />
        ) : null}
      </aside>
    </>,
    document.body,
  );
}

function HeaderButton({
  label,
  pressed,
  onClick,
  children,
}: {
  label: string;
  pressed?: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          aria-label={label}
          aria-pressed={pressed}
          onClick={onClick}
          className={cn(
            "grid size-10 shrink-0 place-items-center rounded-lg text-muted transition-colors",
            "hover:bg-surface-hover hover:text-content",
            pressed && "bg-surface-hover text-content",
          )}
        >
          {children}
        </button>
      </TooltipTrigger>
      <TooltipContent side="bottom">{label}</TooltipContent>
    </Tooltip>
  );
}
