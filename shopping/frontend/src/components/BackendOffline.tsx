import { ServerCrash } from "lucide-react";

export function BackendOffline({ message }: { message?: string }) {
  return (
    <div className="container-page py-24">
      <div className="card mx-auto max-w-xl p-10 text-center">
        <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl bg-rose-50 text-rose-600">
          <ServerCrash className="h-7 w-7" />
        </div>
        <h1 className="mt-6 font-display text-3xl">The store is waking up</h1>
        <p className="mt-3 text-ink-500">
          We couldn&apos;t reach the NovaMart API. Start the backend and refresh this page.
        </p>
        <pre className="mt-6 overflow-x-auto rounded-2xl bg-ink-900 p-4 text-left text-xs text-ink-100">
          cd shopping/backend{"\n"}.venv/bin/uvicorn app.main:app --reload --port 8100
        </pre>
        {message && <p className="mt-4 font-mono text-xs text-ink-400">{message}</p>}
      </div>
    </div>
  );
}
