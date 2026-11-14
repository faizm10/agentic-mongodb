"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import type { Product, ShippingMethod } from "./types";

export const MAX_QTY = 10;
const STORAGE_KEY = "novamart.cart.v1";

export type CartLine = {
  productId: string;
  slug: string;
  name: string;
  image: string;
  priceCents: number;
  quantity: number;
  color?: string;
};

type CartState = { lines: CartLine[]; promoCode: string; shippingMethod: ShippingMethod };

type CartContextValue = CartState & {
  hydrated: boolean;
  itemCount: number;
  drawerOpen: boolean;
  lastAdded: CartLine | null;
  addItem: (product: Product, quantity?: number, color?: string) => void;
  setQuantity: (productId: string, quantity: number) => void;
  removeItem: (productId: string) => void;
  clear: () => void;
  setPromoCode: (code: string) => void;
  setShippingMethod: (method: ShippingMethod) => void;
  openDrawer: () => void;
  closeDrawer: () => void;
};

const EMPTY: CartState = { lines: [], promoCode: "", shippingMethod: "standard" };
const CartContext = createContext<CartContextValue | null>(null);

function clampQty(n: number) {
  return Math.max(1, Math.min(MAX_QTY, Math.floor(n) || 1));
}

export function CartProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<CartState>(EMPTY);
  const [hydrated, setHydrated] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [lastAdded, setLastAdded] = useState<CartLine | null>(null);

  // Load the persisted cart once on the client.
  useEffect(() => {
    try {
      const raw = window.localStorage.getItem(STORAGE_KEY);
      if (raw) {
        const parsed = JSON.parse(raw) as Partial<CartState>;
        setState({
          lines: Array.isArray(parsed.lines) ? parsed.lines.filter((l) => l && l.productId) : [],
          promoCode: typeof parsed.promoCode === "string" ? parsed.promoCode : "",
          shippingMethod: parsed.shippingMethod === "express" ? "express" : "standard",
        });
      }
    } catch {
      /* corrupted storage: start with an empty cart */
    }
    setHydrated(true);
  }, []);

  useEffect(() => {
    if (!hydrated) return;
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    } catch {
      /* storage unavailable (private mode); cart still works in-memory */
    }
  }, [state, hydrated]);

  // Keep multiple tabs in sync.
  useEffect(() => {
    const onStorage = (e: StorageEvent) => {
      if (e.key === STORAGE_KEY && e.newValue) {
        try {
          setState(JSON.parse(e.newValue));
        } catch {
          /* ignore */
        }
      }
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  const addItem = useCallback((product: Product, quantity = 1, color?: string) => {
    const line: CartLine = {
      productId: product.id,
      slug: product.slug,
      name: product.name,
      image: product.image,
      priceCents: product.price_cents,
      quantity: clampQty(quantity),
      color,
    };
    setState((s) => {
      const existing = s.lines.find((l) => l.productId === product.id);
      const lines = existing
        ? s.lines.map((l) => (l.productId === product.id ? { ...l, quantity: clampQty(l.quantity + quantity), color: color ?? l.color } : l))
        : [...s.lines, line];
      return { ...s, lines };
    });
    setLastAdded(line);
    setDrawerOpen(true);
  }, []);

  const setQuantity = useCallback((productId: string, quantity: number) => {
    setState((s) => ({ ...s, lines: s.lines.map((l) => (l.productId === productId ? { ...l, quantity: clampQty(quantity) } : l)) }));
  }, []);

  const removeItem = useCallback((productId: string) => {
    setState((s) => ({ ...s, lines: s.lines.filter((l) => l.productId !== productId) }));
  }, []);

  const value = useMemo<CartContextValue>(
    () => ({
      ...state,
      hydrated,
      drawerOpen,
      lastAdded,
      itemCount: state.lines.reduce((n, l) => n + l.quantity, 0),
      addItem,
      setQuantity,
      removeItem,
      clear: () => setState((s) => ({ ...EMPTY, shippingMethod: s.shippingMethod })),
      setPromoCode: (promoCode) => setState((s) => ({ ...s, promoCode: promoCode.trim().toUpperCase() })),
      setShippingMethod: (shippingMethod) => setState((s) => ({ ...s, shippingMethod })),
      openDrawer: () => setDrawerOpen(true),
      closeDrawer: () => setDrawerOpen(false),
    }),
    [state, hydrated, drawerOpen, lastAdded, addItem, setQuantity, removeItem],
  );

  return <CartContext.Provider value={value}>{children}</CartContext.Provider>;
}

export function useCart() {
  const ctx = useContext(CartContext);
  if (!ctx) throw new Error("useCart must be used inside <CartProvider>");
  return ctx;
}
