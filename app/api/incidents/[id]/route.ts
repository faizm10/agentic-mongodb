import { NextResponse } from "next/server";
import { db, C } from "@/lib/db";
import { handle } from "@/lib/api";
import { buildProof } from "@/lib/proof";
import { readCheckpoint } from "@/lib/recovery";

export async function GET(_req: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const d = await db();
  const incident = await d.collection(C.incidents).findOne({ _id: id as any });
  if (!incident) {
    const current = await d.collection<{ _id: string; value: string }>(C.controls).findOne({ _id: "current_incident" });
    return NextResponse.json({ error: `Incident ${id} no longer exists (the demo was probably reset).`, currentIncidentId: current?.value ?? null }, { status: 404 });
  }
  return handle(async () => {
    const [events, checkpoint, proof, fault, hires] = await Promise.all([
      d.collection(C.events).find({ incidentId: id }).sort({ at: 1, _id: 1 }).toArray(),
      readCheckpoint(id),
      buildProof(id),
      d.collection(C.controls).findOne({ _id: "fault_crash_after_grant" as any }),
      d.collection(C.hires).find({ synthetic: { $ne: true } }).sort({ _id: 1 }).toArray(),
    ]);
    const liveWorker = proof.workers.find((w) => !w.exitedAt);
    return { incident, events, checkpoint, proof, fault, hires, liveWorker: liveWorker?._id ?? null };
  });
}
