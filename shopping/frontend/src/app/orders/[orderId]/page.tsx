import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { CalendarDays, CheckCircle2, Mail, MapPin, ShieldCheck } from "lucide-react";
import { BackendOffline } from "@/components/BackendOffline";
import { ProductImage } from "@/components/ProductImage";
import { ApiRequestError, getOrder } from "@/lib/api";
import { formatDate, money } from "@/lib/format";
import type { Order } from "@/lib/types";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Order confirmation" };

type Props = { params: Promise<{ orderId: string }>; searchParams: Promise<{ placed?: string }> };

export default async function OrderPage({ params, searchParams }: Props) {
  const { orderId } = await params;
  const { placed } = await searchParams;
  let order: Order;
  try {
    order = await getOrder(orderId);
  } catch (e) {
    if (e instanceof ApiRequestError && e.status === 404) notFound();
    return <BackendOffline message={(e as Error).message} />;
  }
  const firstName = order.customer.full_name.split(" ")[0];
  const a = order.shipping_address;

  return (
    <div className="container-page max-w-4xl pt-12 sm:pt-16">
      <div className="text-center">
        <div className="mx-auto flex h-16 w-16 animate-pop items-center justify-center rounded-full bg-emerald-100">
          <CheckCircle2 className="h-9 w-9 text-emerald-600" />
        </div>
        <p className="mt-6 text-xs font-semibold uppercase tracking-[0.2em] text-nova-600">
          {placed ? "Order confirmed" : "Order details"}
        </p>
        <h1 className="mt-3 font-display text-4xl tracking-tight sm:text-5xl">
          {placed ? <>Thank you, {firstName}!</> : <>Order {order.order_id}</>}
        </h1>
        <p className="mx-auto mt-4 max-w-lg text-ink-500">
          {placed ? (
            <>
              Your order <span className="font-mono font-semibold text-ink-800">{order.order_id}</span> has been placed. A
              confirmation would be sent to <span className="font-medium text-ink-800">{order.customer.email}</span>.
            </>
          ) : (
            <>Placed on {new Date(order.created_at).toLocaleString("en-US", { dateStyle: "long", timeStyle: "short" })}</>
          )}
        </p>
      </div>

      <div className="mt-12 grid gap-4 sm:grid-cols-3">
        <div className="card p-5">
          <CalendarDays className="h-5 w-5 text-nova-600" />
          <p className="mt-3 text-xs uppercase tracking-wider text-ink-400">Estimated delivery</p>
          <p className="mt-1 font-semibold">
            {formatDate(order.estimated_delivery.from)} – {formatDate(order.estimated_delivery.to)}
          </p>
          <p className="text-sm capitalize text-ink-500">{order.shipping_method} shipping</p>
        </div>
        <div className="card p-5">
          <MapPin className="h-5 w-5 text-nova-600" />
          <p className="mt-3 text-xs uppercase tracking-wider text-ink-400">Shipping to</p>
          <p className="mt-1 font-semibold">{order.customer.full_name}</p>
          <p className="text-sm text-ink-500">
            {a.line1}
            {a.line2 ? `, ${a.line2}` : ""}
            <br />
            {a.city}, {a.state} {a.postal_code}
          </p>
        </div>
        <div className="card p-5">
          <Mail className="h-5 w-5 text-nova-600" />
          <p className="mt-3 text-xs uppercase tracking-wider text-ink-400">Contact</p>
          <p className="mt-1 break-all font-semibold">{order.customer.email}</p>
          {order.customer.phone && <p className="text-sm text-ink-500">{order.customer.phone}</p>}
        </div>
      </div>

      <div className="card mt-6 overflow-hidden">
        <div className="flex items-center justify-between border-b border-ink-100 px-6 py-4 sm:px-8">
          <h2 className="font-display text-xl">Items</h2>
          <span className="rounded-full bg-emerald-50 px-3 py-1 text-xs font-semibold capitalize text-emerald-700">{order.status}</span>
        </div>
        <ul className="divide-y divide-ink-100 px-6 sm:px-8">
          {order.items.map((item) => (
            <li key={item.product_id} className="flex items-center gap-4 py-4">
              <ProductImage src={item.image} alt={item.name} width={140} height={140} className="h-16 w-16 rounded-2xl bg-ink-100" />
              <div className="flex-1">
                <Link href={`/products/${item.slug}`} className="font-medium hover:text-nova-700">{item.name}</Link>
                <p className="text-sm text-ink-500">
                  {item.quantity} × {money(item.unit_price_cents)}
                </p>
              </div>
              <p className="font-semibold tabular-nums">{money(item.line_total_cents)}</p>
            </li>
          ))}
        </ul>
        <dl className="space-y-2 border-t border-ink-100 bg-ink-50/50 px-6 py-6 text-sm sm:px-8">
          <div className="flex justify-between text-ink-600"><dt>Subtotal</dt><dd className="tabular-nums">{money(order.subtotal_cents)}</dd></div>
          {order.discount_cents > 0 && (
            <div className="flex justify-between font-medium text-emerald-700"><dt>Discount ({order.promo_code})</dt><dd className="tabular-nums">−{money(order.discount_cents)}</dd></div>
          )}
          <div className="flex justify-between text-ink-600"><dt>Shipping</dt><dd className="tabular-nums">{order.shipping_cents ? money(order.shipping_cents) : "Free"}</dd></div>
          <div className="flex justify-between text-ink-600"><dt>Tax</dt><dd className="tabular-nums">{money(order.tax_cents)}</dd></div>
          <div className="flex items-baseline justify-between border-t border-ink-200 pt-3">
            <dt className="font-semibold">Total</dt>
            <dd className="font-display text-2xl tabular-nums">{money(order.total_cents)}</dd>
          </div>
        </dl>
      </div>

      <p className="mt-6 flex items-start justify-center gap-2 text-center text-sm text-ink-500">
        <ShieldCheck className="mt-0.5 h-4 w-4 flex-none text-nova-600" /> {order.payment.message}
      </p>

      <div className="mt-10 flex justify-center gap-3">
        <Link href="/products" className="btn-primary">Continue shopping</Link>
        <Link href="/" className="btn-secondary">Back to home</Link>
      </div>
    </div>
  );
}
