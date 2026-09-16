"use client";

import { useEffect, useRef } from "react";

import { MessageBubble } from "@/components/chat/MessageBubble";
import type { Turn } from "@/lib/chat";
import type { Role } from "@/types/api";

export function MessageList({
  turns,
  role,
  onNavigate,
}: {
  turns: Turn[];
  role: Role;
  onNavigate?: () => void;
}) {
  const endRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  // Follow the stream, but only while the reader is already at the bottom —
  // yanking the view back while somebody is reading earlier text is worse
  // than letting new text arrive off-screen.
  const lastTurn = turns[turns.length - 1];
  const tick = `${turns.length}:${lastTurn?.content.length ?? 0}:${lastTurn?.activity ?? ""}`;

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const distance =
      container.scrollHeight - container.scrollTop - container.clientHeight;
    if (distance < 120) {
      endRef.current?.scrollIntoView({ block: "end" });
    }
  }, [tick]);

  return (
    <div ref={containerRef} className="flex-1 space-y-3.5 overflow-y-auto px-4 py-4">
      {turns.map((turn) => (
        <MessageBubble key={turn.id} turn={turn} role={role} onNavigate={onNavigate} />
      ))}
      <div ref={endRef} />
    </div>
  );
}
