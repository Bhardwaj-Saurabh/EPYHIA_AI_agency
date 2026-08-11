"""/model_call - the only path from any agent to a model (DESIGN.md sec. 4).

Capability-checked, budget-capped per run, and every call is cost-logged into
agent_calls whether it succeeds or fails.
"""

from typing import Any

from .capabilities import CAPABILITIES
from .db import pool
from .models_ai import azure_chat, resolve_tier
from .pipeline import GateError


def handle_model_call(req: dict[str, Any]) -> dict[str, Any]:
    agent_name = req.get("agentName")
    run_id = req.get("runId")
    messages = req.get("messages")

    allowed = CAPABILITIES.get(agent_name or "")
    if not allowed or "model_call" not in allowed:
        raise GateError(403, f"agent '{agent_name}' has no capability 'model_call'")
    if not run_id:
        raise GateError(400, "model calls must belong to a run")
    if not isinstance(messages, list) or not messages:
        raise GateError(400, "messages required")

    try:
        tier = resolve_tier(agent_name, req.get("tier"))
    except ValueError as err:
        raise GateError(403, str(err)) from err

    # Budget check against the administrator-approved cap for this run.
    with pool.connection() as conn:
        budget = conn.execute(
            """SELECT r.approved_budget_microdollars AS budget,
                      COALESCE((SELECT SUM(cost_microdollars) FROM agent_calls WHERE run_id = r.id), 0)
                    + COALESCE((SELECT SUM(provider_cost_microdollars) FROM actions WHERE run_id = r.id), 0)
                        AS spent
                 FROM runs r WHERE r.id = %s""",
            (run_id,),
        ).fetchone()
    if budget is None:
        raise GateError(404, f"run {run_id} not found")
    cap, spent = int(budget["budget"]), int(budget["spent"])
    if spent >= cap:
        raise GateError(
            402, f"run {run_id} exhausted its budget (spent {spent} of {cap} microdollars)"
        )

    with pool.connection() as conn:
        agent_call = conn.execute(
            """INSERT INTO agent_calls (run_id, task_id, agent_name, model_id, model_tier, status)
               VALUES (%s, %s, %s, 'pending', %s, 'started') RETURNING id""",
            (run_id, req.get("taskId"), agent_name, tier),
        ).fetchone()
    agent_call_id = agent_call["id"]

    try:
        result = azure_chat(
            tier,
            messages,
            json_mode=bool(req.get("json")),
            max_tokens=req.get("maxTokens"),
        )
    except Exception as err:
        with pool.connection() as conn:
            conn.execute(
                "UPDATE agent_calls SET status = 'failed', completed_at = now() WHERE id = %s",
                (agent_call_id,),
            )
        raise GateError(502, f"model call failed: {err}") from err

    with pool.connection() as conn:
        conn.execute(
            """UPDATE agent_calls
                  SET model_id = %s, input_tokens = %s, cached_input_tokens = %s,
                      output_tokens = %s, cost_microdollars = %s, status = 'completed',
                      completed_at = now()
                WHERE id = %s""",
            (
                result["model_id"],
                result["input_tokens"],
                result["cached_input_tokens"],
                result["output_tokens"],
                result["cost_microdollars"],
                agent_call_id,
            ),
        )

    return {
        "agentCallId": str(agent_call_id),
        "content": result["content"],
        "modelId": result["model_id"],
        "tier": tier,
        "inputTokens": result["input_tokens"],
        "cachedInputTokens": result["cached_input_tokens"],
        "outputTokens": result["output_tokens"],
        "costMicrodollars": result["cost_microdollars"],
        "runSpentMicrodollars": str(spent + result["cost_microdollars"]),
    }
