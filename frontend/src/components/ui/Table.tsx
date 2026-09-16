import { cn } from "@/lib/cn";

/**
 * Wide tables scroll inside their own container. The page body must never
 * scroll horizontally, so the overflow lives here rather than on an ancestor.
 */
export function TableWrap({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        // min-w-0 matters: inside a flex or grid parent a scroll container
        // still resolves to its content width without it, and the page gets
        // dragged wide by a table that was supposed to scroll on its own.
        "w-full min-w-0 overflow-x-auto md:overflow-x-auto",
        // Below md the table stacks, so there is nothing to scroll and the
        // padding should come from the card instead.
        "max-md:overflow-visible max-md:p-3",
        className,
      )}
      {...props}
    />
  );
}

export function Table({
  className,
  stack = true,
  ...props
}: React.TableHTMLAttributes<HTMLTableElement> & {
  /** Become a stack of cards below `md`. On by default — seven columns on a
   *  phone is a swipe-fest. Pass `false` for a table that genuinely reads as
   *  a grid, where the columns are the point. */
  stack?: boolean;
}) {
  return (
    <table
      className={cn(
        "w-full border-collapse text-left text-sm",
        // `min-w-max` is what makes a wide table scroll inside its wrapper
        // instead of squashing - but applied while stacked it forces the
        // card to max-content width and drags the whole page sideways. So it
        // is emitted only at the width where the table is still a table.
        stack ? "table-stack md:min-w-max" : "min-w-max",
        className,
      )}
      {...props}
    />
  );
}

export function THead({ className, ...props }: React.HTMLAttributes<HTMLTableSectionElement>) {
  return <thead className={cn("bg-surface-2", className)} {...props} />;
}

export function TH({
  className,
  align = "left",
  ...props
}: React.ThHTMLAttributes<HTMLTableCellElement> & { align?: "left" | "right" | "center" }) {
  return (
    <th
      scope="col"
      className={cn(
        "whitespace-nowrap border-b border-line px-4 py-2.5",
        "text-[11.5px] font-semibold uppercase tracking-wider text-subtle",
        align === "right" && "text-right",
        align === "center" && "text-center",
        className,
      )}
      {...props}
    />
  );
}

export function TBody({ className, ...props }: React.HTMLAttributes<HTMLTableSectionElement>) {
  return <tbody className={cn("divide-y divide-line", className)} {...props} />;
}

/**
 * Rows deliberately do NOT animate in one by one.
 *
 * A staggered entrance uses `animation-fill-mode: both`, which holds every
 * not-yet-started row at opacity 0 — on a 22-row table that reads as a wall
 * of blank rows, and it is choreography nobody asked for. The table as a
 * whole fades in; the rows just appear.
 */
export function TR({ className, ...props }: React.HTMLAttributes<HTMLTableRowElement>) {
  return (
    <tr
      className={cn("transition-colors duration-150 hover:bg-surface-hover", className)}
      {...props}
    />
  );
}

export function TD({
  className,
  align = "left",
  ...props
}: React.TdHTMLAttributes<HTMLTableCellElement> & { align?: "left" | "right" | "center" }) {
  return (
    <td
      className={cn(
        "px-4 py-2.5 align-middle text-content",
        align === "right" && "text-right tabular-nums",
        align === "center" && "text-center",
        className,
      )}
      {...props}
    />
  );
}
