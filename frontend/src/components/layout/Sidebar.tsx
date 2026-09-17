"use client";

import {
  Bell,
  ChevronLeft,
  ChevronRight,
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

import { BrandLogo } from "@/components/layout/BrandLogo";
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
  onToggleCollapsed,
}: {
  role: Role;
  unread?: number;
  /** The overlay drawer, below lg. */
  open: boolean;
  /** At lg and above: the slim icon rail instead of the full panel. */
  collapsed?: boolean;
  onClose: () => void;
  onToggleCollapsed?: () => void;
}) {
  const pathname = usePathname();
  const groups = navigation(role, unread)
    .map((group) => ({ ...group, items: group.items.filter((item) => item.visible(role)) }))
    .filter((group) => group.items.length > 0);

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
          "transition-[transform,width] duration-250 ease-out",
          // Below lg: the overlay drawer, always full width.
          open ? "translate-x-0" : "-translate-x-full",
          // lg and above: always on screen, as the full panel or the icon rail.
          "lg:translate-x-0",
          collapsed ? "lg:w-[76px]" : "lg:w-64",
        )}
      >
        {/* ------------------------------------------------------ brand */}
        <div
          className={cn(
            "flex h-[72px] shrink-0 items-center gap-3 px-4",
            collapsed && "lg:justify-center lg:px-0",
          )}
        >
          <Link
            href="/dashboard"
            onClick={onClose}
            className={cn("flex min-w-0 flex-1 items-center gap-3", collapsed && "lg:flex-none")}
            aria-label="BDE & Sales Portal - dashboard"
          >
            <BrandLogo size={40} />
            <span className={cn("min-w-0", collapsed && "lg:hidden")}>
              <span className="block truncate text-[15px] font-semibold leading-tight text-content">
                BDE &amp; Sales Portal
              </span>
              <span className="block truncate text-[11.5px] leading-tight text-subtle">
                Pouchwale
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

        {/* ------------------------------------------------------- links */}
        <nav
          className={cn(
            "flex-1 overflow-y-auto overflow-x-hidden px-3 pb-4 pt-2",
            collapsed && "lg:px-2.5",
          )}
        >
          {groups.map((group, index) => (
            <ul
              key={group.section}
              aria-label={group.section}
              className={cn(
                "space-y-1",
                // A hairline between groups rather than headings keeps the
                // list clean, and still works in the icon-only rail.
                index > 0 && "mt-2 border-t border-line/70 pt-2",
              )}
            >
              {group.items.map((item) => {
                const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
                const Icon = item.icon;

                return (
                  <li key={item.href}>
                    <Link
                      href={item.href}
                      onClick={onClose}
                      aria-current={active ? "page" : undefined}
                      aria-label={collapsed ? item.label : undefined}
                      title={collapsed ? item.label : undefined}
                      className={cn(
                        "group relative flex h-11 items-center gap-3 rounded-xl px-3.5 text-[14.5px] font-medium",
                        "transition-[background-color,box-shadow,color] duration-150",
                        collapsed && "lg:justify-center lg:px-0",
                        active
                          ? "bg-brand-600/10 text-brand-700 ring-1 ring-inset ring-brand-600/25 dark:bg-brand-400/12 dark:text-brand-200 dark:ring-brand-400/25"
                          : "text-muted hover:bg-surface-hover hover:text-content",
                      )}
                    >
                      <Icon
                        className={cn(
                          "size-[19px] shrink-0 transition-transform duration-150",
                          !active && "group-hover:scale-110",
                        )}
                      />
                      <span className={cn("flex-1 truncate", collapsed && "lg:hidden")}>
                        {item.label}
                      </span>
                      {item.badge ? (
                        <span
                          className={cn(
                            "animate-pop inline-flex min-w-4.5 items-center justify-center rounded-full bg-danger px-1.5 text-[10.5px] font-semibold text-white tabular-nums",
                            collapsed && "lg:absolute lg:right-1.5 lg:top-1 lg:min-w-4 lg:px-1",
                          )}
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
          ))}
        </nav>

        {/* --------------------------------------------- collapse toggle */}
        {onToggleCollapsed ? (
          <div className="hidden shrink-0 border-t border-line p-3 lg:block">
            <button
              type="button"
              onClick={onToggleCollapsed}
              aria-label={collapsed ? "Expand navigation" : "Collapse navigation"}
              aria-expanded={!collapsed}
              aria-controls="portal-nav"
              title={collapsed ? "Expand navigation" : "Collapse navigation"}
              className="grid h-10 w-full place-items-center rounded-full border border-line text-muted transition-colors hover:bg-surface-hover hover:text-content"
            >
              {collapsed ? (
                <ChevronRight className="size-4" aria-hidden />
              ) : (
                <ChevronLeft className="size-4" aria-hidden />
              )}
            </button>
          </div>
        ) : null}
      </aside>
    </>
  );
}
