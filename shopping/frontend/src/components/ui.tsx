import { Star } from "lucide-react";
import { money } from "@/lib/format";

export function Rating({ value, count, size = 14 }: { value: number; count?: number; size?: number }) {
  return (
    <div className="flex items-center gap-1.5 text-sm text-ink-500">
      <div className="flex" aria-label={`Rated ${value} out of 5`}>
        {[1, 2, 3, 4, 5].map((i) => (
          <Star
            key={i}
            width={size}
            height={size}
            className={i <= Math.round(value) ? "fill-amber-400 text-amber-400" : "fill-ink-200 text-ink-200"}
          />
        ))}
      </div>
      <span className="font-medium text-ink-700">{value.toFixed(1)}</span>
      {count !== undefined && <span>({count.toLocaleString()})</span>}
    </div>
  );
}

export function Price({ cents, compareAt, className = "" }: { cents: number; compareAt?: number | null; className?: string }) {
  return (
    <div className={`flex items-baseline gap-2 ${className}`}>
      <span className="font-semibold text-ink-900">{money(cents)}</span>
      {compareAt && compareAt > cents && <span className="text-sm text-ink-400 line-through">{money(compareAt)}</span>}
    </div>
  );
}

const BADGE_STYLES: Record<string, string> = {
  Bestseller: "bg-ink-900 text-white",
  New: "bg-nova-600 text-white",
  Limited: "bg-ember-500 text-white",
  "Low stock": "bg-amber-100 text-amber-900",
  "Sold out": "bg-white/90 text-ink-700 ring-1 ring-ink-200",
};

export function Badge({ label }: { label: string }) {
  return (
    <span className={`inline-flex items-center rounded-full px-2.5 py-1 text-[11px] font-semibold uppercase tracking-wide ${BADGE_STYLES[label] ?? "bg-ink-100 text-ink-700"}`}>
      {label}
    </span>
  );
}

export function StockNote({ available }: { available: number }) {
  if (available <= 0) return <p className="text-sm font-medium text-rose-600">Sold out</p>;
  if (available <= 5)
    return (
      <p className="flex items-center gap-2 text-sm font-medium text-amber-700">
        <span className="h-2 w-2 animate-pulse rounded-full bg-amber-500" /> Only {available} left in stock
      </p>
    );
  return (
    <p className="flex items-center gap-2 text-sm font-medium text-emerald-700">
      <span className="h-2 w-2 rounded-full bg-emerald-500" /> In stock, ready to ship
    </p>
  );
}

export function SectionHeading({ eyebrow, title, children }: { eyebrow?: string; title: string; children?: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
      <div>
        {eyebrow && <p className="mb-2 text-xs font-semibold uppercase tracking-[0.2em] text-nova-600">{eyebrow}</p>}
        <h2 className="font-display text-3xl tracking-tight text-ink-900 sm:text-4xl">{title}</h2>
      </div>
      {children}
    </div>
  );
}
