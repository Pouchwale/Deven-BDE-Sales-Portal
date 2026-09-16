"use client";

import { ArrowUp, Square } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { cn } from "@/lib/cn";

const MAX_LENGTH = 2000; // Matches ChatSendRequest on the server.

export function ChatComposer({
  busy,
  onSend,
  onStop,
  placeholder = "Ask about your work…",
}: {
  busy: boolean;
  onSend: (message: string) => void;
  onStop: () => void;
  /** Nudged by the page you are on, so the field suggests what is answerable
   *  here rather than repeating the same sentence everywhere. */
  placeholder?: string;
}) {
  const [value, setValue] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Grow with the text, up to a point. Reset first so it can also shrink.
  useEffect(() => {
    const element = textareaRef.current;
    if (!element) return;
    element.style.height = "auto";
    element.style.height = `${Math.min(element.scrollHeight, 132)}px`;
  }, [value]);

  function submit() {
    const text = value.trim();
    if (!text || busy) return;
    onSend(text);
    setValue("");
  }

  return (
    <div className="border-t border-line bg-surface px-3 py-2.5 max-sm:pb-safe">
      <div
        className={cn(
          "flex items-end gap-2 rounded-2xl border border-line bg-surface-2 px-3 py-2",
          "transition-colors focus-within:border-brand-400 focus-within:bg-surface",
        )}
      >
        <textarea
          ref={textareaRef}
          rows={1}
          value={value}
          maxLength={MAX_LENGTH}
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={(event) => {
            // Enter sends, Shift+Enter breaks the line — the convention
            // everybody already has in their fingers.
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              submit();
            }
          }}
          placeholder={placeholder}
          aria-label="Message the assistant"
          className={cn(
            "max-h-33 flex-1 resize-none bg-transparent text-[13.5px] leading-relaxed",
            "text-content outline-none placeholder:text-subtle",
          )}
        />

        {busy ? (
          <button
            type="button"
            onClick={onStop}
            aria-label="Stop"
            className={cn(
              "grid size-7 shrink-0 place-items-center rounded-full bg-surface-hover",
              "text-muted transition-colors hover:text-content active:scale-95",
            )}
          >
            <Square className="size-3 fill-current" aria-hidden />
          </button>
        ) : (
          <button
            type="button"
            onClick={submit}
            disabled={!value.trim()}
            aria-label="Send"
            className={cn(
              "grid size-7 shrink-0 place-items-center rounded-full bg-brand-600 text-white",
              "transition-[background-color,transform,opacity] hover:bg-brand-700",
              "active:scale-95 disabled:pointer-events-none disabled:opacity-35",
            )}
          >
            <ArrowUp className="size-3.5" aria-hidden />
          </button>
        )}
      </div>

      <p className="mt-1.5 px-1 text-[11px] text-subtle">
        Answers come from your own data and can be wrong — check anything that matters.
      </p>
    </div>
  );
}
