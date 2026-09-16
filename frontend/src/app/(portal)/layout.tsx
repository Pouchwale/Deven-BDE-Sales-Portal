"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { ChatLauncher } from "@/components/chat/ChatLauncher";
import { Sidebar } from "@/components/layout/Sidebar";
import { Topbar } from "@/components/layout/Topbar";
import { Spinner } from "@/components/ui/Feedback";
import { cn } from "@/lib/cn";
import { useNavCollapsed } from "@/lib/useNavCollapsed";
import { useAuth } from "@/lib/auth";
import { ChatProvider } from "@/lib/chat";
import { NotificationsProvider, useNotifications } from "@/lib/notifications";

const TITLES: [prefix: string, title: string][] = [
  ["/dashboard", "Dashboard"],
  ["/references", "Reference Tracking"],
  ["/leads", "Assigned Leads"],
  ["/feedback", "Feedback & Reviews"],
  ["/notifications", "Notifications"],
  ["/customers", "Converted customers"],
  ["/team", "Team"],
  // Before /admin/users: the list is scanned in order and both share a
  // prefix boundary, so the more specific path has to come first.
  ["/admin/logs", "Activity log"],
  ["/admin/users", "User administration"],
  ["/admin/settings", "Settings"],
  ["/profile", "My profile"],
];

function titleFor(pathname: string): string {
  const match = TITLES.find(([prefix]) => pathname.startsWith(prefix));
  return match?.[1] ?? "Portal";
}

/**
 * The authenticated shell.
 *
 * The guard is client-side because the session is a bearer token in
 * localStorage, which the server never sees. The API is the real boundary —
 * this only decides what to render, and every endpoint re-checks
 * independently.
 */
export default function PortalLayout({ children }: { children: React.ReactNode }) {
  return (
    <NotificationsProvider>
      <ChatProvider>
        <PortalShell>{children}</PortalShell>
      </ChatProvider>
    </NotificationsProvider>
  );
}

function PortalShell({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  const [navOpen, setNavOpen] = useState(false);
  const { unread } = useNotifications();

  /**
   * The desktop sidebar, hidden or shown.
   *
   * Separate from `navOpen` on purpose: below lg the sidebar is an overlay you
   * open and dismiss, and it should always start closed. At lg and above it is
   * a permanent panel, and whether you want the screen width back is a
   * preference that ought to survive a navigation and a reload.
   */
  const [navCollapsed, toggleNav] = useNavCollapsed();

  useEffect(() => {
    if (loading) return;
    if (!user) {
      router.replace("/login");
    } else if (user.must_change_password) {
      // The API returns 403 PASSWORD_CHANGE_REQUIRED for everything else, so
      // there is nothing useful to render until this is done.
      router.replace("/set-password");
    }
  }, [user, loading, router]);

  // The drawer closes from the nav links themselves (Sidebar passes onClose
  // to every one), so there is no need to watch the pathname for it.

  // Escape closes it, and the page behind stops scrolling while it is open —
  // without the lock, a swipe on the backdrop scrolls the page underneath,
  // which reads as the app having lost track of what you are touching.
  useEffect(() => {
    if (!navOpen) return;

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setNavOpen(false);
    };
    document.addEventListener("keydown", onKeyDown);

    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = previousOverflow;
    };
  }, [navOpen]);

  if (loading || !user || user.must_change_password) {
    return (
      <div className="flex min-h-dvh items-center justify-center">
        <div className="flex items-center gap-2.5 text-sm text-muted">
          <Spinner />
          Loading your portal…
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-dvh">
      <Sidebar
        role={user.role}
        unread={unread}
        open={navOpen}
        collapsed={navCollapsed}
        onClose={() => setNavOpen(false)}
      />
      <div
        className={cn(
          // Matches the sidebar's own transition so the page and the panel
          // move together instead of the content snapping into place.
          "transition-[padding] duration-250 ease-out",
          navCollapsed ? "lg:pl-0" : "lg:pl-64",
        )}
      >
        <Topbar
          user={user}
          title={titleFor(pathname)}
          navOpen={navOpen}
          navCollapsed={navCollapsed}
          onOpenNav={() => setNavOpen(true)}
          onToggleNav={toggleNav}
        />
        {/* Keyed on the route so the entrance replays on every navigation —
            without the key React would reuse the element and the animation
            would only ever run once, on first load. */}
        <main
          key={pathname}
          className="page-enter mx-auto w-full max-w-[1400px] px-4 py-6 sm:px-6 lg:py-8"
        >
          {children}
        </main>
      </div>

      {/* Outside <main>, deliberately: `key={pathname}` remounts that subtree
          on every navigation, and a conversation must survive one. */}
      <ChatLauncher user={user} />
    </div>
  );
}
