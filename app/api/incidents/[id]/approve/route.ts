import { handle } from "@/lib/api";
import { approveRestore } from "@/lib/approve";
import { launchWorker } from "@/lib/supervisor";

export async function POST(req: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const { approvedBy } = await req.json().catch(() => ({}));
  return handle(async () => {
    const approval = await approveRestore(id, String(approvedBy || "on-call engineer"));
    const w = await launchWorker(id, "approval");
    return { approval, workerId: w.workerId };
  });
}
