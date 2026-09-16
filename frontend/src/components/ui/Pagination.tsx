"use client";

import { ChevronLeft, ChevronRight } from "lucide-react";

import { Button } from "@/components/ui/Button";
import { formatNumber } from "@/lib/format";

export function Pagination({
  page,
  pageSize,
  total,
  onPageChange,
  noun = "records",
}: {
  page: number;
  pageSize: number;
  total: number;
  onPageChange: (page: number) => void;
  noun?: string;
}) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const from = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const to = Math.min(page * pageSize, total);

  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line px-4 py-3">
      <p className="text-[13px] text-muted">
        {total === 0 ? (
          <>No {noun}</>
        ) : (
          <>
            Showing <span className="font-medium text-content">{formatNumber(from)}</span>–
            <span className="font-medium text-content">{formatNumber(to)}</span> of{" "}
            <span className="font-medium text-content">{formatNumber(total)}</span> {noun}
          </>
        )}
      </p>

      {pages > 1 ? (
        <div className="flex items-center gap-1.5">
          <Button
            variant="secondary"
            size="sm"
            disabled={page <= 1}
            onClick={() => onPageChange(page - 1)}
          >
            <ChevronLeft className="size-3.5" aria-hidden />
            Previous
          </Button>
          <span className="px-2 text-[13px] tabular-nums text-muted">
            {page} / {pages}
          </span>
          <Button
            variant="secondary"
            size="sm"
            disabled={page >= pages}
            onClick={() => onPageChange(page + 1)}
          >
            Next
            <ChevronRight className="size-3.5" aria-hidden />
          </Button>
        </div>
      ) : null}
    </div>
  );
}
