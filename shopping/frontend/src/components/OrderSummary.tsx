"use client";

import { useState } from "react";
import { AlertTriangle, Loader2, Tag, X } from "lucide-react";
import { useCart } from "@/lib/cart";
import { money } from "@/lib/format";
import type { ApiError, Quote } from "@/lib/types";

function Row({ label, value, accent }: { label: React.ReactNode; value: React.ReactNode; accent?: boolean }) {
  return (
    <div className={`flex justify-between text-sm ${accent ? "font-medium text-emerald-700" : "text-ink-600"}`}>
      <span>{label}</span>
      <span className="tabular-nums">{value}</span>
    </div>
  );
}

export function PromoForm() {
  const { promoCode, setPromoCode } = useCart();
  const [value, setValue] = useState("");
  if (promoCode) {
    return (
      <div className="flex items-center justify-between rounded-xl border border-dashed border-nova-300 bg-nova-50 px-4 py-3 text-sm">
        <span className="flex items-center gap-2 font-mono font-semibold tracking-wider text-nova-800">
          <Tag className="h-4 w-4" /> {promoCode}
        </span>
        <button onClick={() => setPromoCode("")} className="text-nova-700 hover:text-nova-900" aria-label="Remove promo code">
          <X className="h-4 w-4" />
        </button>
      </div>
    );
  }
  // Not a <form>: this renders inside the checkout form, and forms cannot nest.
  const apply = () => {
    if (value.trim()) setPromoCode(value);
    setValue("");
  };
  return (
    <div className="flex gap-2">
      <input
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            apply();
          }
        }}
        placeholder="Promo code"
        aria-label="Promo code"
        className="field h-11 uppercase placeholder:normal-case"
      />
      <button type="button" onClick={apply} className="rounded-xl border border-ink-200 bg-white px-4 text-sm font-semibold hover:bg-ink-50">
        Apply
      </button>
    </div>
  );
}

export function OrderSummary({
  quote,
  loading,
  error,
  children,
}: {
  quote: Quote | null;
  loading: boolean;
  error: ApiError | null;
  children?: React.ReactNode;
}) {
  return (
    <div className="card p-6 sm:p-8">
      <div className="flex items-center justify-between">
        <h2 className="font-display text-2xl">Order summary</h2>
        {loading && <Loader2 className="h-4 w-4 animate-spin text-ink-400" aria-label="Updating totals" />}
      </div>

      <div className="mt-6">
        <PromoForm />
        {quote?.promo_error && (
          <p className="mt-2 flex items-start gap-1.5 text-sm text-rose-600">
            <AlertTriangle className="mt-0.5 h-4 w-4 flex-none" /> {quote.promo_error.message}
          </p>
        )}
        {quote?.promo && (
          <p className={`mt-2 text-sm ${quote.promo.applied ? "text-emerald-700" : "text-amber-700"}`}>{quote.promo.message}</p>
        )}
      </div>

      {error && (
        <div className="mt-6 rounded-2xl bg-rose-50 p-4 text-sm text-rose-700">
          <p className="font-semibold">We couldn&apos;t price your cart.</p>
          <p className="mt-1">{error.message}</p>
          <p className="mt-2 font-mono text-xs text-rose-500">{error.code}</p>
        </div>
      )}

      {quote && (
        <div className={`mt-6 space-y-3 border-t border-ink-100 pt-6 transition-opacity ${loading ? "opacity-60" : ""}`}>
          <Row label={`Subtotal (${quote.item_count} ${quote.item_count === 1 ? "item" : "items"})`} value={money(quote.subtotal_cents)} />
          {quote.discount_cents > 0 && <Row accent label={`Discount (${quote.promo_code})`} value={`−${money(quote.discount_cents)}`} />}
          <Row
            label={`Shipping (${quote.shipping_method === "express" ? "Express" : "Standard"})`}
            value={quote.shipping_cents === 0 ? <span className="font-medium text-emerald-700">Free</span> : money(quote.shipping_cents)}
          />
          <Row label="Estimated tax (8.25%)" value={money(quote.tax_cents)} />
          <div className="flex items-baseline justify-between border-t border-ink-100 pt-4">
            <span className="font-semibold">Total</span>
            <span className="font-display text-3xl tabular-nums">{money(quote.total_cents)}</span>
          </div>
          {quote.shipping_method === "standard" && quote.amount_to_free_shipping_cents > 0 && (
            <p className="rounded-xl bg-ink-50 px-3 py-2 text-xs text-ink-500">
              Add {money(quote.amount_to_free_shipping_cents)} more for free standard shipping.
            </p>
          )}
        </div>
      )}
      {children && <div className="mt-6">{children}</div>}
    </div>
  );
}
