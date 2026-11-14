import type { ApiError, Category, Order, Product, ProductDetail, Promotion, Quote, ShippingMethod } from "./types";

/**
 * Server components call the backend directly; the browser goes through the
 * Next.js `/api/*` rewrite so there are no CORS concerns.
 */
const SERVER_BASE = process.env.BACKEND_URL ?? "http://127.0.0.1:8100";

export class ApiRequestError extends Error {
  constructor(
    public status: number,
    public error: ApiError,
    public requestId?: string,
  ) {
    super(error.message);
  }
}

export class BackendUnavailableError extends Error {}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const base = typeof window === "undefined" ? SERVER_BASE : "";
  let res: Response;
  try {
    res = await fetch(`${base}${path}`, {
      ...init,
      cache: "no-store",
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
  } catch {
    throw new BackendUnavailableError(`Could not reach the NovaMart API at ${base || "/api"}.`);
  }
  const text = await res.text();
  let body: unknown = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = null;
  }
  if (!res.ok) {
    const payload = body as { error?: ApiError; request_id?: string } | null;
    if (!payload?.error && res.status >= 500) {
      throw new BackendUnavailableError(`The NovaMart API responded with HTTP ${res.status}.`);
    }
    throw new ApiRequestError(
      res.status,
      payload?.error ?? { code: `HTTP_${res.status}`, component: "api", message: res.statusText || "Request failed" },
      payload?.request_id,
    );
  }
  return body as T;
}

export type ProductQuery = {
  q?: string;
  category?: string;
  sort?: string;
  in_stock?: boolean;
  featured?: boolean;
};

export function getProducts(query: ProductQuery = {}) {
  const params = new URLSearchParams();
  Object.entries(query).forEach(([k, v]) => {
    if (v !== undefined && v !== "" && v !== false) params.set(k, String(v));
  });
  const qs = params.toString();
  return request<{ count: number; products: Product[] }>(`/api/products${qs ? `?${qs}` : ""}`);
}

export const getProduct = (idOrSlug: string) => request<ProductDetail>(`/api/products/${encodeURIComponent(idOrSlug)}`);
export const getCategories = () => request<Category[]>("/api/categories");
export const getPromotions = () => request<Promotion[]>("/api/promotions");
export const getOrder = (orderId: string) => request<Order>(`/api/orders/${encodeURIComponent(orderId)}`);

export type CartPayload = {
  items: { product_id: string; quantity: number }[];
  promo_code?: string | null;
  shipping_method: ShippingMethod;
};

export const quoteCart = (payload: CartPayload) =>
  request<Quote>("/api/cart/quote", { method: "POST", body: JSON.stringify(payload) });

export type CheckoutPayload = CartPayload & {
  customer: { full_name: string; email: string; phone?: string };
  shipping_address: {
    line1: string;
    line2?: string;
    city: string;
    state: string;
    postal_code: string;
    country: string;
  };
};

export const placeOrder = (payload: CheckoutPayload) =>
  request<Order>("/api/checkout", { method: "POST", body: JSON.stringify(payload) });
