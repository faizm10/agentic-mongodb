"use client";

import { useState } from "react";
import { ProductImage } from "./ProductImage";

// One studio photo per product; detail views are focal-point crops of it.
const VIEWS = [
  { zoom: undefined, fx: 0.5, fy: 0.5, label: "Full view" },
  { zoom: 1.7, fx: 0.4, fy: 0.45, label: "Detail" },
  { zoom: 2.2, fx: 0.6, fy: 0.55, label: "Close-up" },
];

export function ProductGallery({ src, alt, dimmed }: { src: string; alt: string; dimmed?: boolean }) {
  const [active, setActive] = useState(0);
  const view = VIEWS[active];
  return (
    <div className="flex flex-col-reverse gap-4 lg:flex-row">
      <div className="flex gap-3 lg:flex-col">
        {VIEWS.map((v, i) => (
          <button
            key={v.label}
            onClick={() => setActive(i)}
            aria-label={`Show ${v.label.toLowerCase()}`}
            aria-pressed={active === i}
            className={`overflow-hidden rounded-2xl ring-2 ring-offset-2 ring-offset-ink-50 transition ${active === i ? "ring-ink-900" : "ring-transparent opacity-70 hover:opacity-100"}`}
          >
            <ProductImage src={src} alt={`${alt}, ${v.label}`} width={180} height={220} zoom={v.zoom} fx={v.fx} fy={v.fy} className="h-24 w-20" />
          </button>
        ))}
      </div>
      <div className="relative aspect-[4/5] flex-1 overflow-hidden rounded-[2rem] bg-ink-100">
        <ProductImage
          key={active}
          src={src}
          alt={alt}
          width={1100}
          height={1375}
          zoom={view.zoom}
          fx={view.fx}
          fy={view.fy}
          priority
          className={`h-full w-full animate-fade-up ${dimmed ? "opacity-60 grayscale" : ""}`}
        />
      </div>
    </div>
  );
}
