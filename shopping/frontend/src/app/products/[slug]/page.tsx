import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { Check, ChevronRight, RotateCcw, ShieldCheck, Truck } from "lucide-react";
import { AddToCartPanel } from "@/components/AddToCartPanel";
import { BackendOffline } from "@/components/BackendOffline";
import { ProductCard } from "@/components/ProductCard";
import { ProductGallery } from "@/components/ProductGallery";
import { Badge, Price, Rating, SectionHeading } from "@/components/ui";
import { ApiRequestError, getProduct } from "@/lib/api";
import { CATEGORY_LABELS } from "@/lib/format";
import type { ProductDetail } from "@/lib/types";

export const dynamic = "force-dynamic";

type Params = Promise<{ slug: string }>;

async function load(slug: string): Promise<ProductDetail | Error | null> {
  try {
    return await getProduct(slug);
  } catch (e) {
    if (e instanceof ApiRequestError && e.status === 404) return null;
    return e as Error;
  }
}

export async function generateMetadata({ params }: { params: Params }): Promise<Metadata> {
  const result = await load((await params).slug);
  return result && !(result instanceof Error) ? { title: result.name, description: result.tagline } : { title: "Product" };
}

export default async function ProductPage({ params }: { params: Params }) {
  const result = await load((await params).slug);
  if (result === null) notFound();
  if (result instanceof Error) return <BackendOffline message={result.message} />;
  const product = result;
  const category = CATEGORY_LABELS[product.category] ?? product.category;

  return (
    <div className="container-page pt-8">
      <nav className="flex items-center gap-1.5 text-sm text-ink-400" aria-label="Breadcrumb">
        <Link href="/" className="hover:text-ink-700">Home</Link>
        <ChevronRight className="h-3.5 w-3.5" />
        <Link href={`/products?category=${product.category}`} className="hover:text-ink-700">{category}</Link>
        <ChevronRight className="h-3.5 w-3.5" />
        <span className="truncate text-ink-600">{product.name}</span>
      </nav>

      <div className="mt-8 grid gap-10 lg:grid-cols-[1.15fr_1fr] lg:gap-16">
        <ProductGallery src={product.image} alt={product.name} dimmed={!product.in_stock} />

        <div className="lg:sticky lg:top-28 lg:self-start">
          <div className="flex items-center gap-3">
            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-nova-600">{category}</p>
            {product.badge && <Badge label={product.badge} />}
          </div>
          <h1 className="mt-3 font-display text-4xl leading-tight tracking-tight sm:text-5xl">{product.name}</h1>
          <p className="mt-3 text-lg text-ink-500">{product.tagline}</p>
          <div className="mt-5 flex flex-wrap items-center gap-x-6 gap-y-2">
            <Price cents={product.price_cents} compareAt={product.compare_at_cents} className="text-2xl" />
            <Rating value={product.rating} count={product.review_count} />
          </div>

          <div className="mt-8 border-t border-ink-200 pt-8">
            <AddToCartPanel product={product} />
          </div>

          <div className="mt-8 grid grid-cols-3 gap-3 text-center text-xs text-ink-500">
            {[
              [Truck, "Free shipping over $75"],
              [RotateCcw, "30-day returns"],
              [ShieldCheck, "2-year warranty"],
            ].map(([Icon, label]) => {
              const I = Icon as typeof Truck;
              return (
                <div key={label as string} className="flex flex-col items-center gap-2 rounded-2xl bg-white p-4 shadow-soft">
                  <I className="h-5 w-5 text-nova-600" />
                  {label as string}
                </div>
              );
            })}
          </div>

          <div className="mt-10 space-y-6">
            <div>
              <h2 className="font-semibold">About this product</h2>
              <p className="mt-2 leading-relaxed text-ink-600">{product.description}</p>
            </div>
            <div>
              <h2 className="font-semibold">Highlights</h2>
              <ul className="mt-3 grid gap-2 sm:grid-cols-2">
                {product.features.map((f) => (
                  <li key={f} className="flex items-start gap-2 text-sm text-ink-600">
                    <Check className="mt-0.5 h-4 w-4 flex-none text-nova-600" /> {f}
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </div>
      </div>

      {product.related.length > 0 && (
        <section className="mt-24">
          <SectionHeading eyebrow="Complete the look" title={`More in ${category}`} />
          <div className="mt-10 grid grid-cols-2 gap-x-5 gap-y-10 lg:grid-cols-4">
            {product.related.map((p) => (
              <ProductCard key={p.id} product={p} />
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
