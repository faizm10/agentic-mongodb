import { handle } from "@/lib/api";
import { runOnboardingCheck, runPortalCheck } from "@/lib/provisioning";

export async function POST() {
  return handle(async () => ({ portal: await runPortalCheck(), onboarding: await runOnboardingCheck() }));
}
