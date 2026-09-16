"use client";

import { useId } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { TooltipContentProps } from "recharts/types/component/Tooltip";

import { formatNumber } from "@/lib/format";

export interface TrendPoint {
  label: string;
  value: number;
}

function ChartTooltip({
  active,
  payload,
  valueLabel,
}: TooltipContentProps & { valueLabel: string }) {
  if (!active || !payload?.length) return null;
  const point = payload[0]!.payload as TrendPoint;
  return (
    <div className="rounded-lg border border-line bg-surface px-2.5 py-1.5 text-[12px] card-shadow">
      <p className="font-medium text-content">
        {formatNumber(point.value)} <span className="font-normal text-subtle">{valueLabel}</span>
      </p>
      <p className="text-subtle">{point.label}</p>
    </div>
  );
}

/**
 * A single-series trend over time, as a gradient-filled area.
 *
 * One series means no legend — the card title names it. `var(--info)` makes
 * the fill and line re-theme for free on the light/dark toggle, the same way
 * every other chart in this file does.
 */
export function TrendLine({
  data,
  height = 200,
  valueLabel = "responses",
}: {
  data: TrendPoint[];
  height?: number;
  valueLabel?: string;
}) {
  const gradientId = useId();

  if (data.length === 0) {
    return (
      <p className="py-10 text-center text-[13px] text-subtle">
        No responses yet — the trend appears once feedback is imported.
      </p>
    );
  }

  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
        <defs>
          <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--info)" stopOpacity={0.28} />
            <stop offset="100%" stopColor="var(--info)" stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid vertical={false} stroke="var(--line)" strokeDasharray="3 3" />
        <XAxis
          dataKey="label"
          tick={{ fontSize: 11, fill: "var(--text-subtle)" }}
          tickLine={false}
          axisLine={{ stroke: "var(--line)" }}
        />
        <YAxis
          allowDecimals={false}
          tick={{ fontSize: 11, fill: "var(--text-subtle)" }}
          tickLine={false}
          axisLine={false}
          width={28}
        />
        <Tooltip
          cursor={{ stroke: "var(--info)", strokeWidth: 1, strokeDasharray: "3 3" }}
          content={(props) => <ChartTooltip {...props} valueLabel={valueLabel} />}
        />
        <Area
          type="monotone"
          dataKey="value"
          stroke="var(--info)"
          strokeWidth={2}
          fill={`url(#${gradientId})`}
          dot={{ r: 3, fill: "var(--info)", stroke: "var(--surface)", strokeWidth: 1.5 }}
          activeDot={{ r: 5, fill: "var(--info)", stroke: "var(--surface)", strokeWidth: 2 }}
          // The line draws itself in on arrival rather than appearing whole.
          animationDuration={700}
          animationEasing="ease-out"
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}
