import { handle } from "@/lib/api";
import { pluginStatuses } from "@/lib/plugins";

export async function GET() {
  return handle(pluginStatuses);
}
