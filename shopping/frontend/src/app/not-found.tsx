import Link from "next/link";

export default function NotFound() {
  return (
    <div className="container-page flex flex-col items-center py-32 text-center">
      <p className="font-display text-8xl text-nova-200">404</p>
      <h1 className="mt-4 font-display text-4xl">We couldn&apos;t find that page</h1>
      <p className="mt-3 text-ink-500">The product or order you&apos;re looking for doesn&apos;t exist.</p>
      <Link href="/products" className="btn-primary mt-8">Browse the collection</Link>
    </div>
  );
}
