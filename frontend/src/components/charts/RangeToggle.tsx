"use client";

import { cn } from "@/lib/cn";

const RANGES = [3, 6, 12] as const;
export type MonthRange = (typeof RANGES)[number];

/** A 3/6/12-month toggle for a trend chart that already has a year of data
 *  loaded — switching ranges slices the array in memory, no refetch. */
export function RangeToggle({
  value,
  onChange,
}: {
  value: MonthRange;
  onChange: (value: MonthRange) => void;
}) {
  return (
    <div role="radiogroup" aria-label="Months to show" className="flex gap-0.5 rounded-lg border border-line bg-surface-2 p-0.5">
      {RANGES.map((months) => (
        <button
          key={months}
          type="button"
          role="radio"
          aria-checked={value === months}
          onClick={() => onChange(months)}
          className={cn(
            "rounded-md px-2 py-1 text-[11.5px] font-medium transition-colors",
            value === months ? "bg-surface text-content card-shadow" : "text-muted hover:text-content",
          )}
        >
          {months}mo
        </button>
      ))}
    </div>
  );
}
