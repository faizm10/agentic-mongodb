"use client";

import { Minus, Plus } from "lucide-react";
import { MAX_QTY } from "@/lib/cart";

type Props = {
  value: number;
  onChange: (value: number) => void;
  max?: number;
  size?: "sm" | "md";
  label?: string;
};

export function QuantityStepper({ value, onChange, max = MAX_QTY, size = "md", label = "Quantity" }: Props) {
  const limit = Math.max(1, Math.min(MAX_QTY, max));
  const btn = size === "sm" ? "h-8 w-8" : "h-11 w-11";
  return (
    <div className="inline-flex items-center rounded-full border border-ink-200 bg-white" role="group" aria-label={label}>
      <button
        type="button"
        className={`${btn} flex items-center justify-center rounded-full text-ink-600 transition hover:bg-ink-50 disabled:opacity-30`}
        onClick={() => onChange(value - 1)}
        disabled={value <= 1}
        aria-label="Decrease quantity"
      >
        <Minus className="h-4 w-4" />
      </button>
      <span className={`${size === "sm" ? "w-7 text-sm" : "w-9"} text-center font-semibold tabular-nums`} aria-live="polite">
        {value}
      </span>
      <button
        type="button"
        className={`${btn} flex items-center justify-center rounded-full text-ink-600 transition hover:bg-ink-50 disabled:opacity-30`}
        onClick={() => onChange(value + 1)}
        disabled={value >= limit}
        aria-label="Increase quantity"
      >
        <Plus className="h-4 w-4" />
      </button>
    </div>
  );
}
