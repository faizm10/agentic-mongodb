import { handle } from "@/lib/api";
import { launchWorker } from "@/lib/supervisor";

export async function POST(_req: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return handle(async () => {
    const w = await launchWorker(id, "operator: launch fresh worker");
    return { workerId: w.workerId, pid: w.pid };
  });
}
