"use client";

import { useEffect, useRef, useState } from "react";
import { ApiRequestError, quoteCart } from "./api";
import { useCart } from "./cart";
import type { ApiError, Quote } from "./types";

/** Server-authoritative pricing for the current cart (debounced). */
export function useQuote() {
  const { lines, promoCode, shippingMethod, hydrated } = useCart();
  const [quote, setQuote] = useState<Quote | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const seq = useRef(0);

  const key = JSON.stringify([lines.map((l) => [l.productId, l.quantity]), promoCode, shippingMethod]);

  useEffect(() => {
    if (!hydrated) return;
    if (lines.length === 0) {
      setQuote(null);
      setError(null);
      return;
    }
    const id = ++seq.current;
    setLoading(true);
    const timer = window.setTimeout(async () => {
      try {
        const q = await quoteCart({
          items: lines.map((l) => ({ product_id: l.productId, quantity: l.quantity })),
          promo_code: promoCode || null,
          shipping_method: shippingMethod,
        });
        if (id === seq.current) {
          setQuote(q);
          setError(null);
        }
      } catch (e) {
        if (id === seq.current) {
          setError(
            e instanceof ApiRequestError
              ? e.error
              : { code: "BACKEND_UNAVAILABLE", component: "frontend", message: (e as Error).message },
          );
        }
      } finally {
        if (id === seq.current) setLoading(false);
      }
    }, 180);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, hydrated]);

  return { quote, loading, error };
}
