import OpenAI from "openai";
import { env } from "./env";
import { recordIntegration } from "./events";
import type { Investigation, ProposedAction } from "./types";

const ACTIONS: ProposedAction[] = ["restore_mapping", "restart_worker", "human_review"];

export async function explainDecision(inv: Omit<Investigation, "explanation" | "at">): Promise<Investigation["explanation"]> {
  const fallback = (): Investigation["explanation"] => ({
    source: "fallback_template",
    text:
      inv.decision.action === "restore_mapping"
        ? `The closest past incident (${inv.similar.results[0]?.title ?? "n/a"}) was fixed by restarting the worker, but that does not fit today: ` +
          `the worker heartbeat is fresh, every failure is a 404 for team ${inv.signals.currentTeamId}, and that ID was introduced by a config change away from the approved team ${inv.signals.approvedTeamId}. Restoring the approved mapping addresses the cause; a restart would not.`
        : `Rule ${inv.decision.rule}. ${inv.decision.reasons.join("; ")}.`,
  });
  if (!env.openaiKey) return fallback();

  try {
    const client = new OpenAI({ apiKey: env.openaiKey });
    const r = await client.chat.completions.create({
      model: env.openaiModel,
      response_format: { type: "json_object" },
      temperature: 0,
      messages: [
        {
          role: "system",
          content:
            "You are an on-call SRE assistant. Given live evidence, similar past incidents, and a rule-based proposed action, " +
            `respond with JSON {"recommended_action": one of ${JSON.stringify(ACTIONS)}, "explanation": string}. ` +
            "The explanation must be 2-4 sentences, cite specific evidence, and say explicitly whether the most similar past incident's fix applies today and why. " +
            "Recommend human_review if the evidence is contradictory or insufficient.",
        },
        { role: "user", content: JSON.stringify({ signals: inv.signals, evidence: inv.evidence, similarPastIncidents: inv.similar.results, ruleDecision: inv.decision }) },
      ],
    });
    const parsed = JSON.parse(r.choices[0]?.message?.content || "{}");
    const rec = ACTIONS.includes(parsed.recommended_action) ? (parsed.recommended_action as ProposedAction) : "human_review";
    await recordIntegration("openai", true, `chat ok (${env.openaiModel})`);
    return { source: "openai", model: env.openaiModel, text: String(parsed.explanation || ""), llmRecommendedAction: rec };
  } catch (e) {
    await recordIntegration("openai", false, `chat failed: ${(e as Error).message}`);
    return { ...fallback(), error: (e as Error).message };
  }
}
