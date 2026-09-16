"use client";

import dynamic from "next/dynamic";

/**
 * The charts, fetched when they are actually rendered.
 *
 * recharts is by far the heaviest thing the portal ships, and both pages that
 * use it (Dashboard, Feedback) draw their charts BELOW the fold, under KPI
 * tiles and tables that people read first. Importing them statically put the
 * whole library in those routes' first load.
 *
 * The placeholders reserve exactly the height the real chart occupies
 * (HBarChart 240, TrendLine 200 - their defaults, and no call site overrides
 * them), so nothing on the page moves when the chart arrives.
 */
function ChartPlaceholder({ height }: { height: number }) {
  return (
    <div
      aria-hidden
      className="animate-pulse rounded-lg bg-surface-2"
      style={{ height }}
    />
  );
}

export const HBarChart = dynamic(
  () => import("@/components/charts/HBarChart").then((module) => module.HBarChart),
  { ssr: false, loading: () => <ChartPlaceholder height={240} /> },
);

export const TrendLine = dynamic(
  () => import("@/components/charts/TrendLine").then((module) => module.TrendLine),
  { ssr: false, loading: () => <ChartPlaceholder height={200} /> },
);
