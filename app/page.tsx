import Link from "next/link";
import { connection } from "next/server";
import { db, C } from "@/lib/db";
import { discoverRepos, services } from "@/lib/catalog";
import { integrationStatus, type Status } from "@/lib/integrations";
import { StatusActions } from "@/components/StatusActions";

const HISTORY = 16;

function StatusLabel({ ok }: { ok?: boolean }) {
  return ok ? (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap text-sm text-ok">
      <span className="flex h-5 w-5 items-center justify-center rounded-full bg-ok text-xs text-white">✓</span>Available
    </span>
  ) : (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap text-sm font-medium text-warn">
      <span className="flex h-5 w-5 items-center justify-center rounded-full bg-warn text-xs font-bold text-white">!</span>Disruption
    </span>
  );
}

const statusStyle: Record<Status, string> = {
  verified: "bg-green-100 text-green-800",
  configured_unverified: "bg-yellow-100 text-yellow-800",
  fallback: "bg-orange-100 text-orange-800",
  missing: "bg-gray-100 text-gray-600",
  error: "bg-red-100 text-red-800",
};

export default async function StatusPage() {
  await connection();
  let d;
  try {
    d = await db();
    await d.command({ ping: 1 });
  } catch (e) {
    return <div className="rounded border border-red-300 bg-red-50 p-4 text-sm text-red-800">Database unavailable: {(e as Error).message}</div>;
  }

  const checkIds = services.flatMap((s) => s.checks);
  const history = Object.fromEntries(
    await Promise.all(checkIds.map(async (id) => [id, (await d.collection(C.checks).find({ checkId: id }).sort({ at: -1 }).limit(HISTORY).toArray()).reverse()] as const)),
  );
  const current = await d.collection<{ _id: string; value: string }>(C.controls).findOne({ _id: "current_incident" });
  const incident = current ? await d.collection(C.incidents).findOne({ _id: current.value as any }) : null;
  const [integrations, catalog] = await Promise.all([integrationStatus(), discoverRepos()]);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-medium">Internal tools status</h1>
          <p className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-muted">
            <span className="inline-flex items-center gap-1.5"><span className="inline-block h-3 w-2 rounded-sm bg-green-500" />Check passed</span>
            <span className="inline-flex items-center gap-1.5"><span className="inline-block h-3 w-2 rounded-sm bg-orange-500" />Check failed</span>
            <span className="inline-flex items-center gap-1.5"><span className="cell-seeded inline-block h-3 w-2 rounded-sm bg-green-500" />Seeded history</span>
          </p>
        </div>
        <StatusActions />
      </div>

      {incident && (
        <Link
          href={`/incidents/${incident._id}`}
          className={`flex flex-col gap-2 rounded-lg border p-4 sm:flex-row sm:items-center ${incident.status === "resolved" ? "border-ok/30 bg-ok-soft" : "border-warn/40 bg-warn-soft"}`}
        >
          <div className="flex-1">
            <div className="font-medium">{incident.status === "resolved" ? "Resolved: " : "Active disruption: "}{incident.title}</div>
            <div className="font-mono text-xs text-muted">{String(incident._id)}</div>
          </div>
          <span className={`whitespace-nowrap rounded-md px-4 py-2 text-center font-medium text-white ${incident.status === "resolved" ? "bg-ok" : "bg-accent"}`}>
            {incident.status === "resolved" ? "See how it was fixed →" : "Open agent investigation →"}
          </span>
        </Link>
      )}

      <div className="overflow-x-auto rounded-lg border border-line bg-surface">
        <table className="w-full text-sm">
          <thead className="border-b border-line bg-canvas text-left text-xs text-muted">
            <tr>
              <th className="px-4 py-2 font-medium">Service</th>
              <th className="px-4 py-2 font-medium">Check</th>
              <th className="px-4 py-2 font-medium">Now</th>
              <th className="px-4 py-2 font-medium">Recent checks (oldest → newest)</th>
              <th><span className="sr-only">Action</span></th>
            </tr>
          </thead>
          <tbody>
            {services.flatMap((s) =>
              s.checks.map((checkId) => {
                const h = history[checkId] || [];
                const latest = h.at(-1);
                const disrupted = latest && !latest.ok;
                const row = (
                  <>
                    <td className="px-4 py-3">
                      {s.name}
                      {!s.live && <span className="ml-2 rounded bg-gray-100 px-1.5 py-0.5 text-[10px] uppercase text-gray-500">seeded</span>}
                    </td>
                    <td className="px-4 py-3 font-mono text-xs text-gray-600">{checkId}</td>
                    <td className="px-4 py-3"><StatusLabel ok={latest?.ok} /></td>
                    <td className="px-4 py-3">
                      <div className="flex gap-1">
                        {h.map((c) => (
                          <span key={String(c._id)} title={`${new Date(c.at).toLocaleTimeString()} — ${c.detail}`} className={`inline-block h-5 w-3 rounded-sm ${c.ok ? "bg-green-500" : "bg-orange-500"} ${c.seeded ? "cell-seeded" : ""}`} />
                        ))}
                      </div>
                    </td>
                  </>
                );
                return disrupted && incident && incident.serviceId === s.id ? (
                  <tr key={checkId} className="relative border-b border-line bg-warn-soft/50 hover:bg-warn-soft">
                    {row}
                    <td className="pr-4">
                      <Link href={`/incidents/${incident._id}`} className="whitespace-nowrap text-sm font-medium text-accent after:absolute after:inset-0">
                        Investigate →
                      </Link>
                    </td>
                  </tr>
                ) : (
                  <tr key={checkId} className="border-b border-line">{row}<td /></tr>
                );
              }),
            )}
          </tbody>
        </table>
      </div>

      <div className="grid gap-6 md:grid-cols-2">
        <section className="rounded-lg border border-line bg-surface p-4">
          <div className="mb-2 flex items-baseline justify-between">
            <h2 className="font-medium">Integrations</h2>
            <Link href="/plugins" className="text-sm text-accent hover:underline">GitHub, Slack, Jira plugins →</Link>
          </div>
          <ul className="space-y-2 text-sm">
            {integrations.map((i) => (
              <li key={i.id}>
                <span className={`mr-2 rounded px-1.5 py-0.5 text-[11px] font-medium ${statusStyle[i.status]}`}>{i.status.replace("_", " ")}</span>
                <span className="font-medium">{i.name}</span>
                <div className="ml-1 text-xs text-gray-500">{i.detail}</div>
              </li>
            ))}
          </ul>
        </section>
        <section className="rounded-lg border border-line bg-surface p-4">
          <h2 className="mb-1 font-medium">Repo catalog</h2>
          <p className="mb-2 text-xs text-gray-500">
            {catalog.source === "seeded" && <span className="mr-1 rounded bg-gray-100 px-1.5 py-0.5 text-[10px] uppercase">seeded</span>}
            {catalog.detail}. Mapping lives in <code>config/service-catalog.json</code>.
          </p>
          <table className="w-full text-sm">
            <tbody>
              {catalog.repos.map((r) => (
                <tr key={r.name} className="border-t">
                  <td className="py-1.5 font-mono text-xs">{r.name}</td>
                  <td className="py-1.5 text-xs">{r.monitored ? services.find((s) => s.id === r.service)?.name : <span className="text-gray-400">Not monitored</span>}</td>
                  <td className="py-1.5 font-mono text-[11px] text-gray-500">{r.checks.join(", ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      </div>
    </div>
  );
}
