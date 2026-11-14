import Link from "next/link";
import type { Product } from "@/lib/types";
import { CATEGORY_LABELS } from "@/lib/format";
import { ProductImage } from "./ProductImage";
import { QuickAdd } from "./QuickAdd";
import { Badge, Price, Rating } from "./ui";

export function ProductCard({ product, priority = false }: { product: Product; priority?: boolean }) {
  const badge = !product.in_stock ? "Sold out" : product.badge;
  return (
    <article className="group relative flex flex-col">
      <div className="relative aspect-[4/5] overflow-hidden rounded-3xl bg-ink-100">
        <Link href={`/products/${product.slug}`} aria-label={product.name} className="absolute inset-0">
          <ProductImage
            src={product.image}
            alt={product.name}
            width={640}
            height={800}
            priority={priority}
            className={`h-full w-full transition duration-700 ease-out group-hover:scale-[1.04] ${product.in_stock ? "" : "opacity-60 grayscale"}`}
          />
        </Link>
        {badge && (
          <div className="pointer-events-none absolute left-3 top-3">
            <Badge label={badge} />
          </div>
        )}
        <div className="absolute inset-x-3 bottom-3 translate-y-2 opacity-0 transition duration-300 group-hover:translate-y-0 group-hover:opacity-100 max-sm:translate-y-0 max-sm:opacity-100">
          <QuickAdd product={product} />
        </div>
      </div>
      <div className="mt-4 flex flex-1 flex-col gap-1.5 px-1">
        <p className="text-xs font-medium uppercase tracking-wider text-ink-400">{CATEGORY_LABELS[product.category] ?? product.category}</p>
        <Link href={`/products/${product.slug}`} className="font-medium leading-snug text-ink-900 hover:text-nova-700">
          {product.name}
        </Link>
        <Rating value={product.rating} count={product.review_count} size={12} />
        <Price cents={product.price_cents} compareAt={product.compare_at_cents} className="mt-1" />
      </div>
    </article>
  );
}
