"use client";

import { Bell, KeyRound, LogOut, Menu, Moon, Sun, UserRound } from "lucide-react";
import Link from "next/link";

import { preloadMobileNav } from "@/components/layout/Sidebar";
import { Avatar } from "@/components/ui/Avatar";
import { RoleBadge } from "@/components/ui/Badge";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/primitives/dropdown-menu";
import { cn } from "@/lib/cn";
import { withHonorific } from "@/lib/roles";
import { useAuth } from "@/lib/auth";
import { useTheme } from "@/lib/theme";
import type { User } from "@/types/api";

/** One size for every icon control in the bar: 44px on a phone, 40px with a
 *  mouse. */
const ICON_BUTTON = cn(
  "relative grid size-11 shrink-0 place-items-center rounded-lg text-muted sm:size-10",
  "transition-colors hover:bg-surface-hover hover:text-content",
);

export function Topbar({
  user,
  title,
  unread,
  navOpen,
  onOpenNav,
}: {
  user: User;
  title: string;
  unread: number;
  /** Whether the nav sheet is open — announced on the button that opens it. */
  navOpen: boolean;
  onOpenNav: () => void;
}) {
  const { theme, toggle } = useTheme();
  const { signOut } = useAuth();

  return (
    <header className="sticky top-0 z-20 flex h-14 items-center gap-1 border-b border-line bg-bg/85 px-3 backdrop-blur-md sm:gap-1.5 sm:px-6">
      {/* Below lg the navigation is a sheet opened from here. At lg and
          above it is always on screen and collapses from its own button. */}
      <button
        type="button"
        onClick={onOpenNav}
        onPointerEnter={() => void preloadMobileNav()}
        onTouchStart={() => void preloadMobileNav()}
        onFocus={() => void preloadMobileNav()}
        aria-label="Open navigation"
        aria-expanded={navOpen}
        aria-haspopup="dialog"
        data-nav-trigger
        className={cn(ICON_BUTTON, "-ml-1 lg:hidden")}
      >
        <Menu className="size-5" aria-hidden />
      </button>

      {/* Where you are. Not a heading: the page's own title is the h1, and a
          second one here would give every page two. */}
      <p className="min-w-0 flex-1 truncate pl-1 text-[14px] font-medium text-muted lg:pl-0">
        {title}
      </p>

      <Link
        href="/notifications"
        aria-label={unread > 0 ? `Notifications, ${unread} unread` : "Notifications"}
        className={ICON_BUTTON}
      >
        <Bell className="size-[18px]" aria-hidden />
        {unread > 0 ? (
          <span
            aria-hidden
            className="animate-pop absolute right-1.5 top-1.5 grid min-w-4 place-items-center rounded-full bg-danger px-1 text-[10px] font-semibold leading-4 text-white tabular-nums ring-2 ring-bg"
          >
            {unread > 9 ? "9+" : unread}
          </span>
        ) : null}
      </Link>

      <button
        type="button"
        onClick={toggle}
        aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
        className={ICON_BUTTON}
      >
        {theme === "dark" ? (
          <Sun className="size-[18px]" aria-hidden />
        ) : (
          <Moon className="size-[18px]" aria-hidden />
        )}
      </button>

      {/* A real menu (Radix): arrow keys, typeahead, Escape, and focus back on
          the trigger afterwards. */}
      <DropdownMenu>
        <DropdownMenuTrigger
          data-testid="user-menu-trigger"
          aria-label="Account menu"
          className={cn(
            "ml-0.5 flex h-11 items-center gap-2 rounded-lg pl-1 pr-1 transition-colors sm:h-10 sm:pr-2",
            "hover:bg-surface-hover data-[state=open]:bg-surface-hover",
          )}
        >
          <Avatar name={user.name} size="sm" />
          <span className="hidden max-w-32 truncate text-[13px] font-medium text-content sm:block">
            {withHonorific(user.name, user.honorific)}
          </span>
        </DropdownMenuTrigger>

        <DropdownMenuContent className="w-64">
          <DropdownMenuLabel className="flex items-center gap-3">
            <Avatar name={user.name} size="md" />
            <span className="min-w-0">
              <span className="block truncate text-[13.5px] font-medium text-content">
                {user.name}
              </span>
              <span className="block truncate text-[12px] font-normal text-subtle">
                {user.email}
              </span>
            </span>
          </DropdownMenuLabel>
          <div className="px-2.5 pb-2">
            <RoleBadge role={user.role} />
          </div>
          <DropdownMenuSeparator />
          <DropdownMenuGroup>
            <DropdownMenuItem asChild>
              <Link href="/profile">
                <UserRound aria-hidden />
                My profile
              </Link>
            </DropdownMenuItem>
            <DropdownMenuItem asChild>
              <Link href="/profile#security">
                <KeyRound aria-hidden />
                Security · Change password
              </Link>
            </DropdownMenuItem>
          </DropdownMenuGroup>
          <DropdownMenuSeparator />
          <DropdownMenuItem variant="destructive" data-testid="sign-out" onSelect={signOut}>
            <LogOut aria-hidden />
            Sign out
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </header>
  );
}
