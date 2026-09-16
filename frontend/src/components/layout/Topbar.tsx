"use client";

import { LogOut, Menu, Moon, Sun, UserRound } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { Avatar } from "@/components/ui/Avatar";
import { RoleBadge } from "@/components/ui/Badge";
import { cn } from "@/lib/cn";
import { withHonorific } from "@/lib/roles";
import { useAuth } from "@/lib/auth";
import { useTheme } from "@/lib/theme";
import type { User } from "@/types/api";

export function Topbar({
  user,
  title,
  navOpen,
  navCollapsed,
  onOpenNav,
  onToggleNav,
}: {
  user: User;
  title: string;
  /** Whether the nav drawer is open — announced on the button that opens it. */
  navOpen: boolean;
  /** Whether the desktop sidebar is currently pushed off-screen. */
  navCollapsed: boolean;
  onOpenNav: () => void;
  onToggleNav: () => void;
}) {
  const { theme, toggle } = useTheme();
  const { signOut } = useAuth();
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!menuOpen) return;

    const onPointerDown = (event: MouseEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) setMenuOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMenuOpen(false);
    };

    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [menuOpen]);

  return (
    <header className="sticky top-0 z-20 flex h-14 items-center gap-3 border-b border-line bg-bg/85 px-4 backdrop-blur-md sm:px-6">
      {/* Two buttons rather than one that guesses the viewport in JS: below
          lg the sidebar is an overlay drawer you OPEN, at lg and above it is
          a permanent panel you COLLAPSE. Same icon in the same place, so it
          reads as one control; different verbs, so the label and aria-expanded
          are honest about which one you are pressing. */}
      <button
        type="button"
        onClick={onOpenNav}
        aria-label="Open navigation"
        aria-expanded={navOpen}
        aria-controls="portal-nav"
        // p-2.5 on a 18px icon gives a 44px target — the smallest a thumb
        // hits reliably.
        className="-ml-1.5 grid size-11 shrink-0 place-items-center rounded-lg text-muted transition-colors hover:bg-surface-hover hover:text-content lg:hidden"
      >
        <Menu className="size-5" aria-hidden />
      </button>

      <button
        type="button"
        onClick={onToggleNav}
        aria-label={navCollapsed ? "Show navigation" : "Hide navigation"}
        aria-expanded={!navCollapsed}
        aria-controls="portal-nav"
        title={navCollapsed ? "Show navigation" : "Hide navigation"}
        className="-ml-1.5 hidden size-11 shrink-0 place-items-center rounded-lg text-muted transition-colors hover:bg-surface-hover hover:text-content lg:grid"
      >
        <Menu className="size-5" aria-hidden />
      </button>

      <h1 className="min-w-0 flex-1 truncate text-[15px] font-semibold text-content">{title}</h1>

      <button
        type="button"
        onClick={toggle}
        aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
        className="rounded-lg p-2 text-muted transition-colors hover:bg-surface-hover hover:text-content"
      >
        {theme === "dark" ? (
          <Sun className="size-4.5" aria-hidden />
        ) : (
          <Moon className="size-4.5" aria-hidden />
        )}
      </button>

      <div className="relative" ref={menuRef}>
        <button
          type="button"
          onClick={() => setMenuOpen((open) => !open)}
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          data-testid="user-menu-trigger"
          className={cn(
            "flex items-center gap-2 rounded-lg py-1 pl-1 pr-2 transition-colors",
            "hover:bg-surface-hover",
            menuOpen && "bg-surface-hover",
          )}
        >
          <Avatar name={user.name} size="sm" />
          <span className="hidden max-w-32 truncate text-[13px] font-medium text-content sm:block">
            {withHonorific(user.name, user.honorific)}
          </span>
        </button>

        {menuOpen ? (
          <div
            role="menu"
            className="absolute right-0 top-full z-30 mt-1.5 w-60 origin-top-right animate-scale-in overflow-hidden rounded-xl border border-line bg-surface card-shadow-lg"
          >
            <div className="flex items-center gap-3 border-b border-line px-3.5 py-3">
              <Avatar name={user.name} size="md" />
              <div className="min-w-0">
                <p className="truncate text-[13.5px] font-medium text-content">{user.name}</p>
                <p className="truncate text-[12px] text-subtle">{user.email}</p>
              </div>
            </div>
            <div className="border-b border-line px-3.5 py-2.5">
              <RoleBadge role={user.role} />
            </div>
            <div className="p-1.5">
              <Link
                href="/profile"
                role="menuitem"
                onClick={() => setMenuOpen(false)}
                className="flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-[13px] text-muted transition-colors hover:bg-surface-hover hover:text-content"
              >
                <UserRound className="size-4" aria-hidden />
                My profile
              </Link>
              <button
                type="button"
                role="menuitem"
                data-testid="sign-out"
                onClick={signOut}
                className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-[13px] text-muted transition-colors hover:bg-danger-soft hover:text-danger"
              >
                <LogOut className="size-4" aria-hidden />
                Sign out
              </button>
            </div>
          </div>
        ) : null}
      </div>
    </header>
  );
}
