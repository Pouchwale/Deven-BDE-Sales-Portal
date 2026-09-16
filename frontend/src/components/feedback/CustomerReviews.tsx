"use client";

import { MessageSquareQuote, Star, TriangleAlert } from "lucide-react";

import { Badge } from "@/components/ui/Badge";
import { Card, CardBody } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/Feedback";
import { cn } from "@/lib/cn";
import { formatDate } from "@/lib/format";
import type { DepartmentSummary, Feedback } from "@/types/api";

/**
 * What each customer actually said, and how it becomes the number we report.
 *
 * The table this replaces answered "how many responses and what score" but
 * never "what did they tell us" - the comments were the one thing it dropped,
 * and the comments are the reason anybody reads feedback at all.
 *
 * Each department row carries the customer's score AND the company average for
 * that department, because a 2 means something different when the company sits
 * at 4.5 than when it sits at 2.9. That pairing is the whole point: it shows
 * where our published rating comes from.
 */

/** A rating, as marks out of the scale. Readable at a glance, unlike "3.00". */
function Stars({
  value,
  max,
  size = "sm",
}: {
  value: number;
  max: number;
  size?: "sm" | "lg";
}) {
  const filled = Math.round(value);
  return (
    <span
      className="inline-flex items-center gap-0.5"
      aria-label={`${value} out of ${max}`}
    >
      {Array.from({ length: max }).map((_, index) => (
        <Star
          key={index}
          aria-hidden
          className={cn(
            size === "lg" ? "size-4" : "size-3",
            index < filled
              ? "fill-warning text-warning"
              : "fill-transparent text-line",
          )}
        />
      ))}
    </span>
  );
}

function toneFor(rating: number, threshold: number): string {
  if (rating < threshold) return "text-danger";
  if (rating < threshold + 1) return "text-warning";
  return "text-success";
}

export function CustomerReviews({
  items,
  departments,
  scaleMax,
  threshold,
}: {
  items: Feedback[];
  departments: DepartmentSummary[];
  scaleMax: number;
  threshold: number;
}) {
  const companyAverage = new Map(
    departments.map((row) => [row.department_name, row.average_rating]),
  );

  if (items.length === 0) {
    return (
      <Card>
        <EmptyState
          icon={MessageSquareQuote}
          title="No customer reviews yet"
          description="Once a customer fills in the feedback form, what they said and how they scored each department appears here."
        />
      </Card>
    );
  }

  return (
    <div className="space-y-3">
      {items.map((review) => {
        const overall = review.overall_rating ? Number(review.overall_rating) : null;
        const rated = review.department_ratings.filter((row) => row.rating !== null);

        return (
          <Card key={review.id} className="overflow-hidden">
            {/* ------------------------------------------------- who */}
            <div className="flex flex-wrap items-start justify-between gap-3 border-b border-line px-4 py-3 sm:px-5">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <h3 className="truncate text-[14px] font-semibold text-content">
                    {review.customer_name ?? "Unnamed customer"}
                  </h3>
                  {review.is_sample ? (
                    <Badge className="border-warning/40 bg-warning-soft text-warning">
                      Sample
                    </Badge>
                  ) : null}
                  {review.would_recommend ? (
                    <Badge className="border-line bg-surface-2 text-muted">
                      Recommends: {review.would_recommend}
                    </Badge>
                  ) : null}
                </div>
                <p className="mt-0.5 text-[12px] text-subtle">
                  {formatDate(review.submitted_at_source ?? review.created_at)}
                  {review.handled_by_name ? ` · handled by ${review.handled_by_name}` : ""}
                </p>
              </div>

              {overall !== null ? (
                <div className="shrink-0 text-right">
                  <div className="flex items-center justify-end gap-1.5">
                    <span
                      className={cn(
                        "text-[18px] font-semibold tabular-nums",
                        toneFor(overall, threshold),
                      )}
                    >
                      {overall.toFixed(1)}
                    </span>
                    <span className="text-[12px] text-subtle">/ {scaleMax}</span>
                  </div>
                  <Stars value={overall} max={scaleMax} size="lg" />
                </div>
              ) : null}
            </div>

            <CardBody className="space-y-3.5">
              {/* --------------------------------------- what they said */}
              {review.overall_comments ? (
                <blockquote className="border-l-2 border-brand-600/40 pl-3 text-[13.5px] leading-relaxed text-content">
                  &ldquo;{review.overall_comments}&rdquo;
                </blockquote>
              ) : null}

              {/* ----------------------------- what they scored, and ours */}
              {rated.length > 0 ? (
                <div>
                  <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-subtle">
                    Department by department
                  </p>
                  <ul className="divide-y divide-line rounded-lg border border-line">
                    {rated.map((row) => {
                      const score = Number(row.rating);
                      const ours = companyAverage.get(row.department_name) ?? null;
                      return (
                        <li
                          key={row.department_id}
                          className="flex flex-wrap items-start gap-x-3 gap-y-1 px-3 py-2"
                        >
                          <span className="w-32 shrink-0 text-[13px] font-medium text-content">
                            {row.department_name}
                          </span>

                          <span className="flex shrink-0 items-center gap-2">
                            <Stars value={score} max={scaleMax} />
                            <span
                              className={cn(
                                "text-[13px] font-semibold tabular-nums",
                                toneFor(score, threshold),
                              )}
                            >
                              {score.toFixed(1)}
                            </span>
                            {score < threshold ? (
                              <TriangleAlert
                                className="size-3.5 text-danger"
                                aria-label="below the alert threshold"
                              />
                            ) : null}
                          </span>

                          {/* Where our published number for this department
                              sits, so the review can be read against it. */}
                          {ours !== null ? (
                            <span className="shrink-0 text-[12px] text-subtle">
                              our average {ours.toFixed(1)}
                            </span>
                          ) : null}

                          {row.comments ? (
                            <span className="w-full text-[12.5px] leading-relaxed text-muted sm:w-auto sm:flex-1 sm:min-w-40">
                              &ldquo;{row.comments}&rdquo;
                            </span>
                          ) : null}
                        </li>
                      );
                    })}
                  </ul>
                </div>
              ) : (
                <p className="text-[12.5px] text-subtle">
                  This response scored the company overall but did not rate the
                  individual departments.
                </p>
              )}
            </CardBody>
          </Card>
        );
      })}
    </div>
  );
}
