"use client";

import { useState } from "react";
import { Check, ShoppingBag } from "lucide-react";
import { MAX_QTY, useCart } from "@/lib/cart";
import type { Product } from "@/lib/types";
import { QuantityStepper } from "./QuantityStepper";
import { StockNote } from "./ui";

const SWATCHES: Record<string, string> = {
  Onyx: "#1d1d1f", Sand: "#d8c8ad", Sky: "#a9c6e8", Silver: "#c9cbcf", Copper: "#b8734a", Graphite: "#45474d",
  Cloud: "#eceff3", Midnight: "#1e2433", Titanium: "#9a9690", Chalk: "#f2efe8", Slate: "#5b6470", Arctic: "#f4f6f8",
  "Space Grey": "#6b6d72", Bone: "#e8e1d3", White: "#fafafa", Charcoal: "#3a3a3c", Sage: "#a7b59c", Porcelain: "#f5f3ef",
  Oat: "#d9cbb3", Mint: "#bfe3d6", Blush: "#f1c9c3", Stone: "#b9b3a8", Fog: "#c8c6c1", Amber: "#c8862f", Black: "#111111",
  Navy: "#1f2a44", Olive: "#6b6b3a", Natural: "#c9a77c", Fern: "#5d7b63", Snow: "#f5f5f5", Ink: "#22263a", Carbon: "#2b2b2b",
  Ash: "#bdbdbd", Heather: "#9b9b9f",
};

export function AddToCartPanel({ product }: { product: Product }) {
  const { addItem, lines } = useCart();
  const [qty, setQty] = useState(1);
  const [color, setColor] = useState(product.colors[0]);
  const [justAdded, setJustAdded] = useState(false);
  const inCart = lines.find((l) => l.productId === product.id)?.quantity ?? 0;
  const maxAddable = Math.max(0, Math.min(MAX_QTY - inCart, product.available - inCart));

  return (
    <div className="space-y-6">
      {product.colors.length > 0 && (
        <div>
          <p className="text-sm font-medium text-ink-700">
            Color: <span className="text-ink-500">{color}</span>
          </p>
          <div className="mt-3 flex flex-wrap gap-3">
            {product.colors.map((c) => (
              <button
                key={c}
                onClick={() => setColor(c)}
                aria-label={c}
                aria-pressed={color === c}
                className={`h-9 w-9 rounded-full border border-ink-200 ring-2 ring-offset-2 ring-offset-ink-50 transition ${color === c ? "ring-ink-900" : "ring-transparent hover:ring-ink-300"}`}
                style={{ backgroundColor: SWATCHES[c] ?? "#ddd" }}
              />
            ))}
          </div>
        </div>
      )}

      <StockNote available={product.available} />

      {product.in_stock ? (
        <div className="flex flex-wrap gap-3">
          <QuantityStepper value={qty} onChange={(v) => setQty(Math.min(v, Math.max(1, maxAddable)))} max={Math.max(1, maxAddable)} />
          <button
            className="btn-primary min-w-48 flex-1 py-4"
            disabled={maxAddable <= 0}
            onClick={() => {
              addItem(product, Math.min(qty, maxAddable), color);
              setJustAdded(true);
              setQty(1);
              window.setTimeout(() => setJustAdded(false), 1800);
            }}
          >
            {justAdded ? <Check className="h-4 w-4" /> : <ShoppingBag className="h-4 w-4" />}
            {maxAddable <= 0 ? "Maximum in cart" : justAdded ? "Added to cart" : "Add to cart"}
          </button>
        </div>
      ) : (
        <button className="btn-secondary w-full py-4" disabled>
          Sold out, back soon
        </button>
      )}
      {inCart > 0 && <p className="text-sm text-ink-500">You have {inCart} in your cart.</p>}
    </div>
  );
}
