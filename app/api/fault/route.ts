import { db, C } from "@/lib/db";
import { handle } from "@/lib/api";

export async function POST(req: Request) {
  const { armed } = await req.json();
  return handle(async () => {
    await (await db()).collection<{ _id: string }>(C.controls).updateOne({ _id: "fault_crash_after_grant" }, { $set: { armed: !!armed }, $unset: { consumedBy: "", consumedAt: "" } }, { upsert: true });
    return { armed: !!armed };
  });
}
