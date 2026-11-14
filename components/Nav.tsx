"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";

const links = [
  { href: "/", label: "Status", match: (p: string) => p === "/" || p.startsWith("/incidents") },
  { href: "/portal", label: "Employee Portal", match: (p: string) => p.startsWith("/portal") },
  { href: "/plugins", label: "Plugins", match: (p: string) => p.startsWith("/plugins") },
];

export function Nav() {
  const path = usePathname();
  return (
    <nav className="flex gap-1 text-sm">
      {links.map((l) => (
        <Link
          key={l.href}
          href={l.href}
          aria-current={l.match(path) ? "page" : undefined}
          className={`rounded-md px-3 py-2 ${l.match(path) ? "bg-canvas font-medium text-ink" : "text-muted hover:text-ink"}`}
        >
          {l.label}
        </Link>
      ))}
    </nav>
  );
}
