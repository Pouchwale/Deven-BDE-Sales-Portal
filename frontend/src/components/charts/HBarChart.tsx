"use client";

import { AlertTriangle } from "lucide-react";
import {
  Bar,
  BarChart,
  Cell,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { TooltipContentProps } from "recharts/types/component/Tooltip";

import { formatNumber } from "@/lib/format";

export interface BarDatum {
  label: string;
  value: number;
  /** Marks this bar as the one that needs attention — emphasis, not a
   *  categorical colour. Carries an icon and a label, never colour alone. */
  flagged?: boolean;
  hint?: string;
  href?: string;
}

function ChartTooltip({
  active,
  payload,
  valueFormatter,
}: TooltipContentProps & { valueFormatter: (value: number) => string }) {
  if (!active || !payload?.length) return null;
  const row = payload[0]!.payload as BarDatum;
  return (
    <div className="rounded-lg border border-line bg-surface px-2.5 py-1.5 text-[12px] card-shadow">
      <p className="font-medium text-content">{row.label}</p>
      <p className={row.flagged ? "font-semibold text-danger" : "text-muted"}>
        {valueFormatter(row.value)}
        {row.hint ? <span className="text-subtle"> · {row.hint}</span> : null}
      </p>
    </div>
  );
}

/**
 * Bars for comparing magnitude across a handful of named things.
 *
 * (The name is historical — every call site still imports HBarChart. The
 * orientation is now chosen from the data, see `horizontal` below.)
 *
 * Design decisions, deliberately:
 *   * ONE hue. Identity comes from the axis label, so per-bar colours would
 *     encode nothing and risk colouring by rank.
 *   * `flagged` uses the reserved status hue for emphasis — the one bar that
 *     matters — and always pairs it with an icon in the legend, never colour
 *     alone.
 *   * A threshold renders as a dashed reference line, which is what "compare
 *     against a baseline" looks like, and is named in the legend.
 *   * Colours are CSS custom properties (`var(--info)`, `var(--danger)`, …),
 *     so the chart re-themes for free on the light/dark toggle — no separate
 *     chart palette to keep in sync with globals.css.
 */
export function HBarChart({
  data,
  max,
  threshold,
  thresholdLabel,
  valueFormatter = (value) => formatNumber(value),
  emptyMessage = "Nothing to show yet.",
  height = 240,
}: {
  data: BarDatum[];
  max?: number;
  threshold?: number;
  thresholdLabel?: string;
  valueFormatter?: (value: number) => string;
  emptyMessage?: string;
  height?: number;
}) {
  if (data.length === 0) {
    return <p className="py-6 text-center text-[13px] text-subtle">{emptyMessage}</p>;
  }

  const ceiling = Math.max(max ?? 0, ...data.map((row) => row.value), 1);
  const hasFlagged = data.some((row) => row.flagged);

  /**
   * Which way the bars run is decided by the labels, not by taste.
   *
   * Short names fit along the bottom, and upright bars are what a person
   * pictures when they hear "bar chart". Long ones do not fit — rotating them
   * to squeeze in costs more legibility than turning the chart on its side —
   * so a department called "Customer Support" gets a horizontal bar with its
   * name written out flat.
   */
  const longest = Math.max(...data.map((row) => row.label.length));
  const horizontal = longest > 11 || data.length > 7;

  const ticks = { fontSize: 11.5, fill: "var(--text-muted)" };
  const valueTicks = { fontSize: 11, fill: "var(--text-subtle)" };
  const bars = (
    <Bar
      dataKey="value"
      radius={horizontal ? [0, 4, 4, 0] : [4, 4, 0, 0]}
      maxBarSize={horizontal ? 22 : 48}
      // Bars grow out of the baseline on arrival, on the same curve as the
      // cards around them.
      animationDuration={620}
      animationEasing="ease-out"
    >
      {data.map((row) => (
        <Cell key={row.label} fill={row.flagged ? "var(--danger)" : "var(--info)"} />
      ))}
    </Bar>
  );
  const tooltip = (
    <Tooltip
      cursor={{ fill: "var(--surface-2)" }}
      content={(props) => <ChartTooltip {...props} valueFormatter={valueFormatter} />}
    />
  );

  return (
    <div>
      <ResponsiveContainer
        width="100%"
        // A horizontal chart needs a row's worth of height per bar.
        height={horizontal ? Math.max(height, data.length * 34 + 52) : height}
      >
        {horizontal ? (
          <BarChart
            layout="vertical"
            data={data}
            margin={{ top: 4, right: 12, left: 0, bottom: 4 }}
            barCategoryGap="26%"
          >
            <XAxis
              type="number"
              domain={[0, ceiling]}
              tick={valueTicks}
              tickLine={false}
              axisLine={{ stroke: "var(--line)" }}
              tickFormatter={(value: number) => valueFormatter(value)}
              height={24}
            />
            <YAxis
              type="category"
              dataKey="label"
              tick={ticks}
              tickLine={false}
              axisLine={false}
              width={116}
              interval={0}
            />
            {tooltip}
            {threshold !== undefined ? (
              <ReferenceLine
                x={threshold}
                stroke="var(--warning)"
                strokeDasharray="4 3"
                strokeWidth={1.5}
              />
            ) : null}
            {bars}
          </BarChart>
        ) : (
          <BarChart
            data={data}
            margin={{ top: 8, right: 8, left: 0, bottom: 0 }}
            barCategoryGap="28%"
          >
            <XAxis
              dataKey="label"
              tick={ticks}
              tickLine={false}
              tickMargin={6}
              axisLine={{ stroke: "var(--line)" }}
              interval={0}
              height={26}
            />
            <YAxis
              domain={[0, ceiling]}
              tick={valueTicks}
              tickLine={false}
              axisLine={false}
              width={32}
              tickFormatter={(value: number) => valueFormatter(value)}
            />
            {tooltip}
            {threshold !== undefined ? (
              <ReferenceLine
                y={threshold}
                stroke="var(--warning)"
                strokeDasharray="4 3"
                strokeWidth={1.5}
              />
            ) : null}
            {bars}
          </BarChart>
        )}
      </ResponsiveContainer>

      {/* The legend states the threshold once, below the plot. Printed on the
          line itself it either sits over a bar or gets clipped at the edge,
          depending on the orientation. */}
      {threshold !== undefined || hasFlagged ? (
        <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11.5px]">
          {threshold !== undefined ? (
            <span className="flex items-center gap-1.5 text-subtle">
              <span
                className="inline-block w-4 border-t border-dashed border-warning"
                aria-hidden
              />
              {thresholdLabel ?? `Threshold ${threshold}`}
            </span>
          ) : null}
          {hasFlagged ? (
            <span className="flex items-center gap-1.5 text-danger">
              <AlertTriangle className="size-3" aria-hidden />
              Needs attention
            </span>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
