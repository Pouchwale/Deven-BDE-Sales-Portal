"use client";

import dynamic from "next/dynamic";
import { useEffect, useState } from "react";

import { AssistantMark } from "@/components/chat/AssistantMark";
import { useChat } from "@/lib/chat";
import { cn } from "@/lib/cn";
import type { User } from "@/types/api";

/**
 * The drawer is the whole assistant UI - transcript, composer, tool trail,
 * history. It used to be imported here, which put it in the shared layout
 * chunk of every page in the portal for a panel most visits never open. It is
 * fetched on first open instead, and prefetched on hover so opening still
 * feels immediate.
 */
const loadDrawer = () => import("@/components/chat/ChatDrawer");
const ChatDrawer = dynamic(() => loadDrawer().then((module) => module.ChatDrawer), {
  ssr: false,
});

/**
 * The floating entry point, plus the drawer it opens.
 *
 * Renders nothing when the deployment has switched the assistant off. Missing
 * a key is a different thing: the launcher appears and the drawer explains
 * what is missing, because a feature that hides itself is indistinguishable
 * from a broken one.
 */
export function ChatLauncher({ user }: { user: User }) {
  const { available, open, setOpen } = useChat();
  // Once opened it stays mounted: the drawer plays its own exit animation, and
  // remounting it on every close would flicker. Adjusted during render, the
  // same way ChatDrawer derives its own `closing` flag from `open`.
  const [everOpened, setEverOpened] = useState(false);
  if (open && !everOpened) setEverOpened(true);

  // Ctrl/Cmd+K toggles it. Skipped while typing, so it never steals a
  // keystroke from a search box or a note field.
  useEffect(() => {
    if (!available) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key.toLowerCase() !== "k" || !(event.metaKey || event.ctrlKey)) return;
      const target = event.target as HTMLElement | null;
      if (target?.isContentEditable) return;
      if (target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return;
      event.preventDefault();
      setOpen(!open);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [available, open, setOpen]);

  // `available`, not `enabled`: a deployment that wants the assistant but has
  // no key yet should still show it, and say so inside. Only CHAT_ENABLED=false
  // removes the feature entirely.
  if (!available) return null;

  return (
    <>
      {/* Gone while the drawer is up: it would float over the page next to a
          panel that already has a close button and answers to Escape. */}
      {open ? null : (
        <div
          className={cn(
            "group fixed right-5 z-30",
            // Above the home indicator on a phone, not under it.
            "bottom-[calc(1.25rem+env(safe-area-inset-bottom))]",
          )}
        >
          {/* A real tooltip rather than the native `title`, which takes a
              second to appear and cannot be styled or read consistently.
              aria-hidden because the button already carries the same words
              as its accessible name. */}
          <span
            aria-hidden
            className={cn(
              "pointer-events-none absolute right-full top-1/2 mr-3 -translate-y-1/2",
              "whitespace-nowrap rounded-lg border border-line bg-surface px-2.5 py-1.5",
              "text-[12.5px] text-content card-shadow",
              "translate-x-1 opacity-0 transition-[opacity,transform] duration-150",
              "group-hover:translate-x-0 group-hover:opacity-100",
              "group-focus-within:translate-x-0 group-focus-within:opacity-100",
              "max-sm:hidden",
            )}
          >
            Ask the assistant
            <kbd className="ml-2 rounded border border-line bg-surface-2 px-1 py-0.5 font-mono text-[10.5px] text-subtle">
              Ctrl K
            </kbd>
          </span>

          <button
            type="button"
            onClick={() => setOpen(true)}
            // Warm the chunk before the click lands.
            onMouseEnter={() => void loadDrawer()}
            onFocus={() => void loadDrawer()}
            aria-label="Ask the assistant"
            aria-keyshortcuts="Control+K"
            className={cn(
              "animate-pop relative grid size-12 place-items-center rounded-full",
              "bg-brand-600 text-white card-shadow-lg ring-1 ring-brand-700/20",
              "transition-[transform,background-color,box-shadow] duration-200",
              "hover:-translate-y-0.5 hover:bg-brand-700",
              // A real pressed state: it goes down, not just smaller.
              "active:translate-y-0 active:scale-95 active:bg-brand-800",
            )}
          >
            {/* A single halo that settles once, on mount, and then stops.
                Deliberately not a loop: a button that pulses forever is a
                button people learn to ignore, and it keeps a compositor
                layer awake for no reason. `motion-safe` drops it entirely
                for anybody who has asked for less movement. */}
            <span
              aria-hidden
              className="motion-safe:animate-halo absolute inset-0 rounded-full ring-2 ring-brand-500/40"
            />
            <AssistantMark className="relative size-6" />
          </button>
        </div>
      )}

      {everOpened ? <ChatDrawer user={user} /> : null}
    </>
  );
}
