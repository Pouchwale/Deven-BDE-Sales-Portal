"use client";

import { ChevronRight } from "lucide-react";
import { useMemo, useState } from "react";

import { Avatar } from "@/components/ui/Avatar";
import { ActiveBadge, RoleBadge } from "@/components/ui/Badge";
import { cn } from "@/lib/cn";
import type { OrgNode } from "@/types/api";

/**
 * The reporting chain, drawn as a tree.
 *
 * A node whose manager is outside the caller's scope becomes a root of the
 * caller's view — which is exactly right: a manager sees their own subtree,
 * hanging from themselves, and never anything above.
 */
export function OrgTree({ nodes, focusId }: { nodes: OrgNode[]; focusId?: string | null }) {
  // People who lead a branch come first. Otherwise the seven unassigned
  // accounts sort alphabetically to the top and push the actual org chart
  // below the fold.
  const roots = useMemo(
    () =>
      [...nodes].sort((a, b) => {
        const branch = Number(b.reports.length > 0) - Number(a.reports.length > 0);
        return branch !== 0 ? branch : a.name.localeCompare(b.name);
      }),
    [nodes],
  );

  return (
    <ul className="space-y-1.5">
      {roots.map((node) => (
        <OrgTreeNode key={node.id} node={node} focusId={focusId} />
      ))}
    </ul>
  );
}

function countSubtree(node: OrgNode): number {
  return node.reports.reduce((total, child) => total + 1 + countSubtree(child), 0);
}

function OrgTreeNode({
  node,
  focusId,
}: {
  node: OrgNode;
  focusId?: string | null;
}) {
  const [expanded, setExpanded] = useState(true);
  const hasReports = node.reports.length > 0;
  // A recursive walk, and every node in the tree runs it - so without this the
  // whole chart is re-counted from scratch on each expand/collapse click.
  const below = useMemo(() => countSubtree(node), [node]);
  const focused = focusId === node.id;

  return (
    <li>
      <div
        className={cn(
          "group flex items-center gap-3 rounded-xl border bg-surface p-2.5 pr-3.5",
          "transition-[border-color,box-shadow,transform] duration-200",
          "hover:-translate-y-px hover:border-line-strong hover:card-shadow",
          focused ? "border-brand-500 ring-4 ring-brand-500/12" : "border-line",
          !node.is_active && "opacity-70",
        )}
      >
        <button
          type="button"
          onClick={() => setExpanded((open) => !open)}
          disabled={!hasReports}
          aria-label={hasReports ? (expanded ? "Collapse" : "Expand") : undefined}
          aria-expanded={hasReports ? expanded : undefined}
          className={cn(
            "shrink-0 rounded-md p-0.5 text-subtle transition-all duration-200",
            hasReports
              ? "hover:bg-surface-hover hover:text-content"
              : "pointer-events-none opacity-0",
            expanded && "rotate-90",
          )}
        >
          <ChevronRight className="size-4" aria-hidden />
        </button>

        <Avatar name={node.name} size="sm" />

        <div className="min-w-0 flex-1">
          <p className="truncate text-[13.5px] font-medium text-content">{node.name}</p>
          {/* "BDE · BDE" — a job title that happens to match the team name —
              reads as a rendering bug, so the duplicate is dropped. */}
          <p className="truncate text-[12px] text-subtle">
            {[node.title, node.team_name === node.title ? null : node.team_name]
              .filter(Boolean)
              .join(" · ") || "—"}
          </p>
        </div>

        <div className="flex shrink-0 items-center gap-1.5">
          {hasReports ? (
            <span className="hidden text-[11.5px] tabular-nums text-subtle sm:inline">
              {below} below
            </span>
          ) : null}
          {node.is_active ? null : <ActiveBadge active={false} />}
          <RoleBadge role={node.role} />
        </div>
      </div>

      {hasReports && expanded ? (
        // The rail is the visual "reports to" line. Padding on the left of
        // the list, border on that padding — no absolute positioning needed.
        <ul className="ml-[1.4rem] mt-1.5 space-y-1.5 border-l border-line pl-4">
          {node.reports.map((child) => (
            <OrgTreeNode key={child.id} node={child} focusId={focusId} />
          ))}
        </ul>
      ) : null}
    </li>
  );
}
