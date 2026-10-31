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
} from "lucide-react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";

import { BrandLogo } from "@/components/layout/BrandLogo";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/primitives/tooltip";
import { cn } from "@/lib/cn";
import { FEEDBACK_MODULE_OPEN } from "@/lib/features";
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
  /** Shown but closed: struck through, dark, not a link. */
  disabled?: boolean;
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
          disabled: !FEEDBACK_MODULE_OPEN,
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

/**
 * The phone navigation sheet (Radix Dialog) is only ever needed after somebody
 * taps the menu button below lg, so it is fetched then - not shipped in the
 * shared bundle every desktop page load pays for. The Topbar prefetches it on
 * the first touch or hover of that button, so the tap itself does not wait.
 */
export const preloadMobileNav = () => import("@/components/layout/MobileNav");
const MobileNav = dynamic(() => preloadMobileNav().then((module) => module.MobileNav), {
  ssr: false,
});

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
  /** The navigation sheet, below lg. */
  open: boolean;
  /** At lg and above: the slim icon rail instead of the full panel. */
  collapsed?: boolean;
  onClose: () => void;
  onToggleCollapsed?: () => void;
}) {
  // Mounted on first open and kept, so it can play its own exit animation.
  const [everOpened, setEverOpened] = useState(open);
  if (open && !everOpened) setEverOpened(true);

  return (
    <>
      {/* lg and up: a permanent panel, full or as an icon rail. */}
      <aside
        id="portal-nav"
        aria-label="Main navigation"
        className={cn(
          "fixed inset-y-0 left-0 z-30 hidden flex-col border-r border-line bg-surface lg:flex",
          "transition-[width] duration-250 ease-out",
          collapsed ? "w-[76px]" : "w-64",
        )}
      >
        <NavContent role={role} unread={unread} collapsed={collapsed} />

        {onToggleCollapsed ? (
          <div className={cn("shrink-0 border-t border-line p-3", collapsed && "px-2.5")}>
            <button
              type="button"
              onClick={onToggleCollapsed}
              aria-label={collapsed ? "Expand navigation" : "Collapse navigation"}
              aria-expanded={!collapsed}
              className={cn(
                "flex h-9 w-full items-center gap-2.5 rounded-lg px-3 text-[13px] text-subtle",
                "transition-colors hover:bg-surface-hover hover:text-content",
                collapsed && "justify-center px-0",
              )}
            >
              {collapsed ? (
                <ChevronRight className="size-4" aria-hidden />
              ) : (
                <>
                  <ChevronLeft className="size-4" aria-hidden />
                  Collapse
                </>
              )}
            </button>
          </div>
        ) : null}
      </aside>

      {everOpened ? (
        <MobileNav role={role} unread={unread} open={open} onClose={onClose} />
      ) : null}
    </>
  );
}

export function NavContent({
  role,
  unread,
  collapsed = false,
  onNavigate,
}: {
  role: Role;
  unread: number;
  collapsed?: boolean;
  onNavigate?: () => void;
}) {
  const pathname = usePathname();
  const groups = navigation(role, unread)
    .map((group) => ({ ...group, items: group.items.filter((item) => item.visible(role)) }))
    .filter((group) => group.items.length > 0);

  return (
    <>
      {/* ------------------------------------------------------ brand */}
      <div className={cn("flex h-16 shrink-0 items-center px-4", collapsed && "justify-center px-0")}>
        <Link
          href="/dashboard"
          onClick={onNavigate}
          className="flex min-w-0 items-center gap-3"
          aria-label="BDE & Sales Portal - dashboard"
        >
          <BrandLogo size={34} />
          {collapsed ? null : (
            <span className="min-w-0">
              <span className="block truncate text-[14px] font-semibold leading-tight text-content">
                BDE &amp; Sales Portal
              </span>
              <span className="block truncate text-[11.5px] leading-tight text-subtle">
                Pouchwale
              </span>
            </span>
          )}
        </Link>
      </div>

      {/* ------------------------------------------------------- links */}
      <nav
        aria-label="Main"
        className={cn("flex-1 overflow-y-auto overflow-x-hidden px-3 pb-4", collapsed && "px-2.5")}
      >
        {groups.map((group, index) => {
          // A label names the group when there is room to read it; the icon
          // rail keeps a hairline instead. "Overview" and "Account" are one
          // link each and a label would add nothing.
          const labelled = !collapsed && group.items.length > 1;
          return (
            <div key={group.section} className={cn(index > 0 && "mt-4")}>
              {labelled ? (
                <p className="mb-1 px-3 text-[11px] font-medium uppercase tracking-[0.06em] text-subtle">
                  {group.section}
                </p>
              ) : index > 0 && collapsed ? (
                <div className="mx-2 mb-3 border-t border-line" aria-hidden />
              ) : null}
              <ul aria-label={group.section} className="flex flex-col gap-0.5">
                {group.items.map((item) => (
                  <li key={item.href}>
                    <NavLink
                      item={item}
                      active={pathname === item.href || pathname.startsWith(`${item.href}/`)}
                      collapsed={collapsed}
                      onNavigate={onNavigate}
                    />
                  </li>
                ))}
              </ul>
            </div>
          );
        })}
      </nav>
    </>
  );
}

function NavLink({
  item,
  active,
  collapsed,
  onNavigate,
}: {
  item: NavItem;
  active: boolean;
  collapsed: boolean;
  onNavigate?: () => void;
}) {
  const Icon = item.icon;
  if (item.disabled) {
    // Not in its working phase: visible, so nobody wonders where it went,
    // but struck through, dark and not a link.
    const closed = (
      <span
        aria-disabled="true"
        title={`${item.label} is not open yet`}
        className={cn(
          "flex h-11 cursor-not-allowed select-none items-center gap-3 rounded-lg px-3 text-[14px] font-medium text-subtle opacity-50 lg:h-[38px]",
          collapsed && "justify-center px-0",
        )}
      >
        <Icon className="size-[18px] shrink-0" />
        {collapsed ? null : <span className="flex-1 truncate line-through">{item.label}</span>}
      </span>
    );
    if (!collapsed) return closed;
    return (
      <Tooltip>
        <TooltipTrigger asChild>{closed}</TooltipTrigger>
        <TooltipContent side="right">{item.label} (not open yet)</TooltipContent>
      </Tooltip>
    );
  }
  const link = (
    <Link
      href={item.href}
      onClick={onNavigate}
      aria-current={active ? "page" : undefined}
      aria-label={collapsed ? item.label : undefined}
      className={cn(
        // 44px tall in the phone sheet (a thumb target), 38px on desktop.
        "group relative flex h-11 items-center gap-3 rounded-lg px-3 text-[14px] lg:h-[38px]",
        "transition-colors duration-150",
        collapsed && "justify-center px-0",
        active
          ? "bg-brand-600/8 font-semibold text-brand-700 dark:bg-brand-400/12 dark:text-brand-200"
          : "font-medium text-muted hover:bg-surface-hover hover:text-content",
      )}
    >
      {/* The active marker: a short bar on the leading edge, so the current
          page is not signalled by colour alone. */}
      {active ? (
        <span
          aria-hidden
          className={cn(
            "absolute top-1/2 h-5 w-[3px] -translate-y-1/2 rounded-r-full bg-brand-600 dark:bg-brand-400",
            collapsed ? "-left-2.5" : "-left-3",
          )}
        />
      ) : null}
      <Icon
        className={cn("size-[18px] shrink-0", !active && "text-subtle group-hover:text-content")}
      />
      {collapsed ? null : <span className="flex-1 truncate">{item.label}</span>}
      {item.badge ? (
        <span
          className={cn(
            "animate-pop inline-flex min-w-5 items-center justify-center rounded-full bg-danger px-1.5 text-[10.5px] font-semibold leading-5 text-white tabular-nums",
            collapsed && "absolute right-1 top-0.5 min-w-4 px-1 leading-4",
          )}
          aria-label={`${item.badge} unread`}
        >
          {item.badge > 99 ? "99+" : item.badge}
        </span>
      ) : null}
    </Link>
  );

  if (!collapsed) return link;

  // The icon rail names each link on hover AND on keyboard focus - `title`
  // did neither reliably, and never for keyboard users.
  return (
    <Tooltip>
      <TooltipTrigger asChild>{link}</TooltipTrigger>
      <TooltipContent side="right">{item.label}</TooltipContent>
    </Tooltip>
  );
}
