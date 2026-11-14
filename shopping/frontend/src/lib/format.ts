const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });

export function money(cents: number): string {
  return usd.format(cents / 100);
}

/** Request an appropriately sized, cropped rendition from Unsplash's image CDN. */
export function img(src: string, width: number, opts: { height?: number; zoom?: number; fx?: number; fy?: number } = {}) {
  if (!src.includes("images.unsplash.com")) return src;
  const params = new URLSearchParams({ auto: "format", fit: "crop", w: String(width), q: "80" });
  if (opts.height) params.set("h", String(opts.height));
  if (opts.zoom) {
    params.set("crop", "focalpoint");
    params.set("fp-z", String(opts.zoom));
    params.set("fp-x", String(opts.fx ?? 0.5));
    params.set("fp-y", String(opts.fy ?? 0.5));
  }
  return `${src}?${params.toString()}`;
}

export function formatDate(iso: string): string {
  return new Date(`${iso.length === 10 ? `${iso}T12:00:00` : iso}`).toLocaleDateString("en-US", {
    weekday: "short",
    month: "short",
    day: "numeric",
  });
}

export const CATEGORY_LABELS: Record<string, string> = {
  audio: "Audio",
  wearables: "Wearables",
  workspace: "Workspace",
  home: "Home",
  accessories: "Accessories",
  apparel: "Apparel",
};
