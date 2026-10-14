import { CheckCircle2, CircleDashed, Clock3, XCircle } from "lucide-react";

import { cn } from "@/lib/cn";
import {
  REFERENCE_STATUS_LABELS,
  REFERENCE_STATUS_TONE,
  ROLE_TONE,
  roleLabel,
} from "@/lib/roles";
import type { ReferenceStatus, Role } from "@/types/api";

export function Badge({
  className,
  ...props
}: React.HTMLAttributes<HTMLSpanElement>) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5",
        "text-[11.5px] font-medium leading-4 whitespace-nowrap",
        // A badge changes when the thing it labels changes — a lead moving
        // stage, an alert being assigned — so the colour should travel rather
        // than cut.
        "transition-colors duration-200",
        className,
      )}
      {...props}
    />
  );
}

export function RoleBadge({ role, className }: { role: Role; className?: string }) {
  return (
    <Badge className={cn(ROLE_TONE[role] ?? ROLE_TONE.BDE, className)}>{roleLabel(role)}</Badge>
  );
}

/** A shape per status as well as a colour, so the four read apart for
 *  somebody who cannot tell green from amber - or is looking at a phone in
 *  the sun. */
const REFERENCE_STATUS_ICON: Record<ReferenceStatus, React.ComponentType<{ className?: string }>> = {
  NOT_ASKED: CircleDashed,
  TAKEN: CheckCircle2,
  PENDING: Clock3,
  DECLINED: XCircle,
};

export function ReferenceStatusBadge({
  status,
  className,
}: {
  status: ReferenceStatus;
  className?: string;
}) {
  const Icon = REFERENCE_STATUS_ICON[status];
  return (
    <Badge className={cn(REFERENCE_STATUS_TONE[status], className)}>
      {Icon ? <Icon className="size-3 shrink-0" aria-hidden /> : null}
      {REFERENCE_STATUS_LABELS[status] ?? status}
    </Badge>
  );
}

export function ActiveBadge({ active }: { active: boolean }) {
  return (
    <Badge
      className={
        active
          ? "border-transparent bg-success-soft text-success"
          : "border-line bg-surface-2 text-subtle"
      }
    >
      <span
        className={cn("size-1.5 rounded-full", active ? "bg-success" : "bg-subtle")}
        aria-hidden
      />
      {active ? "Active" : "Inactive"}
    </Badge>
  );
}
