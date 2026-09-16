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

export function ReferenceStatusBadge({
  status,
  className,
}: {
  status: ReferenceStatus;
  className?: string;
}) {
  return (
    <Badge className={cn(REFERENCE_STATUS_TONE[status], className)}>
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
