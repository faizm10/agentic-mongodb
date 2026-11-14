import Link from "next/link";
import { ShieldCheck } from "lucide-react";
import { Logo } from "./Header";

const COLUMNS = [
  {
    title: "Shop",
    links: [
      ["All products", "/products"],
      ["Audio", "/products?category=audio"],
      ["Wearables", "/products?category=wearables"],
      ["Home", "/products?category=home"],
    ],
  },
  {
    title: "Help",
    links: [
      ["Your cart", "/cart"],
      ["Checkout", "/checkout"],
      ["Promotions", "/#promotions"],
    ],
  },
];

export function Footer() {
  return (
    <footer className="mt-24 border-t border-ink-200 bg-white">
      <div className="mx-auto grid max-w-7xl gap-10 px-4 py-14 sm:px-6 md:grid-cols-[1.5fr_1fr_1fr] lg:px-8">
        <div className="max-w-sm">
          <Logo />
          <p className="mt-4 text-sm leading-relaxed text-ink-500">
            Thoughtfully designed objects for everyday life, curated by NovaMart.
          </p>
          <p className="mt-6 flex items-start gap-2 rounded-2xl bg-ink-50 p-3 text-xs leading-relaxed text-ink-600">
            <ShieldCheck className="mt-0.5 h-4 w-4 flex-none text-nova-600" />
            NovaMart is a demo storefront built for the OpsRelay hackathon. Checkout is simulated: no payment details are
            collected and nothing is ever charged.
          </p>
        </div>
        {COLUMNS.map((col) => (
          <div key={col.title}>
            <h3 className="text-sm font-semibold text-ink-900">{col.title}</h3>
            <ul className="mt-4 space-y-3">
              {col.links.map(([label, href]) => (
                <li key={label}>
                  <Link href={href} className="text-sm text-ink-500 transition hover:text-ink-900">
                    {label}
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
      <div className="border-t border-ink-100 py-6 text-center text-xs text-ink-400">
        © {new Date().getFullYear()} NovaMart Demo · Built with Next.js & FastAPI
      </div>
    </footer>
  );
}
