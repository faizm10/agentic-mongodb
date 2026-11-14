"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Menu, Search, ShoppingBag, Sparkles, X } from "lucide-react";
import { Suspense, useEffect, useState } from "react";
import { useCart } from "@/lib/cart";

const NAV = [
  { href: "/products", label: "Shop all" },
  { href: "/products?category=audio", label: "Audio" },
  { href: "/products?category=wearables", label: "Wearables" },
  { href: "/products?category=workspace", label: "Workspace" },
  { href: "/products?category=home", label: "Home" },
  { href: "/products?category=accessories", label: "Accessories" },
];

export function Logo({ className = "" }: { className?: string }) {
  return (
    <Link href="/" className={`flex items-center gap-2 ${className}`} aria-label="NovaMart home">
      <span className="relative flex h-8 w-8 items-center justify-center rounded-xl bg-gradient-to-br from-nova-500 to-nova-800 text-white shadow-soft">
        <Sparkles className="h-4 w-4" />
      </span>
      <span className="font-display text-2xl tracking-tight text-ink-900">
        Nova<span className="text-nova-600">Mart</span>
      </span>
    </Link>
  );
}

function SearchBox({ onDone }: { onDone?: () => void }) {
  const router = useRouter();
  const params = useSearchParams();
  const [q, setQ] = useState(params.get("q") ?? "");
  useEffect(() => setQ(params.get("q") ?? ""), [params]);
  return (
    <form
      role="search"
      onSubmit={(e) => {
        e.preventDefault();
        router.push(q.trim() ? `/products?q=${encodeURIComponent(q.trim())}` : "/products");
        onDone?.();
      }}
      className="relative w-full"
    >
      <Search className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-400" />
      <input
        value={q}
        onChange={(e) => setQ(e.target.value)}
        type="search"
        placeholder="Search headphones, lamps, bags…"
        aria-label="Search products"
        className="h-10 w-full rounded-full border border-ink-200 bg-ink-50/60 pl-10 pr-4 text-sm outline-none transition placeholder:text-ink-400 focus:border-nova-400 focus:bg-white focus:ring-4 focus:ring-nova-100"
      />
    </form>
  );
}

export function Header() {
  const { itemCount, hydrated, openDrawer } = useCart();
  const pathname = usePathname();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [scrolled, setScrolled] = useState(false);

  useEffect(() => setMobileOpen(false), [pathname]);
  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 8);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  return (
    <>
      <div className="bg-ink-900 px-4 py-2 text-center text-xs font-medium tracking-wide text-ink-100">
        Free shipping on orders over $75 · Use code <span className="font-semibold text-white">WELCOME10</span> for 10% off
      </div>
      <header
        className={`sticky top-0 z-40 border-b transition-colors ${scrolled ? "border-ink-200/80 bg-white/85 backdrop-blur-xl" : "border-transparent bg-ink-50"}`}
      >
        <div className="mx-auto flex h-16 max-w-7xl items-center gap-6 px-4 sm:px-6 lg:px-8">
          <button className="-ml-1 p-1 lg:hidden" onClick={() => setMobileOpen((v) => !v)} aria-label="Toggle menu">
            {mobileOpen ? <X className="h-6 w-6" /> : <Menu className="h-6 w-6" />}
          </button>
          <Logo />
          <nav className="hidden items-center gap-6 lg:flex" aria-label="Primary">
            {NAV.map((item) => (
              <Link key={item.href} href={item.href} className="text-sm font-medium text-ink-600 transition hover:text-ink-900">
                {item.label}
              </Link>
            ))}
          </nav>
          <div className="ml-auto hidden w-72 md:block">
            <Suspense>
              <SearchBox />
            </Suspense>
          </div>
          <button
            onClick={openDrawer}
            className="relative ml-auto flex h-10 items-center gap-2 rounded-full bg-ink-900 px-4 text-sm font-semibold text-white transition hover:bg-nova-700 md:ml-0"
            aria-label={`Open cart, ${itemCount} items`}
          >
            <ShoppingBag className="h-4 w-4" />
            <span className="hidden sm:inline">Cart</span>
            {hydrated && itemCount > 0 && (
              <span key={itemCount} className="flex h-5 min-w-5 animate-pop items-center justify-center rounded-full bg-white px-1.5 text-[11px] font-bold text-ink-900">
                {itemCount}
              </span>
            )}
          </button>
        </div>
        {mobileOpen && (
          <div className="border-t border-ink-200 bg-white px-4 pb-6 pt-4 lg:hidden">
            <Suspense>
              <SearchBox onDone={() => setMobileOpen(false)} />
            </Suspense>
            <nav className="mt-4 grid gap-1" aria-label="Mobile">
              {NAV.map((item) => (
                <Link key={item.href} href={item.href} className="rounded-xl px-3 py-2.5 font-medium text-ink-700 hover:bg-ink-50">
                  {item.label}
                </Link>
              ))}
            </nav>
          </div>
        )}
      </header>
    </>
  );
}
