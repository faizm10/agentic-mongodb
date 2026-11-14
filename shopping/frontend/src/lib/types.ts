export type Product = {
  id: string;
  slug: string;
  name: string;
  category: string;
  tagline: string;
  description: string;
  price_cents: number;
  compare_at_cents: number | null;
  image: string;
  rating: number;
  review_count: number;
  features: string[];
  colors: string[];
  badge: string | null;
  featured: boolean;
  available: number;
  in_stock: boolean;
};

export type ProductDetail = Product & { related: Product[] };

export type Category = {
  slug: string;
  name: string;
  blurb: string;
  image: string;
  product_count: number;
};

export type Promotion = { code: string; description: string; min_subtotal_cents: number };

export type ShippingMethod = "standard" | "express";

export type ApiError = {
  code: string;
  component: string;
  message: string;
  details?: Record<string, unknown>;
  hint?: string;
};

export type StockIssue = {
  code: "INSUFFICIENT_STOCK";
  product_id: string;
  requested: number;
  available: number;
  message: string;
};

export type QuoteLine = {
  product_id: string;
  slug: string;
  name: string;
  image: string;
  unit_price_cents: number;
  quantity: number;
  line_total_cents: number;
  available: number;
};

export type Totals = {
  item_count: number;
  subtotal_cents: number;
  discount_cents: number;
  shipping_cents: number;
  tax_cents: number;
  total_cents: number;
  currency: string;
  shipping_method: ShippingMethod;
  promo_code: string | null;
};

export type Quote = Totals & {
  lines: QuoteLine[];
  promo: { code: string; description: string; applied: boolean; message: string | null } | null;
  promo_error: ApiError | null;
  free_shipping_threshold_cents: number;
  amount_to_free_shipping_cents: number;
  issues: StockIssue[];
  can_checkout: boolean;
};

export type Order = Totals & {
  order_id: string;
  status: string;
  created_at: string;
  customer: { full_name: string; email: string; phone: string | null };
  shipping_address: {
    line1: string;
    line2: string | null;
    city: string;
    state: string;
    postal_code: string;
    country: string;
  };
  items: Omit<QuoteLine, "available">[];
  estimated_delivery: { from: string; to: string };
  payment: { method: string; status: string; message: string };
};
