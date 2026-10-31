"use client";

import { Check, Copy } from "lucide-react";
import { useState } from "react";

import { cn } from "@/lib/cn";

/**
 * A phone number someone has to act on: tap it to call (a phone opens the
 * dialler), or press the button to copy it. Sales work from their phones and
 * cannot look the number up in the sheet the way a desk can.
 */
export function PhoneNumber({ value, className }: { value: string | null; className?: string }) {
  const [copied, setCopied] = useState(false);
  if (!value) return <span className="text-subtle">—</span>;

  async function copy() {
    try {
      await navigator.clipboard.writeText(value!);
    } catch {
      // Older browsers or a non-secure page: the hidden-textarea route still works.
      const area = document.createElement("textarea");
      area.value = value!;
      area.setAttribute("readonly", "");
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.appendChild(area);
      area.select();
      document.execCommand("copy");
      area.remove();
    }
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  }

  return (
    <span className={cn("inline-flex items-center gap-1", className)}>
      <a
        href={`tel:${value.replace(/[^\d+]/g, "")}`}
        className="whitespace-nowrap font-medium tabular-nums text-content hover:underline"
        title="Call"
      >
        {value}
      </a>
      <button
        type="button"
        onClick={() => void copy()}
        aria-label={copied ? "Number copied" : `Copy ${value}`}
        title={copied ? "Copied" : "Copy number"}
        className={cn(
          // 36px to tap on a phone, 24px with a mouse.
          "-m-1.5 grid size-9 shrink-0 place-items-center rounded-md transition-colors sm:m-0 sm:size-6",
          copied ? "text-success" : "text-subtle hover:bg-surface-hover hover:text-content",
        )}
      >
        {copied ? <Check className="size-3.5" aria-hidden /> : <Copy className="size-3.5" aria-hidden />}
      </button>
    </span>
  );
}
