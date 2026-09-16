"use client";

import { ArrowUpRight } from "lucide-react";
import Link from "next/link";

import { cn } from "@/lib/cn";
import { isLeadership } from "@/lib/roles";
import type { ResultCard, Role } from "@/types/api";

/**
 * The four shapes from section 7.5 of the chatbot plan.
 *
 * Cards are for things you would want to *act* on — a lead you should call, an
 * account nobody has asked, a department that needs somebody. Everything else
 * stays prose, because a wall of cards is worse than a sentence.
 *
 * What a card is NOT is a copy of the record. It carries an id, a name and the
 * two facts you need to decide whether to click; the phone number lives on the
 * page it links to, where reading it leaves a trail. The server enforces that
 * (`services/chat/cards.py`) — this component could not render a mobile number
 * even if somebody asked it to, because none arrives.
 */
const TONES: Record<string, string> = {
  success: "border-transparent bg-success-soft text-success",
  warning: "border-transparent bg-warning-soft text-warning",
  danger: "border-transparent bg-danger-soft text-danger",
  info: "border-transparent bg-info-soft text-info",
  neutral: "border-line bg-surface-2 text-muted",
};

/** Destinations a role may not open. Mirrors `Sidebar.tsx`, as the plan
 *  requires: the assistant must never link a BDE somewhere that answers 403. */
const GATED: [prefix: string, allowed: (role: Role) => boolean][] = [
  ["/team", isLeadership],
  ["/admin", isLeadership],
];

function reachable(href: string, role: Role): boolean {
  const gate = GATED.find(([prefix]) => href.startsWith(prefix));
  return gate ? gate[1](role) : true;
}

export function ResultCards({
  cards,
  role,
  onNavigate,
}: {
  cards: ResultCard[];
  role: Role;
  onNavigate?: () => void;
}) {
  const visible = cards.filter((card) => reachable(card.href, role));
  if (visible.length === 0) return null;

  return (
    <ul className="mt-2.5 space-y-1.5">
      {visible.map((card, index) => (
        <li
          key={`${card.kind}-${card.id}-${index}`}
          className="stagger"
          style={{ "--index": index } as React.CSSProperties}
        >
          <Link
            href={card.href}
            onClick={onNavigate}
            className={cn(
              "group block rounded-xl border border-line bg-surface px-3 py-2.5",
              "transition-[border-color,background-color,transform] duration-150",
              "hover:-translate-y-px hover:border-line-strong hover:bg-surface-hover",
            )}
          >
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <p className="truncate text-[13px] font-medium text-content">
                  {card.title}
                </p>
                {card.subtitle ? (
                  <p className="truncate text-[11.5px] text-subtle">{card.subtitle}</p>
                ) : null}
              </div>
              <ArrowUpRight
                className={cn(
                  "mt-0.5 size-3.5 shrink-0 text-subtle opacity-0",
                  "transition-opacity duration-150 group-hover:opacity-100",
                )}
                aria-hidden
              />
            </div>

            {card.badges.length ? (
              <div className="mt-1.5 flex flex-wrap items-center gap-1">
                {card.badges.map((badge) => (
                  <span
                    key={badge.label}
                    className={cn(
                      "rounded-full border px-1.5 py-0.5 text-[10.5px] font-medium",
                      TONES[badge.tone] ?? TONES.neutral,
                    )}
                  >
                    {badge.label}
                  </span>
                ))}
              </div>
            ) : null}

            {card.meta.length ? (
              <p className="mt-1 truncate text-[11px] text-subtle">
                {card.meta.join(" · ")}
              </p>
            ) : null}
          </Link>
        </li>
      ))}
    </ul>
  );
}
