import { handle } from "@/lib/api";
import { resetDemo } from "@/lib/seed";

export async function POST(req: Request) {
  const { variant } = await req.json().catch(() => ({}));
  return handle(() => resetDemo(variant === "stalled-worker" ? "stalled-worker" : "config-drift"));
}
