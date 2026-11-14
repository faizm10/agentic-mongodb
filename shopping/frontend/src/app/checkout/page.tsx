"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { AlertTriangle, Info, Loader2, Lock, Sparkles, Truck, Zap } from "lucide-react";
import { OrderSummary } from "@/components/OrderSummary";
import { ProductImage } from "@/components/ProductImage";
import { ApiRequestError, placeOrder } from "@/lib/api";
import { useCart } from "@/lib/cart";
import { money } from "@/lib/format";
import type { ApiError, ShippingMethod } from "@/lib/types";
import { useQuote } from "@/lib/useQuote";

type Form = {
  full_name: string;
  email: string;
  phone: string;
  line1: string;
  line2: string;
  city: string;
  state: string;
  postal_code: string;
  country: string;
};

const EMPTY_FORM: Form = { full_name: "", email: "", phone: "", line1: "", line2: "", city: "", state: "", postal_code: "", country: "US" };
const DEMO_FORM: Form = {
  full_name: "Jordan Rivera",
  email: "jordan.rivera@example.com",
  phone: "+1 (415) 555-0142",
  line1: "500 Market Street",
  line2: "Apt 12B",
  city: "San Francisco",
  state: "CA",
  postal_code: "94105",
  country: "US",
};

// Maps API field paths (e.g. "customer.email") to form keys.
const FIELD_MAP: Record<string, keyof Form> = {
  "customer.full_name": "full_name",
  "customer.email": "email",
  "customer.phone": "phone",
  "shipping_address.line1": "line1",
  "shipping_address.line2": "line2",
  "shipping_address.city": "city",
  "shipping_address.state": "state",
  "shipping_address.postal_code": "postal_code",
  "shipping_address.country": "country",
};

function validate(f: Form): Partial<Record<keyof Form, string>> {
  const errors: Partial<Record<keyof Form, string>> = {};
  if (f.full_name.trim().length < 2) errors.full_name = "Enter your full name.";
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(f.email.trim())) errors.email = "Enter a valid email address.";
  if (f.line1.trim().length < 3) errors.line1 = "Enter a street address.";
  if (f.city.trim().length < 2) errors.city = "Enter a city.";
  if (f.state.trim().length < 2) errors.state = "Enter a state.";
  if (f.postal_code.trim().length < 3) errors.postal_code = "Enter a postal code.";
  return errors;
}

function Field({
  label,
  name,
  form,
  errors,
  onChange,
  optional,
  className = "",
  ...rest
}: {
  label: string;
  name: keyof Form;
  form: Form;
  errors: Partial<Record<keyof Form, string>>;
  onChange: (name: keyof Form, value: string) => void;
  optional?: boolean;
  className?: string;
} & Omit<React.InputHTMLAttributes<HTMLInputElement>, "form" | "onChange" | "name" | "value">) {
  const error = errors[name];
  return (
    <label className={`block ${className}`}>
      <span className="mb-1.5 flex justify-between text-sm font-medium text-ink-700">
        {label} {optional && <span className="font-normal text-ink-400">Optional</span>}
      </span>
      <input
        {...rest}
        name={name}
        value={form[name]}
        onChange={(e) => onChange(name, e.target.value)}
        aria-invalid={Boolean(error)}
        className={`field ${error ? "field-error" : ""}`}
      />
      {error && <span className="mt-1 block text-xs text-rose-600">{error}</span>}
    </label>
  );
}

export default function CheckoutPage() {
  const router = useRouter();
  const { lines, hydrated, promoCode, shippingMethod, setShippingMethod, clear } = useCart();
  const { quote, loading, error: quoteError } = useQuote();
  const [form, setForm] = useState<Form>(EMPTY_FORM);
  const [errors, setErrors] = useState<Partial<Record<keyof Form, string>>>({});
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<(ApiError & { requestId?: string }) | null>(null);
  const [placedId, setPlacedId] = useState<string | null>(null);

  const onChange = (name: keyof Form, value: string) => {
    setForm((f) => ({ ...f, [name]: value }));
    if (errors[name]) setErrors((e) => ({ ...e, [name]: undefined }));
  };

  if (!hydrated) return <div className="container-page py-24"><div className="h-96 animate-pulse rounded-3xl bg-white" /></div>;

  if (placedId) {
    return (
      <div className="container-page flex flex-col items-center py-28 text-center">
        <Loader2 className="h-8 w-8 animate-spin text-nova-600" />
        <p className="mt-4 text-ink-500">Order {placedId} confirmed. Loading your receipt…</p>
      </div>
    );
  }

  if (lines.length === 0) {
    return (
      <div className="container-page py-28 text-center">
        <h1 className="font-display text-4xl">Nothing to check out yet</h1>
        <p className="mt-3 text-ink-500">Your cart is empty.</p>
        <Link href="/products" className="btn-primary mt-8">Browse products</Link>
      </div>
    );
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitError(null);
    const v = validate(form);
    setErrors(v);
    if (Object.keys(v).length) return;
    setSubmitting(true);
    try {
      const order = await placeOrder({
        items: lines.map((l) => ({ product_id: l.productId, quantity: l.quantity })),
        promo_code: promoCode || null,
        shipping_method: shippingMethod,
        customer: { full_name: form.full_name, email: form.email, phone: form.phone || undefined },
        shipping_address: {
          line1: form.line1,
          line2: form.line2 || undefined,
          city: form.city,
          state: form.state,
          postal_code: form.postal_code,
          country: form.country,
        },
      });
      setPlacedId(order.order_id);
      clear();
      router.push(`/orders/${order.order_id}?placed=1`);
    } catch (err) {
      if (err instanceof ApiRequestError) {
        const fields = (err.error.details?.fields as { field: string; message: string }[] | undefined) ?? [];
        const mapped: Partial<Record<keyof Form, string>> = {};
        fields.forEach((f) => {
          const key = FIELD_MAP[f.field];
          if (key) mapped[key] = f.message;
        });
        setErrors(mapped);
        setSubmitError({ ...err.error, requestId: err.requestId });
      } else {
        setSubmitError({ code: "BACKEND_UNAVAILABLE", component: "frontend", message: (err as Error).message });
      }
      setSubmitting(false);
    }
  }

  const blocked = !quote || !quote.can_checkout || Boolean(quoteError);

  return (
    <div className="container-page pt-10 sm:pt-14">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-display text-4xl tracking-tight sm:text-5xl">Checkout</h1>
          <p className="mt-2 flex items-center gap-1.5 text-sm text-ink-500"><Lock className="h-3.5 w-3.5" /> Simulated, secure demo checkout</p>
        </div>
        <button type="button" onClick={() => { setForm(DEMO_FORM); setErrors({}); }} className="inline-flex items-center gap-2 rounded-full border border-dashed border-nova-300 bg-nova-50 px-4 py-2 text-sm font-semibold text-nova-700 hover:bg-nova-100">
          <Sparkles className="h-4 w-4" /> Fill demo details
        </button>
      </div>

      <form onSubmit={submit} noValidate className="mt-10 grid gap-10 lg:grid-cols-[1fr_420px]">
        <div className="space-y-8">
          <section className="card p-6 sm:p-8">
            <h2 className="font-display text-2xl">Contact</h2>
            <div className="mt-6 grid gap-5 sm:grid-cols-2">
              <Field label="Full name" name="full_name" form={form} errors={errors} onChange={onChange} autoComplete="name" className="sm:col-span-2" />
              <Field label="Email" name="email" type="email" form={form} errors={errors} onChange={onChange} autoComplete="email" />
              <Field label="Phone" name="phone" type="tel" optional form={form} errors={errors} onChange={onChange} autoComplete="tel" />
            </div>
          </section>

          <section className="card p-6 sm:p-8">
            <h2 className="font-display text-2xl">Shipping address</h2>
            <div className="mt-6 grid gap-5 sm:grid-cols-6">
              <Field label="Street address" name="line1" form={form} errors={errors} onChange={onChange} autoComplete="address-line1" className="sm:col-span-6" />
              <Field label="Apartment, suite, etc." name="line2" optional form={form} errors={errors} onChange={onChange} autoComplete="address-line2" className="sm:col-span-6" />
              <Field label="City" name="city" form={form} errors={errors} onChange={onChange} autoComplete="address-level2" className="sm:col-span-3" />
              <Field label="State" name="state" form={form} errors={errors} onChange={onChange} autoComplete="address-level1" className="sm:col-span-1" />
              <Field label="ZIP code" name="postal_code" form={form} errors={errors} onChange={onChange} autoComplete="postal-code" className="sm:col-span-2" />
            </div>
          </section>

          <section className="card p-6 sm:p-8">
            <h2 className="font-display text-2xl">Delivery</h2>
            <div className="mt-6 grid gap-3 sm:grid-cols-2">
              {([
                ["standard", Truck, "Standard", "4–7 business days", quote && quote.shipping_method === "standard" && quote.shipping_cents === 0 ? "Free" : money(595)],
                ["express", Zap, "Express", "1–2 business days", money(1995)],
              ] as [ShippingMethod, typeof Truck, string, string, string][]).map(([value, Icon, title, eta, price]) => (
                <label
                  key={value}
                  className={`flex cursor-pointer items-start gap-4 rounded-2xl border p-4 transition ${shippingMethod === value ? "border-ink-900 bg-ink-50 ring-1 ring-ink-900" : "border-ink-200 hover:border-ink-300"}`}
                >
                  <input type="radio" name="shipping" value={value} checked={shippingMethod === value} onChange={() => setShippingMethod(value)} className="sr-only" />
                  <Icon className="mt-0.5 h-5 w-5 text-nova-600" />
                  <span className="flex-1">
                    <span className="block font-semibold">{title}</span>
                    <span className="text-sm text-ink-500">{eta}</span>
                  </span>
                  <span className="text-sm font-semibold">{price}</span>
                </label>
              ))}
            </div>
          </section>

          <section className="card p-6 sm:p-8">
            <h2 className="font-display text-2xl">Payment</h2>
            <div className="mt-6 flex items-start gap-3 rounded-2xl bg-nova-50 p-4 text-sm text-nova-900">
              <Info className="mt-0.5 h-5 w-5 flex-none text-nova-600" />
              <p>
                <strong>This is a simulated checkout.</strong> NovaMart is a demo store: no card details are collected and no
                payment will be processed. Placing an order creates a demo order record only.
              </p>
            </div>
          </section>
        </div>

        <aside className="space-y-4 lg:sticky lg:top-28 lg:self-start">
          <OrderSummary quote={quote} loading={loading} error={quoteError}>
            <ul className="mb-6 space-y-3 border-b border-ink-100 pb-6">
              {lines.map((l) => {
                const issue = quote?.issues.find((i) => i.product_id === l.productId);
                return (
                  <li key={l.productId} className="flex items-center gap-3">
                    <div className="relative">
                      <ProductImage src={l.image} alt={l.name} width={120} height={120} className="h-14 w-14 rounded-xl bg-ink-100" />
                      <span className="absolute -right-1.5 -top-1.5 flex h-5 min-w-5 items-center justify-center rounded-full bg-ink-900 px-1 text-[11px] font-bold text-white">{l.quantity}</span>
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium">{l.name}</p>
                      {issue && <p className="text-xs text-amber-700">{issue.message}</p>}
                    </div>
                  </li>
                );
              })}
            </ul>
            {submitError && (
              <div role="alert" className="mb-4 rounded-2xl bg-rose-50 p-4 text-sm text-rose-800">
                <p className="flex items-center gap-1.5 font-semibold"><AlertTriangle className="h-4 w-4" /> Order not placed</p>
                <p className="mt-1">{submitError.message}</p>
                {submitError.hint && <p className="mt-1 text-rose-700">{submitError.hint}</p>}
                <p className="mt-2 font-mono text-[11px] text-rose-500">
                  {submitError.code}{submitError.requestId ? ` · request ${submitError.requestId}` : ""}
                </p>
              </div>
            )}
            <button type="submit" className="btn-primary w-full py-4 text-base" disabled={submitting || blocked || loading}>
              {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Lock className="h-4 w-4" />}
              {submitting ? "Placing order…" : quote ? `Place order · ${money(quote.total_cents)}` : "Place order"}
            </button>
            {quote && !quote.can_checkout && (
              <p className="mt-3 text-center text-xs text-amber-700">
                {quote.issues.length ? "Some items exceed available stock. " : "Your promo code is invalid. "}
                <Link href="/cart" className="font-semibold underline">Review cart</Link>
              </p>
            )}
            <p className="mt-3 text-center text-xs text-ink-400">No payment will be taken. Demo order only.</p>
          </OrderSummary>
        </aside>
      </form>
    </div>
  );
}
