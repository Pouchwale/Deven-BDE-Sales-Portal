"use client";

/**
 * The assistant's mark.
 *
 * A speech bubble with a signal burst inside it: it answers questions (the
 * bubble) by reading live portal data (the burst). Drawn rather than borrowed
 * from the icon set so the assistant reads as its own thing next to the
 * lucide glyphs the rest of the nav uses, and so it stays legible at 16px.
 *
 * `currentColor` throughout, so it inherits whatever the surface gives it and
 * needs no separate dark-mode handling.
 */
export function AssistantMark({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden
    >
      {/* the bubble, with a tail at the lower left */}
      <path d="M20.5 11.8a8 8 0 0 1-8 8H7.4L4 22.2v-4.1a8 8 0 0 1-.5-6.3 8 8 0 0 1 7.6-5.6h1.4a8 8 0 0 1 8 5.6Z" />
      {/* three bars: the data it reads */}
      <path d="M8.6 14.2v-2.1M12 14.2v-4.3M15.4 14.2v-1.2" />
      {/* the spark: the answer */}
      <path d="M17.6 2.2l.7 1.9 1.9.7-1.9.7-.7 1.9-.7-1.9-1.9-.7 1.9-.7z" />
    </svg>
  );
}
