import { connection } from "next/server";
import { db, C } from "@/lib/db";
import type { Hire } from "@/lib/types";

export default async function Portal() {
  await connection();
  const hires = await (await db()).collection<Hire>(C.hires).find({ synthetic: { $ne: true } }).sort({ _id: 1 }).toArray();
  return (
    <div className="mx-auto max-w-2xl space-y-4">
      <h1 className="text-2xl font-normal">Acme Employee Portal</h1>
      <p className="text-sm text-gray-500">Signed in. Your onboarding checklist:</p>
      {hires.map((h) => (
        <div key={h._id} className="flex items-center justify-between rounded border bg-white p-4">
          <div>
            <div className="font-medium">{h.name}</div>
            <div className="text-xs text-gray-500">{h.email} · GitHub @{h.githubLogin}</div>
          </div>
          <div className="text-right text-sm">
            <div className="text-xs text-gray-500">Engineering GitHub team</div>
            <span className={`rounded px-2 py-0.5 text-xs font-medium ${h.status === "complete" ? "bg-green-100 text-green-800" : "bg-yellow-100 text-yellow-800"}`}>{h.status === "complete" ? "Complete" : "Pending"}</span>
          </div>
        </div>
      ))}
    </div>
  );
}
