// /model_call - the only path from any agent to a model (DESIGN.md sec. 4).
// Capability-checked, budget-capped per run, and every call is cost-logged
// into agent_calls whether it succeeds or fails.
import { pool } from "./db.js";
import { GateError } from "./pipeline.js";
import { CAPABILITIES } from "./capabilities.js";
import { azureChat, resolveTier, type ChatMessage } from "./models.js";

export interface ModelCallRequest {
  runId: string;
  taskId?: string | null;
  agentName: string;
  tier?: string;
  messages: ChatMessage[];
  json?: boolean;
  maxTokens?: number;
}

export interface ModelCallResult {
  agentCallId: string;
  content: string;
  modelId: string;
  tier: string;
  inputTokens: number;
  cachedInputTokens: number;
  outputTokens: number;
  costMicrodollars: number;
  runSpentMicrodollars: string;
}

export async function handleModelCall(req: ModelCallRequest): Promise<ModelCallResult> {
  const allowed = CAPABILITIES[req.agentName];
  if (!allowed || !allowed.includes("model_call")) {
    throw new GateError(403, `agent '${req.agentName}' has no capability 'model_call'`);
  }
  if (!req.runId) throw new GateError(400, "model calls must belong to a run");
  if (!Array.isArray(req.messages) || req.messages.length === 0) {
    throw new GateError(400, "messages required");
  }

  let tier;
  try {
    tier = resolveTier(req.agentName, req.tier);
  } catch (err) {
    throw new GateError(403, err instanceof Error ? err.message : String(err));
  }

  // Budget check against the administrator-approved cap for this run.
  const budget = await pool.query<{ budget: string; spent: string }>(
    `SELECT r.approved_budget_microdollars AS budget,
            COALESCE((SELECT SUM(cost_microdollars) FROM agent_calls WHERE run_id = r.id), 0)
          + COALESCE((SELECT SUM(provider_cost_microdollars) FROM actions WHERE run_id = r.id), 0)
            AS spent
       FROM runs r WHERE r.id = $1`,
    [req.runId],
  );
  if (!budget.rows[0]) throw new GateError(404, `run ${req.runId} not found`);
  const { budget: cap, spent } = budget.rows[0];
  if (BigInt(spent) >= BigInt(cap)) {
    throw new GateError(
      402,
      `run ${req.runId} exhausted its budget (spent ${spent} of ${cap} microdollars)`,
    );
  }

  const inserted = await pool.query<{ id: string }>(
    `INSERT INTO agent_calls (run_id, task_id, agent_name, model_id, model_tier, status)
     VALUES ($1, $2, $3, 'pending', $4, 'started') RETURNING id`,
    [req.runId, req.taskId ?? null, req.agentName, tier],
  );
  const agentCallId = inserted.rows[0]!.id;

  try {
    const result = await azureChat(tier, req.messages, {
      json: req.json,
      maxTokens: req.maxTokens,
    });
    await pool.query(
      `UPDATE agent_calls
          SET model_id = $2, input_tokens = $3, cached_input_tokens = $4,
              output_tokens = $5, cost_microdollars = $6, status = 'completed',
              completed_at = now()
        WHERE id = $1`,
      [
        agentCallId,
        result.modelId,
        result.inputTokens,
        result.cachedInputTokens,
        result.outputTokens,
        result.costMicrodollars,
      ],
    );
    const newSpent = (BigInt(spent) + BigInt(result.costMicrodollars)).toString();
    return {
      agentCallId,
      content: result.content,
      modelId: result.modelId,
      tier,
      inputTokens: result.inputTokens,
      cachedInputTokens: result.cachedInputTokens,
      outputTokens: result.outputTokens,
      costMicrodollars: result.costMicrodollars,
      runSpentMicrodollars: newSpent,
    };
  } catch (err) {
    await pool.query(
      `UPDATE agent_calls SET status = 'failed', completed_at = now() WHERE id = $1`,
      [agentCallId],
    );
    throw new GateError(502, `model call failed: ${err instanceof Error ? err.message : String(err)}`);
  }
}
