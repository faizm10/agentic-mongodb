"use client";

import { ShoppingBag } from "lucide-react";
import { useCart } from "@/lib/cart";
import type { Product } from "@/lib/types";

export function QuickAdd({ product }: { product: Product }) {
  const { addItem } = useCart();
  if (!product.in_stock) {
    return (
      <div className="w-full rounded-full bg-white/90 py-2.5 text-center text-sm font-medium text-ink-500 backdrop-blur">
        Sold out
      </div>
    );
  }
  return (
    <button
      type="button"
      onClick={() => addItem(product, 1, product.colors[0])}
      className="flex w-full items-center justify-center gap-2 rounded-full bg-white/95 py-2.5 text-sm font-semibold text-ink-900 shadow-soft backdrop-blur transition hover:bg-ink-900 hover:text-white"
    >
      <ShoppingBag className="h-4 w-4" /> Quick add
    </button>
  );
}
