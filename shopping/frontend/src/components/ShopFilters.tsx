"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { X } from "lucide-react";
import { useTransition } from "react";
import type { Category } from "@/lib/types";

const SORTS = [
  ["featured", "Featured"],
  ["rating", "Top rated"],
  ["price_asc", "Price: low to high"],
  ["price_desc", "Price: high to low"],
  ["name", "Name A–Z"],
] as const;

export function ShopFilters({ categories, total }: { categories: Category[]; total: number }) {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const [pending, startTransition] = useTransition();

  const current = {
    q: params.get("q") ?? "",
    category: params.get("category") ?? "all",
    sort: params.get("sort") ?? "featured",
    inStock: params.get("in_stock") === "true",
  };

  function update(patch: Record<string, string | null>) {
    const next = new URLSearchParams(params.toString());
    Object.entries(patch).forEach(([k, v]) => (v === null || v === "" || v === "all" ? next.delete(k) : next.set(k, v)));
    startTransition(() => router.push(`${pathname}${next.toString() ? `?${next}` : ""}`, { scroll: false }));
  }

  return (
    <div className={`space-y-5 transition-opacity ${pending ? "opacity-60" : ""}`}>
      <div className="-mx-4 flex gap-2 overflow-x-auto px-4 pb-1 sm:mx-0 sm:flex-wrap sm:px-0">
        {[{ slug: "all", name: "All" }, ...categories].map((c) => {
          const active = current.category === c.slug;
          return (
            <button
              key={c.slug}
              onClick={() => update({ category: c.slug })}
              className={`whitespace-nowrap rounded-full px-4 py-2 text-sm font-medium transition ${
                active ? "bg-ink-900 text-white shadow-soft" : "border border-ink-200 bg-white text-ink-600 hover:border-ink-300 hover:text-ink-900"
              }`}
              aria-pressed={active}
            >
              {c.name}
            </button>
          );
        })}
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3 border-y border-ink-200 py-3">
        <div className="flex flex-wrap items-center gap-3 text-sm text-ink-500">
          <span>
            <strong className="text-ink-900">{total}</strong> {total === 1 ? "product" : "products"}
          </span>
          {current.q && (
            <button onClick={() => update({ q: null })} className="inline-flex items-center gap-1 rounded-full bg-nova-50 px-3 py-1 font-medium text-nova-700 hover:bg-nova-100">
              “{current.q}” <X className="h-3.5 w-3.5" />
            </button>
          )}
        </div>
        <div className="flex items-center gap-4">
          <label className="flex cursor-pointer items-center gap-2 text-sm text-ink-600">
            <input
              type="checkbox"
              checked={current.inStock}
              onChange={(e) => update({ in_stock: e.target.checked ? "true" : null })}
              className="h-4 w-4 rounded border-ink-300 accent-nova-600"
            />
            In stock only
          </label>
          <select
            value={current.sort}
            onChange={(e) => update({ sort: e.target.value === "featured" ? null : e.target.value })}
            aria-label="Sort products"
            className="h-10 rounded-full border border-ink-200 bg-white px-4 pr-8 text-sm font-medium outline-none focus:border-nova-400"
          >
            {SORTS.map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </select>
        </div>
      </div>
    </div>
  );
}
