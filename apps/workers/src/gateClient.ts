// The workers' only reach into the world: HTTP to the Action Gate.
// No provider SDKs, no DB connection, no credentials - capability handles only.
import "./env.js";

const GATE = (process.env.GATE_URL ?? "http://localhost:8082").replace(/\/+$/, "");

export class GateClientError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${GATE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  const body = (await res.json().catch(() => ({}))) as any;
  if (!res.ok && res.status !== 202) {
    throw new GateClientError(res.status, body?.error ?? `gate returned ${res.status}`);
  }
  return body as T;
}

export interface GateAction {
  id: string;
  status: string;
  payload_hash: string;
  provider_reference: string | null;
}

export async function requestAction(input: {
  tenantId: string;
  runId?: string;
  agentName: string;
  actionType: string;
  payload: unknown;
  idempotencyKey: string;
}): Promise<{ action: GateAction; replayed: boolean }> {
  return call("/actions", { method: "POST", body: JSON.stringify(input) });
}

export async function modelCall(input: {
  runId: string;
  taskId?: string;
  agentName: string;
  tier?: string;
  messages: Array<{ role: "system" | "user" | "assistant"; content: string }>;
  json?: boolean;
  maxTokens?: number;
}): Promise<{ content: string; costMicrodollars: number; runSpentMicrodollars: string }> {
  return call("/model_call", { method: "POST", body: JSON.stringify(input) });
}

export async function getRun(runId: string): Promise<{
  run: Record<string, unknown> & { id: string; status: string; original_brief: string };
  tasks: Array<{ task_type: string; status: string; output_ref: string | null }>;
  brandDocument: { id: string; version_number: number; full_text: string } | null;
}> {
  return call(`/runs/${runId}`);
}
