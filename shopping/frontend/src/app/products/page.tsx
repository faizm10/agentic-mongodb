import type { Metadata } from "next";
import Link from "next/link";
import { SearchX } from "lucide-react";
import { BackendOffline } from "@/components/BackendOffline";
import { ProductCard } from "@/components/ProductCard";
import { ShopFilters } from "@/components/ShopFilters";
import { getCategories, getProducts } from "@/lib/api";
import type { Category, Product } from "@/lib/types";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Shop" };

type SearchParams = Promise<Record<string, string | string[] | undefined>>;

export default async function ProductsPage({ searchParams }: { searchParams: SearchParams }) {
  const sp = await searchParams;
  const get = (k: string) => (typeof sp[k] === "string" ? (sp[k] as string) : undefined);
  const query = {
    q: get("q"),
    category: get("category"),
    sort: get("sort"),
    in_stock: get("in_stock") === "true",
  };

  let products: Product[];
  let categories: Category[];
  try {
    const [res, cats] = await Promise.all([getProducts(query), getCategories()]);
    products = res.products;
    categories = cats;
  } catch (e) {
    return <BackendOffline message={(e as Error).message} />;
  }

  const active = categories.find((c) => c.slug === query.category);

  return (
    <div className="container-page pb-8 pt-10 sm:pt-14">
      <header className="mb-8 max-w-2xl">
        <p className="text-xs font-semibold uppercase tracking-[0.2em] text-nova-600">{active ? "Category" : "The collection"}</p>
        <h1 className="mt-3 font-display text-4xl tracking-tight sm:text-5xl">
          {query.q ? <>Results for “{query.q}”</> : active ? active.name : "Shop everything"}
        </h1>
        <p className="mt-3 text-ink-500">{active ? active.blurb : "Design-led essentials for listening, working, living and going."}</p>
      </header>

      <ShopFilters categories={categories} total={products.length} />

      {products.length === 0 ? (
        <div className="flex flex-col items-center py-24 text-center">
          <div className="flex h-16 w-16 items-center justify-center rounded-full bg-white shadow-soft">
            <SearchX className="h-7 w-7 text-ink-400" />
          </div>
          <h2 className="mt-6 font-display text-2xl">No products found</h2>
          <p className="mt-2 text-ink-500">Try a different search or browse all products.</p>
          <Link href="/products" className="btn-primary mt-6">
            Clear filters
          </Link>
        </div>
      ) : (
        <div className="mt-10 grid grid-cols-2 gap-x-5 gap-y-12 md:grid-cols-3 lg:grid-cols-4">
          {products.map((p, i) => (
            <ProductCard key={p.id} product={p} priority={i < 4} />
          ))}
        </div>
      )}
    </div>
  );
}
