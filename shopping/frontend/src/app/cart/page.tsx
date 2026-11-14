"use client";

import Link from "next/link";
import { AlertTriangle, ArrowRight, Lock, ShoppingBag, Trash2 } from "lucide-react";
import { OrderSummary } from "@/components/OrderSummary";
import { ProductImage } from "@/components/ProductImage";
import { QuantityStepper } from "@/components/QuantityStepper";
import { useCart } from "@/lib/cart";
import { money } from "@/lib/format";
import { useQuote } from "@/lib/useQuote";

export default function CartPage() {
  const { lines, hydrated, setQuantity, removeItem } = useCart();
  const { quote, loading, error } = useQuote();

  if (!hydrated) {
    return <div className="container-page py-24"><div className="h-64 animate-pulse rounded-3xl bg-white" /></div>;
  }

  if (lines.length === 0) {
    return (
      <div className="container-page flex flex-col items-center py-28 text-center">
        <div className="flex h-20 w-20 items-center justify-center rounded-full bg-white shadow-soft">
          <ShoppingBag className="h-8 w-8 text-ink-400" />
        </div>
        <h1 className="mt-8 font-display text-4xl">Your cart is empty</h1>
        <p className="mt-3 text-ink-500">Discover something you&apos;ll love using every day.</p>
        <Link href="/products" className="btn-primary mt-8">
          Browse the collection <ArrowRight className="h-4 w-4" />
        </Link>
      </div>
    );
  }

  const issueFor = (id: string) => quote?.issues.find((i) => i.product_id === id);
  const lineFor = (id: string) => quote?.lines.find((l) => l.product_id === id);

  return (
    <div className="container-page pt-10 sm:pt-14">
      <h1 className="font-display text-4xl tracking-tight sm:text-5xl">Shopping cart</h1>
      <div className="mt-10 grid gap-10 lg:grid-cols-[1fr_400px]">
        <section aria-label="Cart items">
          <ul className="divide-y divide-ink-200 border-y border-ink-200">
            {lines.map((line) => {
              const issue = issueFor(line.productId);
              const priced = lineFor(line.productId);
              return (
                <li key={line.productId} className="flex gap-5 py-6">
                  <Link href={`/products/${line.slug}`} className="flex-none">
                    <ProductImage src={line.image} alt={line.name} width={260} height={320} className="h-32 w-[6.5rem] rounded-2xl bg-ink-100 sm:h-40 sm:w-32" />
                  </Link>
                  <div className="flex flex-1 flex-col">
                    <div className="flex justify-between gap-4">
                      <div>
                        <Link href={`/products/${line.slug}`} className="font-medium hover:text-nova-700">{line.name}</Link>
                        {line.color && <p className="mt-1 text-sm text-ink-400">{line.color}</p>}
                        <p className="mt-1 text-sm text-ink-500">{money(priced?.unit_price_cents ?? line.priceCents)} each</p>
                      </div>
                      <p className="font-semibold tabular-nums">{money(priced?.line_total_cents ?? line.priceCents * line.quantity)}</p>
                    </div>
                    {issue && (
                      <p className="mt-3 flex items-center gap-1.5 rounded-xl bg-amber-50 px-3 py-2 text-sm text-amber-800">
                        <AlertTriangle className="h-4 w-4 flex-none" /> {issue.message}
                      </p>
                    )}
                    <div className="mt-auto flex items-center justify-between pt-4">
                      <QuantityStepper value={line.quantity} onChange={(q) => setQuantity(line.productId, q)} size="sm" />
                      <button onClick={() => removeItem(line.productId)} className="flex items-center gap-1.5 text-sm text-ink-400 hover:text-rose-600">
                        <Trash2 className="h-4 w-4" /> Remove
                      </button>
                    </div>
                  </div>
                </li>
              );
            })}
          </ul>
          <Link href="/products" className="mt-6 inline-flex items-center gap-1 text-sm font-semibold text-ink-700 hover:text-nova-700">
            ← Continue shopping
          </Link>
        </section>

        <aside className="lg:sticky lg:top-28 lg:self-start">
          <OrderSummary quote={quote} loading={loading} error={error}>
            {quote && !quote.can_checkout ? (
              <button className="btn-primary w-full py-4" disabled>
                {quote.issues.length ? "Resolve stock issues to continue" : "Fix promo code to continue"}
              </button>
            ) : (
              <Link href="/checkout" className={`btn-primary w-full py-4 ${!quote || error ? "pointer-events-none opacity-50" : ""}`}>
                <Lock className="h-4 w-4" /> Secure checkout
              </Link>
            )}
          </OrderSummary>
        </aside>
      </div>
    </div>
  );
}
