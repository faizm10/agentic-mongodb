import { handle } from "@/lib/api";
import { getPlugin } from "@/lib/plugins";

export async function POST(_req: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return handle(async () => {
    const p = getPlugin(id);
    if (!p) throw new Error(`unknown plugin ${id}`);
    if (!p.configured()) return { ok: false, detail: "Not connected: set the environment variables first." };
    return p.sendTest ? p.sendTest() : p.check();
  });
}
