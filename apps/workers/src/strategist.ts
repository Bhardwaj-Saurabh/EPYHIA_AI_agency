// The Strategist (DESIGN.md sections 3, 5): brief -> completeness check ->
// completed brief + brand document + task plan. Delegation only - its single
// tool is the gate's /model_call; persistence is delegated to Ops
// (business_storage actions). It never touches a provider or the DB.
import { createHash } from "node:crypto";
import { modelCall, requestAction } from "./gateClient.js";

const SYSTEM_PROMPT = `You are the Strategist of EPYHIA, an AI agency that builds one real
business per tenant: a deployed website, a marketing pack, and a working
checkout. You turn an administrator's business brief into the foundation the
other agents (Web Builder, Marketer, Ops) build on.

Honesty rules: never invent facts. Prices, item names, quantities, contact
details and claims must come from the brief. If required information is
missing, ask for it instead of guessing.

Required for a complete brief:
- what the business rents/sells, with per-item day rates and available quantities
- business name and contact details (email at minimum)
- target customers / service area
- anything the administrator explicitly wants or forbids

You respond ONLY with a JSON object, no prose around it:
{
  "complete": boolean,
  "questions": string[],            // when complete=false: the specific missing items
  "completedBrief": string,         // when complete=true: the full, normalized brief
  "brandDocument": string,          // when complete=true: markdown per the structure below
  "taskTypes": string[]             // when complete=true: subset of ["CATALOG_SETUP","WEBSITE","MARKETING_PACK","CHECKOUT"]
}

The brand document is markdown with exactly these sections (DESIGN.md sec. 9):
# Business Story (background, mission, strengths, target demographic)
# Logo & Usage
# Typography
# Voice & Tone (writing/grammar guidance)
# Color Palette
# Imagery Dos & Don'ts
# Social Layout Guidance
# Contact Details

Ground every section in the brief. Where the brief leaves brand aesthetics
open, make coherent choices and state them plainly - aesthetics may be chosen,
facts may not.`;

export interface StrategistOutcome {
  status: "AWAITING_BRAND_APPROVAL" | "AWAITING_CLARIFICATION";
  questions?: string[];
  brandDocumentId?: string;
  costMicrodollars: number;
}

interface StrategistJson {
  complete: boolean;
  questions?: string[];
  completedBrief?: string;
  brandDocument?: string;
  taskTypes?: string[];
}

function parseStrategist(content: string): StrategistJson {
  let parsed: unknown;
  try {
    parsed = JSON.parse(content);
  } catch {
    throw new Error("strategist returned non-JSON output");
  }
  const p = parsed as StrategistJson;
  if (typeof p.complete !== "boolean") throw new Error("strategist JSON missing 'complete'");
  if (!p.complete && (!Array.isArray(p.questions) || p.questions.length === 0)) {
    throw new Error("incomplete brief but no questions returned");
  }
  if (p.complete && (!p.completedBrief || !p.brandDocument || !Array.isArray(p.taskTypes))) {
    throw new Error("complete brief but completedBrief/brandDocument/taskTypes missing");
  }
  return p;
}

export async function runStrategist(
  tenantId: string,
  runId: string,
  brief: string,
  clarifications?: string,
): Promise<StrategistOutcome> {
  const userContent = clarifications
    ? `${brief}\n\n--- Administrator clarifications ---\n${clarifications}`
    : brief;

  const result = await modelCall({
    runId,
    agentName: "strategist",
    tier: "sol",
    json: true,
    messages: [
      { role: "system", content: SYSTEM_PROMPT },
      { role: "user", content: userContent },
    ],
  });

  const out = parseStrategist(result.content);

  if (!out.complete) {
    await requestAction({
      tenantId,
      runId,
      agentName: "ops",
      actionType: "business_storage",
      payload: { op: "record_questions", runId, questions: out.questions },
      idempotencyKey: `questions-${runId}-${createHash("sha256").update(JSON.stringify(out.questions)).digest("hex").slice(0, 12)}`,
    });
    return {
      status: "AWAITING_CLARIFICATION",
      questions: out.questions,
      costMicrodollars: result.costMicrodollars,
    };
  }

  // Strategist delegates persistence to Ops (Flow 1 step 5).
  const finalize = await requestAction({
    tenantId,
    runId,
    agentName: "ops",
    actionType: "business_storage",
    payload: {
      op: "finalize_run",
      runId,
      completedBrief: out.completedBrief,
      brandDocument: out.brandDocument,
      taskTypes: out.taskTypes,
    },
    idempotencyKey: `finalize-${runId}`,
  });

  return {
    status: "AWAITING_BRAND_APPROVAL",
    brandDocumentId: finalize.action.provider_reference ?? undefined,
    costMicrodollars: result.costMicrodollars,
  };
}
