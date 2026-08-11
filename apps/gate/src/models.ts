// Model tiers, pricing, and the Azure OpenAI client (DESIGN.md section 3).
// Prices in microdollars per token == dollars per 1M tokens, so
// cost_microdollars = tokens * price. Cached input assumed at 10% of input.

export type Tier = "sol" | "terra" | "luna";

export const TIER_PRICES: Record<Tier, { input: number; cachedInput: number; output: number }> = {
  sol: { input: 5, cachedInput: 0.5, output: 30 },
  terra: { input: 2, cachedInput: 0.2, output: 12 },
  luna: { input: 0.2, cachedInput: 0.02, output: 1.2 },
};

// The tier each agent is entitled to (DESIGN.md section 3). An agent may
// request a CHEAPER tier, never a more expensive one.
export const AGENT_TIER: Record<string, Tier> = {
  strategist: "sol",
  web_builder: "sol",
  marketer: "terra",
  ops: "luna",
};

const TIER_ORDER: Tier[] = ["luna", "terra", "sol"];

export function resolveTier(agentName: string, requested?: string): Tier {
  const entitled = AGENT_TIER[agentName];
  if (!entitled) throw new Error(`agent '${agentName}' has no model tier configured`);
  if (!requested) return entitled;
  const req = requested as Tier;
  if (!TIER_ORDER.includes(req)) throw new Error(`unknown tier '${requested}'`);
  if (TIER_ORDER.indexOf(req) > TIER_ORDER.indexOf(entitled)) {
    throw new Error(`agent '${agentName}' is limited to tier '${entitled}', requested '${requested}'`);
  }
  return req;
}

function deployment(tier: Tier): string {
  const name = process.env[`AZURE_OPENAI_DEPLOYMENT_${tier.toUpperCase()}`];
  if (!name) throw new Error(`AZURE_OPENAI_DEPLOYMENT_${tier.toUpperCase()} not configured`);
  return name;
}

export interface ChatMessage {
  role: "system" | "user" | "assistant";
  content: string;
}

export interface ChatResult {
  content: string;
  modelId: string;
  inputTokens: number;
  cachedInputTokens: number;
  outputTokens: number;
  costMicrodollars: number;
}

export function costMicrodollars(
  tier: Tier,
  inputTokens: number,
  cachedInputTokens: number,
  outputTokens: number,
): number {
  const p = TIER_PRICES[tier];
  const freshInput = Math.max(0, inputTokens - cachedInputTokens);
  return Math.ceil(
    freshInput * p.input + cachedInputTokens * p.cachedInput + outputTokens * p.output,
  );
}

export async function azureChat(
  tier: Tier,
  messages: ChatMessage[],
  opts?: { json?: boolean; maxTokens?: number },
): Promise<ChatResult> {
  const endpoint = process.env.AZURE_OPENAI_ENDPOINT;
  const apiKey = process.env.AZURE_OPENAI_API_KEY;
  if (!endpoint || !apiKey) throw new Error("Azure OpenAI credentials not configured on the gate");

  let base = endpoint.replace(/\/+$/, "");
  if (!base.endsWith("/openai")) base += "/openai";

  const res = await fetch(`${base}/v1/chat/completions`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "api-key": apiKey },
    body: JSON.stringify({
      model: deployment(tier),
      messages,
      ...(opts?.json ? { response_format: { type: "json_object" } } : {}),
      ...(opts?.maxTokens ? { max_completion_tokens: opts.maxTokens } : {}),
    }),
  });

  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Azure OpenAI ${res.status}: ${text.slice(0, 400)}`);
  }

  const body = (await res.json()) as {
    model?: string;
    choices?: Array<{ message?: { content?: string } }>;
    usage?: {
      prompt_tokens?: number;
      completion_tokens?: number;
      prompt_tokens_details?: { cached_tokens?: number };
    };
  };

  const content = body.choices?.[0]?.message?.content;
  if (typeof content !== "string") throw new Error("Azure OpenAI returned no message content");

  const inputTokens = body.usage?.prompt_tokens ?? 0;
  const cachedInputTokens = body.usage?.prompt_tokens_details?.cached_tokens ?? 0;
  const outputTokens = body.usage?.completion_tokens ?? 0;

  return {
    content,
    modelId: body.model ?? deployment(tier),
    inputTokens,
    cachedInputTokens,
    outputTokens,
    costMicrodollars: costMicrodollars(tier, inputTokens, cachedInputTokens, outputTokens),
  };
}
