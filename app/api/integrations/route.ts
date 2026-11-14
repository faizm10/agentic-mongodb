import { handle } from "@/lib/api";
import { integrationStatus } from "@/lib/integrations";

export async function GET() {
  return handle(integrationStatus);
}
