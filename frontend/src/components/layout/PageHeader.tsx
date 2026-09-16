import { cn } from "@/lib/cn";

export function PageHeader({
  title,
  description,
  actions,
  className,
}: {
  title: string;
  description?: React.ReactNode;
  actions?: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "mb-6 flex flex-wrap items-start justify-between gap-x-6 gap-y-3",
        className,
      )}
    >
      {/* The title leads, the description follows a beat later — the page
          reads in the order it arrives. */}
      <div className="stagger min-w-0" style={{ "--index": 0 } as React.CSSProperties}>
        {/* A step down on a phone: 20px over two lines pushes the content
            that answers the question off the first screen. */}
        <h2 className="text-[18px] font-semibold tracking-tight text-content sm:text-[20px]">
          {title}
        </h2>
        {description ? (
          <p className="mt-1 max-w-2xl text-[13.5px] leading-relaxed text-muted">
            {description}
          </p>
        ) : null}
      </div>
      {actions ? (
        <div
          className={cn(
            "stagger flex items-center gap-2",
            // `shrink-0` only once there is room to hold a line: on a 320px
            // screen a row of four badges cannot compress and drags the page
            // sideways. Below sm it wraps instead.
            "min-w-0 max-w-full flex-wrap sm:shrink-0",
          )}
          style={{ "--index": 1 } as React.CSSProperties}
        >
          {actions}
        </div>
      ) : null}
    </div>
  );
}
