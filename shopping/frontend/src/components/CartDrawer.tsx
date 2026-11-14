"use client";

import Link from "next/link";
import { useEffect } from "react";
import { CheckCircle2, ShoppingBag, Trash2, X } from "lucide-react";
import { useCart } from "@/lib/cart";
import { money } from "@/lib/format";
import { ProductImage } from "./ProductImage";
import { QuantityStepper } from "./QuantityStepper";

const FREE_SHIPPING_CENTS = 7500;

export function CartDrawer() {
  const { drawerOpen, closeDrawer, lines, setQuantity, removeItem, lastAdded, itemCount } = useCart();
  const estimate = lines.reduce((sum, l) => sum + l.priceCents * l.quantity, 0);
  const progress = Math.min(100, (estimate / FREE_SHIPPING_CENTS) * 100);

  useEffect(() => {
    if (!drawerOpen) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && closeDrawer();
    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [drawerOpen, closeDrawer]);

  if (!drawerOpen) return null;

  return (
    <div className="fixed inset-0 z-50" role="dialog" aria-modal="true" aria-label="Shopping cart">
      <button className="absolute inset-0 bg-ink-900/40 backdrop-blur-sm" onClick={closeDrawer} aria-label="Close cart" />
      <aside className="absolute right-0 top-0 flex h-full w-full max-w-md animate-slide-in flex-col bg-white shadow-lift">
        <div className="flex items-center justify-between border-b border-ink-100 px-6 py-5">
          <h2 className="font-display text-2xl">Your cart <span className="text-ink-400">({itemCount})</span></h2>
          <button onClick={closeDrawer} className="rounded-full p-2 hover:bg-ink-50" aria-label="Close cart">
            <X className="h-5 w-5" />
          </button>
        </div>

        {lastAdded && lines.some((l) => l.productId === lastAdded.productId) && (
          <div className="mx-6 mt-4 flex items-center gap-2 rounded-2xl bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
            <CheckCircle2 className="h-4 w-4 flex-none" /> Added <strong className="truncate">{lastAdded.name}</strong>
          </div>
        )}

        {lines.length === 0 ? (
          <div className="flex flex-1 flex-col items-center justify-center gap-4 px-6 text-center">
            <div className="flex h-16 w-16 items-center justify-center rounded-full bg-ink-50">
              <ShoppingBag className="h-7 w-7 text-ink-400" />
            </div>
            <p className="text-ink-500">Your cart is empty.</p>
            <Link href="/products" onClick={closeDrawer} className="rounded-full bg-ink-900 px-6 py-3 text-sm font-semibold text-white">
              Start shopping
            </Link>
          </div>
        ) : (
          <>
            <div className="px-6 pt-4">
              <p className="text-sm text-ink-600">
                {estimate >= FREE_SHIPPING_CENTS ? (
                  <>🎉 You&apos;ve unlocked <strong>free shipping</strong>.</>
                ) : (
                  <>Add <strong>{money(FREE_SHIPPING_CENTS - estimate)}</strong> more for free shipping.</>
                )}
              </p>
              <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-ink-100">
                <div className="h-full rounded-full bg-gradient-to-r from-nova-500 to-ember-500 transition-all duration-500" style={{ width: `${progress}%` }} />
              </div>
            </div>
            <ul className="flex-1 divide-y divide-ink-100 overflow-y-auto px-6 py-2">
              {lines.map((line) => (
                <li key={line.productId} className="flex gap-4 py-4">
                  <ProductImage src={line.image} alt={line.name} width={160} height={200} className="h-24 w-20 flex-none rounded-2xl bg-ink-100" />
                  <div className="flex flex-1 flex-col">
                    <div className="flex justify-between gap-3">
                      <Link href={`/products/${line.slug}`} onClick={closeDrawer} className="text-sm font-medium leading-snug hover:text-nova-700">
                        {line.name}
                      </Link>
                      <button onClick={() => removeItem(line.productId)} className="text-ink-400 hover:text-rose-600" aria-label={`Remove ${line.name}`}>
                        <Trash2 className="h-4 w-4" />
                      </button>
                    </div>
                    {line.color && <p className="mt-0.5 text-xs text-ink-400">{line.color}</p>}
                    <div className="mt-auto flex items-center justify-between pt-2">
                      <QuantityStepper size="sm" value={line.quantity} onChange={(q) => setQuantity(line.productId, q)} />
                      <span className="text-sm font-semibold">{money(line.priceCents * line.quantity)}</span>
                    </div>
                  </div>
                </li>
              ))}
            </ul>
            <div className="border-t border-ink-100 px-6 py-5">
              <div className="flex justify-between text-sm">
                <span className="text-ink-500">Estimated subtotal</span>
                <span className="font-semibold">{money(estimate)}</span>
              </div>
              <p className="mt-1 text-xs text-ink-400">Discounts, shipping and tax are calculated at checkout.</p>
              <div className="mt-4 grid grid-cols-2 gap-3">
                <Link href="/cart" onClick={closeDrawer} className="rounded-full border border-ink-200 py-3 text-center text-sm font-semibold hover:bg-ink-50">
                  View cart
                </Link>
                <Link href="/checkout" onClick={closeDrawer} className="rounded-full bg-ink-900 py-3 text-center text-sm font-semibold text-white hover:bg-nova-700">
                  Checkout
                </Link>
              </div>
            </div>
          </>
        )}
      </aside>
    </div>
  );
}
