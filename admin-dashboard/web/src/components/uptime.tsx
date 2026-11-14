"use client";

import type { UptimeStats } from "@/lib/api";
import { duration, pct } from "@/lib/format";

/** Headline uptime. With insufficient history the percentage is never the headline: a few minutes of
 *  data must not read as "100% over 24 hours / 30 days". */
export function UptimeFigure({ stats, big = false }: { stats: UptimeStats; big?: boolean }) {
  const size = big ? "text-[22px]" : "text-lg";
  if (stats.uptime_pct === null) {
    return <span className={`${size} font-semibold text-faint`}>No data</span>;
  }
  if (!stats.sufficient) {
    return (
      <span
        className={`${big ? "text-[17px]" : "text-[15px]"} font-semibold text-muted`}
        title={`Only ${duration(stats.observed_seconds)} of this window has been observed (${stats.coverage_pct.toFixed(2)}% coverage).`}
      >
        Insufficient history
      </span>
    );
  }
  return <span className={`${size} font-semibold text-ink`}>{pct(stats.uptime_pct, 3)}</span>;
}

/** Secondary line explaining what the figure is based on. */
export function uptimeBasis(stats: UptimeStats): string {
  if (stats.uptime_pct === null) return "No observations in this window yet";
  if (!stats.sufficient) return `${pct(stats.uptime_pct)} of ${duration(stats.observed_seconds)} observed (${stats.coverage_pct < 0.01 ? "<0.01" : stats.coverage_pct.toFixed(2)}% of window)`;
  return `${stats.coverage_pct.toFixed(1)}% of window observed`;
}
