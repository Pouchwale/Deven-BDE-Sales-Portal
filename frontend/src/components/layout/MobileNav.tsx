"use client";

import { NavContent } from "@/components/layout/Sidebar";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/primitives/sheet";
import type { Role } from "@/types/api";

/**
 * Navigation below lg, as a sheet. Radix moves focus in, traps it, closes on
 * Escape or a tap outside, and stops the page behind from scrolling. The
 * closed panel is not in the DOM at all, so its links cannot be reached by
 * Tab while hidden. Loaded on demand - see `preloadMobileNav` in Sidebar.
 */
export function MobileNav({
  role,
  unread,
  open,
  onClose,
}: {
  role: Role;
  unread: number;
  open: boolean;
  onClose: () => void;
}) {
  return (
    <Sheet open={open} onOpenChange={(next) => (next ? undefined : onClose())}>
      <SheetContent
        side="left"
        className="lg:hidden"
        aria-describedby={undefined}
        // The button that opened it lives in the Topbar, not in a Radix
        // Trigger, so Radix cannot find it on its own - without this, focus
        // falls to <body> and a keyboard user starts again from the top.
        onCloseAutoFocus={(event) => {
          event.preventDefault();
          document.querySelector<HTMLElement>("[data-nav-trigger]")?.focus();
        }}
      >
        <SheetTitle className="sr-only">Navigation</SheetTitle>
        <NavContent role={role} unread={unread} onNavigate={onClose} />
      </SheetContent>
    </Sheet>
  );
}
