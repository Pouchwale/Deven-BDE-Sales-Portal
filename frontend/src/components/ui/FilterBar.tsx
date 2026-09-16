"use client";

import { X } from "lucide-react";

import { Button } from "@/components/ui/Button";
import { Input, Select } from "@/components/ui/Form";
import { cn } from "@/lib/cn";

/**
 * One filter bar, used by every module.
 *
 * Three things a CRM filter has to do, and the reason each is here:
 *
 *   1. Narrow the list. Obvious, and the easy part.
 *   2. Say what is currently applied. A list showing 4 of 200 rows with the
 *      controls scrolled out of sight looks like missing data, and that is
 *      how people end up convinced the portal has lost something.
 *   3. Get out of the way in one click. A filter you cannot clear is a filter
 *      people stop using.
 *
 * So the active filters are rendered as removable chips underneath, and a
 * "Clear all" appears as soon as anything is set. The count of matching rows
 * sits alongside, because "how many did that leave" is the question the
 * person is actually asking.
 *
 * FILTERING IS SERVER-SIDE. Every value here becomes a query parameter and
 * the database does the work. Fetching a page and filtering it in React would
 * narrow only the rows that happen to have been fetched, which is worse than
 * useless: it looks like an answer.
 */

export interface FilterOption {
  value: string;
  label: string;
  /** Optional count, shown in the dropdown - "Qualified (12)". */
  count?: number;
}

export interface FilterSpec {
  /** Stable key, used for the query parameter and the chip. */
  key: string;
  /** Shown above the control and on the chip: "Stage", "Owner". */
  label: string;
  value: string;
  options: FilterOption[];
  onChange: (value: string) => void;
  /** Wording for the "no filter" option. Defaults to "All". */
  anyLabel?: string;
}

export function FilterBar({
  search,
  onSearchChange,
  searchPlaceholder = "Search…",
  searchLabel,
  filters = [],
  resultCount,
  resultNoun = "results",
  className,
  children,
}: {
  search?: string;
  onSearchChange?: (value: string) => void;
  searchPlaceholder?: string;
  /** Accessible name for the search box. The placeholder is usually a
   *  sentence, which makes a poor label for a screen reader. */
  searchLabel?: string;
  filters?: FilterSpec[];
  /** How many rows the current filters left. Omit when it is not known yet. */
  resultCount?: number;
  resultNoun?: string;
  className?: string;
  /** Anything module-specific - a toggle, an extra button. */
  children?: React.ReactNode;
}) {
  const active = filters.filter((filter) => filter.value !== "");
  const hasSearch = Boolean(search && search.trim());
  const anything = active.length > 0 || hasSearch;

  function clearAll() {
    for (const filter of active) filter.onChange("");
    onSearchChange?.("");
  }

  return (
    <div className={cn("border-b border-line p-3.5", className)}>
      <div className="flex flex-wrap items-end gap-2.5">
        {onSearchChange ? (
          <div className="min-w-52 flex-1">
            <Input
              type="search"
              placeholder={searchPlaceholder}
              value={search ?? ""}
              onChange={(event) => onSearchChange(event.target.value)}
              aria-label={searchLabel ?? searchPlaceholder}
            />
          </div>
        ) : null}

        {filters.map((filter) => (
          <label key={filter.key} className="block">
            <span className="mb-1 block text-[11.5px] font-medium text-subtle">
              {filter.label}
            </span>
            <Select
              value={filter.value}
              onChange={(event) => filter.onChange(event.target.value)}
              aria-label={filter.label}
            >
              <option value="">{filter.anyLabel ?? "All"}</option>
              {filter.options.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                  {option.count === undefined ? "" : ` (${option.count})`}
                </option>
              ))}
            </Select>
          </label>
        ))}

        {children}
      </div>

      {/* What is currently applied, and how to undo it. */}
      {anything ? (
        <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
          {hasSearch ? (
            <Chip label={`Search: "${search}"`} onRemove={() => onSearchChange?.("")} />
          ) : null}
          {active.map((filter) => (
            <Chip
              key={filter.key}
              label={`${filter.label}: ${
                filter.options.find((option) => option.value === filter.value)?.label ??
                filter.value
              }`}
              onRemove={() => filter.onChange("")}
            />
          ))}
          <Button size="sm" variant="subtle" onClick={clearAll}>
            Clear all
          </Button>
          {resultCount === undefined ? null : (
            <span className="ml-auto text-[12.5px] text-muted">
              {resultCount} {resultNoun}
            </span>
          )}
        </div>
      ) : null}
    </div>
  );
}

function Chip({ label, onRemove }: { label: string; onRemove: () => void }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-full border border-line bg-surface-2 py-1 pl-2.5 pr-1 text-[12.5px] text-muted">
      {label}
      <button
        type="button"
        onClick={onRemove}
        aria-label={`Remove filter ${label}`}
        className="grid size-5 place-items-center rounded-full text-subtle transition-colors hover:bg-surface-hover hover:text-content"
      >
        <X className="size-3" aria-hidden />
      </button>
    </span>
  );
}
