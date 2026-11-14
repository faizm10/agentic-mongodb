import { handle } from "@/lib/api";
import { db, C } from "@/lib/db";
import { retryDeliveries } from "@/lib/plugins";

export async function POST() {
  return handle(async () => {
    const current = await (await db()).collection<{ _id: string; value: string }>(C.controls).findOne({ _id: "current_incident" });
    if (!current) throw new Error("no current incident");
    return retryDeliveries(current.value);
  });
}
