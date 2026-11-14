import Link from "next/link";
import { ArrowRight, BadgePercent, PackageCheck, RotateCcw, ShieldCheck, Star, Truck } from "lucide-react";
import { BackendOffline } from "@/components/BackendOffline";
import { Newsletter } from "@/components/Newsletter";
import { ProductCard } from "@/components/ProductCard";
import { ProductImage } from "@/components/ProductImage";
import { SectionHeading } from "@/components/ui";
import { getCategories, getProducts, getPromotions } from "@/lib/api";
import { money } from "@/lib/format";

export const dynamic = "force-dynamic";

const UNSPLASH = "https://images.unsplash.com/photo-";

export default async function HomePage() {
  let data;
  try {
    const [featured, categories, promotions, all] = await Promise.all([
      getProducts({ featured: true }),
      getCategories(),
      getPromotions(),
      getProducts({ sort: "rating" }),
    ]);
    data = { featured: featured.products, categories, promotions, topRated: all.products.slice(0, 4) };
  } catch (e) {
    return <BackendOffline message={(e as Error).message} />;
  }
  const hero = data.featured.find((p) => p.id === "nm-aud-001") ?? data.featured[0];

  return (
    <>
      {/* Hero */}
      <section className="relative overflow-hidden">
        <div className="pointer-events-none absolute -right-40 -top-40 h-[36rem] w-[36rem] rounded-full bg-nova-200/50 blur-3xl" />
        <div className="pointer-events-none absolute -left-32 top-48 h-80 w-80 rounded-full bg-ember-400/20 blur-3xl" />
        <div className="container-page relative grid items-center gap-12 pb-20 pt-12 lg:grid-cols-[1.05fr_1fr] lg:pb-28 lg:pt-20">
          <div className="animate-fade-up">
            <p className="inline-flex items-center gap-2 rounded-full border border-ink-200 bg-white/70 px-3 py-1.5 text-xs font-semibold uppercase tracking-[0.18em] text-ink-600 backdrop-blur">
              <span className="h-1.5 w-1.5 rounded-full bg-nova-500" /> The Autumn Edit · 2026
            </p>
            <h1 className="mt-6 font-display text-5xl leading-[1.02] tracking-tight text-ink-900 sm:text-6xl lg:text-7xl">
              Objects for a <em className="text-nova-600">considered</em> life.
            </h1>
            <p className="mt-6 max-w-lg text-lg leading-relaxed text-ink-500">
              Beautifully engineered audio, timeless wearables and calm essentials for home and work, chosen to be
              used every day and kept for years.
            </p>
            <div className="mt-9 flex flex-wrap gap-3">
              <Link href="/products" className="btn-primary px-7">
                Shop the collection <ArrowRight className="h-4 w-4" />
              </Link>
              <Link href="/products?category=audio" className="btn-secondary px-7">
                Explore audio
              </Link>
            </div>
            <dl className="mt-12 grid max-w-md grid-cols-3 gap-6 border-t border-ink-200 pt-8">
              {[
                ["4.8", "avg. rating"],
                ["30-day", "free returns"],
                ["2-year", "warranty"],
              ].map(([v, l]) => (
                <div key={l}>
                  <dt className="font-display text-2xl text-ink-900">{v}</dt>
                  <dd className="mt-1 text-xs uppercase tracking-wider text-ink-400">{l}</dd>
                </div>
              ))}
            </dl>
          </div>

          <div className="relative animate-fade-up [animation-delay:120ms]">
            <div className="relative aspect-[4/5] overflow-hidden rounded-[2.5rem] shadow-lift">
              <ProductImage src={`${UNSPLASH}1519710164239-da123dc03ef4`} alt="A calm, sunlit living space" width={1100} height={1375} priority className="h-full w-full" />
              <div className="absolute inset-0 bg-gradient-to-t from-ink-900/30 via-transparent" />
            </div>
            {hero && (
              <Link
                href={`/products/${hero.slug}`}
                className="absolute -bottom-6 -left-4 flex w-72 items-center gap-4 rounded-3xl bg-white/95 p-3 pr-5 shadow-lift backdrop-blur transition hover:-translate-y-1 sm:-left-10"
              >
                <ProductImage src={hero.image} alt={hero.name} width={200} height={200} className="h-20 w-20 rounded-2xl" />
                <div className="min-w-0">
                  <p className="flex items-center gap-1 text-xs font-medium text-amber-600">
                    <Star className="h-3 w-3 fill-current" /> {hero.rating} · Bestseller
                  </p>
                  <p className="truncate text-sm font-semibold">{hero.name}</p>
                  <p className="text-sm text-ink-500">{money(hero.price_cents)}</p>
                </div>
              </Link>
            )}
            <div className="absolute -right-3 top-8 hidden rounded-2xl bg-ink-900 px-4 py-3 text-white shadow-lift sm:block">
              <p className="text-[11px] uppercase tracking-wider text-ink-300">Free shipping</p>
              <p className="font-display text-xl">over $75</p>
            </div>
          </div>
        </div>
      </section>

      {/* Categories */}
      <section className="container-page">
        <SectionHeading eyebrow="Browse" title="Shop by category">
          <Link href="/products" className="flex items-center gap-1 text-sm font-semibold text-ink-700 hover:text-nova-700">
            View all products <ArrowRight className="h-4 w-4" />
          </Link>
        </SectionHeading>
        <div className="mt-10 grid grid-cols-2 gap-4 md:grid-cols-3 lg:grid-cols-6">
          {data.categories.map((c) => (
            <Link key={c.slug} href={`/products?category=${c.slug}`} className="group relative aspect-[3/4] overflow-hidden rounded-3xl bg-ink-200">
              <ProductImage src={c.image} alt={c.name} width={480} height={640} className="h-full w-full transition duration-700 group-hover:scale-105" />
              <div className="absolute inset-0 bg-gradient-to-t from-ink-900/75 via-ink-900/10 to-transparent" />
              <div className="absolute inset-x-0 bottom-0 p-4 text-white">
                <p className="font-display text-xl">{c.name}</p>
                <p className="text-xs text-white/75">{c.product_count} products</p>
              </div>
            </Link>
          ))}
        </div>
      </section>

      {/* Featured */}
      <section className="container-page mt-24">
        <SectionHeading eyebrow="Curated" title="This season’s favorites" />
        <div className="mt-10 grid grid-cols-2 gap-x-5 gap-y-10 md:grid-cols-3 lg:grid-cols-4">
          {data.featured.slice(0, 8).map((p, i) => (
            <ProductCard key={p.id} product={p} priority={i < 4} />
          ))}
        </div>
      </section>

      {/* Editorial banner */}
      <section className="container-page mt-24">
        <div className="relative overflow-hidden rounded-[2.5rem] bg-ink-900">
          <div className="grid lg:grid-cols-2">
            <div className="relative z-10 flex flex-col justify-center p-10 sm:p-14 lg:p-16">
              <p className="text-xs font-semibold uppercase tracking-[0.2em] text-nova-300">Workspace</p>
              <h2 className="mt-4 font-display text-4xl leading-tight text-white sm:text-5xl">
                Your desk, <em>refined</em>.
              </h2>
              <p className="mt-5 max-w-md text-ink-300">
                Low-profile keyboards, glare-free lighting and analog tools that make deep work feel effortless.
              </p>
              <Link href="/products?category=workspace" className="mt-8 inline-flex w-fit items-center gap-2 rounded-full bg-white px-6 py-3.5 text-sm font-semibold text-ink-900 transition hover:bg-nova-100">
                Shop workspace <ArrowRight className="h-4 w-4" />
              </Link>
            </div>
            <div className="relative min-h-80">
              <ProductImage src={`${UNSPLASH}1593062096033-9a26b09da705`} alt="A warm, minimal desk setup" width={1200} height={900} className="absolute inset-0 h-full w-full" />
              <div className="absolute inset-0 bg-gradient-to-r from-ink-900 via-ink-900/20 to-transparent max-lg:bg-gradient-to-t" />
            </div>
          </div>
        </div>
      </section>

      {/* Top rated */}
      <section className="container-page mt-24">
        <SectionHeading eyebrow="Loved by customers" title="Top rated">
          <Link href="/products?sort=rating" className="flex items-center gap-1 text-sm font-semibold text-ink-700 hover:text-nova-700">
            See more <ArrowRight className="h-4 w-4" />
          </Link>
        </SectionHeading>
        <div className="mt-10 grid grid-cols-2 gap-x-5 gap-y-10 lg:grid-cols-4">
          {data.topRated.map((p) => (
            <ProductCard key={p.id} product={p} />
          ))}
        </div>
      </section>

      {/* Promotions */}
      <section id="promotions" className="container-page mt-24 scroll-mt-24">
        <SectionHeading eyebrow="Offers" title="Codes worth knowing" />
        <div className="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {data.promotions.map((p) => (
            <div key={p.code} className="card flex flex-col gap-3 p-6">
              <BadgePercent className="h-6 w-6 text-nova-600" />
              <p className="font-mono text-lg font-semibold tracking-wider">{p.code}</p>
              <p className="text-sm text-ink-500">{p.description}</p>
            </div>
          ))}
        </div>
      </section>

      {/* Value props */}
      <section className="container-page mt-24">
        <div className="grid gap-6 rounded-[2rem] border border-ink-200 bg-white p-8 sm:grid-cols-2 lg:grid-cols-4">
          {[
            [Truck, "Free shipping", "On every order over $75."],
            [RotateCcw, "30-day returns", "Changed your mind? No problem."],
            [PackageCheck, "2-year warranty", "Built to last, backed by us."],
            [ShieldCheck, "Simulated checkout", "Demo store: nothing is ever charged."],
          ].map(([Icon, title, copy]) => {
            const I = Icon as typeof Truck;
            return (
              <div key={title as string} className="flex gap-4">
                <div className="flex h-11 w-11 flex-none items-center justify-center rounded-2xl bg-nova-50 text-nova-700">
                  <I className="h-5 w-5" />
                </div>
                <div>
                  <p className="font-semibold">{title as string}</p>
                  <p className="mt-1 text-sm text-ink-500">{copy as string}</p>
                </div>
              </div>
            );
          })}
        </div>
      </section>

      {/* Newsletter */}
      <section className="container-page mt-24">
        <div className="relative overflow-hidden rounded-[2.5rem] bg-gradient-to-br from-nova-700 via-nova-800 to-ink-900 px-8 py-14 text-white sm:px-14">
          <div className="pointer-events-none absolute -right-20 -top-20 h-72 w-72 rounded-full bg-ember-500/30 blur-3xl" />
          <div className="relative flex flex-col items-start justify-between gap-8 lg:flex-row lg:items-center">
            <div>
              <h2 className="font-display text-4xl">Join the NovaMart list</h2>
              <p className="mt-3 max-w-md text-nova-100">New arrivals, restocks and members-only offers, a couple of times a month.</p>
            </div>
            <Newsletter />
          </div>
        </div>
      </section>
    </>
  );
}
