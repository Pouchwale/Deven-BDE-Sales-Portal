"use client";

import {
  Bell,
  Building2,
  LayoutDashboard,
  MessageSquareHeart,
  Network,
  ScrollText,
  ShieldCheck,
  SlidersHorizontal,
  Target,
  UserRound,
  Users,
  X,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/cn";
import { canBrowseCustomers, isAdmin, isLeadership } from "@/lib/roles";
import type { Role } from "@/types/api";

interface NavItem {
  href: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  /** Who may see the link. The API enforces the same rule; this only stops us
   *  showing a door that would answer 403. */
  visible: (role: Role) => boolean;
  badge?: number;
}

/**
 * The five modules the project exists to deliver, plus administration.
 * Ordered the way the work actually flows: what happened, who referred us,
 * what we were given to chase, what customers said.
 */
function navigation(role: Role, unread: number): { section: string; items: NavItem[] }[] {
  return [
    {
      section: "Overview",
      items: [
        { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard, visible: () => true },
      ],
    },
    {
      section: "Modules",
      items: [
        {
          href: "/references",
          label: "Reference Tracking",
          icon: Users,
          visible: () => true,
        },
        { href: "/leads", label: "Assigned Leads", icon: Target, visible: () => true },
        {
          href: "/feedback",
          label: "Feedback & Reviews",
          icon: MessageSquareHeart,
          // Everyone: the pending-requests queue is the caller's OWN converted
          // work waiting on an ask, and hiding the module is what would break
          // the trail from a lead somebody just converted. The analysis,
          // responses and alerts inside are still department-scoped by the
          // API, and the page shows only what the caller may read.
          visible: () => true,
        },
        {
          href: "/notifications",
          label: "Notifications",
          icon: Bell,
          visible: () => true,
          badge: unread,
        },
      ],
    },
    {
      section: "Data",
      items: [
        // The SAP book: Super Admin only. The API refuses everyone else, so
        // hiding it here just stops people finding a door that will not open.
        { href: "/customers", label: "Customers", icon: Building2, visible: canBrowseCustomers },
      ],
    },
    {
      section: "Administration",
      items: [
        { href: "/team", label: "Team", icon: Network, visible: isLeadership },
        {
          href: "/admin/logs",
          label: "Activity log",
          icon: ScrollText,
          // Super Admin only, matching GET /admin/audit. The trail names who
          // did what to whom across the whole organisation.
          visible: (role) => role === "SUPER_ADMIN",
        },
        { href: "/admin/users", label: "User admin", icon: ShieldCheck, visible: isAdmin },
        {
          href: "/admin/settings",
          label: "Settings",
          icon: SlidersHorizontal,
          visible: isAdmin,
        },
      ],
    },
    {
      section: "Account",
      items: [{ href: "/profile", label: "My profile", icon: UserRound, visible: () => true }],
    },
  ];
}

export function Sidebar({
  role,
  unread = 0,
  open,
  collapsed = false,
  onClose,
}: {
  role: Role;
  unread?: number;
  /** The overlay drawer, below lg. */
  open: boolean;
  /** The permanent desktop sidebar, pushed off-screen at lg and above. */
  collapsed?: boolean;
  onClose: () => void;
}) {
  const pathname = usePathname();

  return (
    <>
      <div
        aria-hidden
        onClick={onClose}
        className={cn(
          "fixed inset-0 z-30 bg-black/40 backdrop-blur-[1px] transition-opacity duration-200 lg:hidden",
          open ? "opacity-100" : "pointer-events-none opacity-0",
        )}
      />

      <aside
        id="portal-nav"
        aria-label="Main navigation"
        className={cn(
          "fixed inset-y-0 left-0 z-40 flex w-64 flex-col border-r border-line bg-surface",
          "transition-transform duration-250 ease-out",
          // Below lg this is the overlay drawer, driven by `open`.
          open ? "translate-x-0" : "-translate-x-full",
          // At lg and above it is the permanent sidebar, and `collapsed` is
          // what moves it. Same transform, same transition, so the desktop
          // toggle slides exactly the way the mobile drawer does.
          collapsed ? "lg:-translate-x-full" : "lg:translate-x-0",
        )}
      >
        <div className="flex h-14 items-center justify-between gap-2 border-b border-line px-4">
          <Link href="/dashboard" className="flex min-w-0 items-center gap-2.5">
            <span className="inline-flex size-8 shrink-0 items-center justify-center rounded-lg bg-brand-600 text-[13px] font-bold text-white">
              BP
            </span>
            <span className="min-w-0">
              <span className="block truncate text-[13.5px] font-semibold leading-tight text-content">
                BDE &amp; Sales Portal
              </span>
              <span className="block truncate text-[11px] leading-tight text-subtle">
                Activity Management
              </span>
            </span>
          </Link>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close navigation"
            className="-mr-1.5 grid size-11 shrink-0 place-items-center rounded-lg text-subtle transition-colors hover:bg-surface-hover hover:text-content lg:hidden"
          >
            <X className="size-4" aria-hidden />
          </button>
        </div>

        <nav className="flex-1 space-y-5 overflow-y-auto px-3 py-4">
          {navigation(role, unread).map((group) => {
            const items = group.items.filter((item) => item.visible(role));
            if (items.length === 0) return null;

            return (
              <div key={group.section}>
                <p className="px-3 pb-1.5 text-[10.5px] font-semibold uppercase tracking-widest text-subtle">
                  {group.section}
                </p>
                <ul className="space-y-0.5">
                  {items.map((item) => {
                    const active =
                      pathname === item.href || pathname.startsWith(`${item.href}/`);
                    const Icon = item.icon;

                    return (
                      <li key={item.href}>
                        <Link
                          href={item.href}
                          onClick={onClose}
                          aria-current={active ? "page" : undefined}
                          className={cn(
                            "group relative flex items-center gap-2.5 rounded-lg px-3 py-2 text-[13.5px] font-medium",
                            "transition-[background-color,color,transform] duration-150",
                            active
                              ? "bg-brand-600/10 text-brand-700 dark:bg-brand-400/12 dark:text-brand-200"
                              : "text-muted hover:translate-x-0.5 hover:bg-surface-hover hover:text-content",
                          )}
                        >
                          {/* The marker grows out of the edge rather than
                              blinking on, so moving between pages reads as one
                              thing travelling down the list. */}
                          <span
                            className={cn(
                              "absolute left-0 top-1/2 h-4.5 w-0.5 origin-center -translate-y-1/2 rounded-r-full bg-brand-600 dark:bg-brand-400",
                              "transition-[opacity,transform] duration-200 ease-out",
                              active ? "scale-y-100 opacity-100" : "scale-y-0 opacity-0",
                            )}
                            aria-hidden
                          />
                          <Icon
                            className={cn(
                              "size-4 shrink-0 transition-transform duration-150",
                              !active && "group-hover:scale-110",
                            )}
                          />
                          <span className="flex-1 truncate">{item.label}</span>
                          {item.badge ? (
                            <span
                              className="animate-pop inline-flex min-w-4.5 items-center justify-center rounded-full bg-danger px-1.5 text-[10.5px] font-semibold text-white tabular-nums"
                              aria-label={`${item.badge} unread`}
                            >
                              {item.badge > 99 ? "99+" : item.badge}
                            </span>
                          ) : null}
                        </Link>
                      </li>
                    );
                  })}
                </ul>
              </div>
            );
          })}
        </nav>
      </aside>
    </>
  );
}
