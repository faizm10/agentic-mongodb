import Link from "next/link";

export default function NotFound() {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center px-6 text-center">
      <p className="font-mono text-sm text-faint">404</p>
      <h1 className="mt-2 text-2xl font-semibold">Nothing to monitor here</h1>
      <Link href="/" className="mt-6 rounded-lg border border-line-strong px-4 py-2 text-sm hover:bg-hover">Back to status</Link>
    </div>
  );
}
